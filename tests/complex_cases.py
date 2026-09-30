"""Run T1/T2/T6 through the AI tool boundary and native EasySpider executor."""
import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from openpyxl import Workbook

import _bootstrap
import auth
from agent import DETAIL_SYSTEM, explore
from auth import LoginGate, detect_challenge
from observer import observe
from complex_fixture import fixture_server
from executor_smoke import EXECUTOR, SANDBOX


class DeterministicModel:
    def __init__(self, case):
        self.case = case
        self.calls = 0

    def __call__(self, provider, key, model, system, user, screenshot=None):
        assert 'local-only' not in user, 'login password leaked to model'
        self.calls += 1
        payload = json.loads(user)
        snap = payload['observation']
        if self.case == 't3' and '/search' in snap['url']:
            if self.calls == 1:
                field = next(e for e in snap['elements'] if e['tag'] == 'input' and e['placeholder'] == 'Keyword')
                return {'action': 'input', 'candidate_id': field['id'], 'value': 'A'}
            if self.calls == 2:
                select = next(e for e in snap['elements'] if e['tag'] == 'select')
                return {'action': 'select', 'candidate_id': select['id'], 'value': 'Research'}
            button = next(e for e in snap['elements'] if e['tag'] == 'button' and e['text'] == 'Search')
            return {'action': 'click', 'candidate_id': button['id']}
        if self.case == 't4' and self.calls == 1:
            return {'action': 'scroll_until_end'}
        if system == DETAIL_SYSTEM:
            detail_fields = {
                't1': [('stock', 'span', 'Stock'), ('sku', 'code', 'SKU-')],
                't2': [('body', 'p', 'Full body')],
                't6': [('date', 'span', '2026-09'), ('amount', 'span', '.00')],
            }[self.case]
            return {'fields': [self.field(snap, name, tag, text) for name, tag, text in detail_fields]}
        if self.case == 't5':
            fields = [self.field(snap, 'title', 'h1', 'Product'),
                      self.field(snap, 'spec', 'span', 'Spec')]
            return {'action': 'extract', 'mode': 'single', 'fields': fields, 'next_id': None}
        prefix = {'t1': 'Item ', 't2': 'News ', 't3': 'Common',
                  't4': '5', 't6': 'Order '}[self.case]
        listing = next(item for item in snap['lists'] if item['xpath'].endswith(
            {'t1': 'article[contains(concat(" ",normalize-space(@class)," ")," product ")]',
             't2': 'article[contains(concat(" ",normalize-space(@class)," ")," news ")]',
             't3': 'article[contains(concat(" ",normalize-space(@class)," ")," result ")]',
             't4': 'article[contains(concat(" ",normalize-space(@class)," ")," comment ")]',
             't6': 'article[contains(concat(" ",normalize-space(@class)," ")," order ")]'}[self.case])
            and item['samples'][0].startswith(prefix))
        first = listing['first_xpath'] + '/'
        elements = [e for e in snap['elements'] if e['absolute_xpath'].startswith(first)]
        if self.case == 't4':
            fields = [{'name': 'score', 'candidate_id': next(e for e in elements if e['tag'] == 'span')['id'],
                       'attribute': 'text'},
                      {'name': 'comment', 'candidate_id': next(e for e in elements if e['tag'] == 'p')['id'],
                       'attribute': 'text'},
                      {'name': 'date', 'candidate_id': next(e for e in elements if e['tag'] == 'time')['id'],
                       'attribute': 'text'}]
            return {'action': 'extract', 'mode': 'list', 'list_id': listing['id'],
                    'fields': fields, 'next_id': None}
        title = next(e for e in elements if e['tag'] == 'h2')
        link = next(e for e in elements if e['tag'] == 'a' and e['href'])
        fields = [{'name': 'title', 'candidate_id': title['id'], 'attribute': 'text'}]
        if self.case == 't1':
            price = next(e for e in elements if e['tag'] == 'span' and '.00' in e['text'])
            fields.append({'name': 'price', 'candidate_id': price['id'], 'attribute': 'text'})
        elif self.case == 't2':
            date = next(e for e in elements if e['tag'] == 'time')
            fields.append({'name': 'published', 'candidate_id': date['id'], 'attribute': 'text',
                           'default': '未知'})
        elif self.case == 't3':
            fields.append({'name': 'link', 'candidate_id': link['id'], 'attribute': 'href'})
        next_id = snap['next_candidates'][0]['id'] if snap['next_candidates'] else None
        return {'action': 'extract', 'mode': 'list', 'list_id': listing['id'],
                'fields': fields, 'detail_id': link['id'] if self.case in ('t1', 't2', 't6') else None,
                'next_id': next_id}

    @staticmethod
    def field(snap, name, tag, text):
        element = next(e for e in snap['elements'] if e['tag'] == tag and text in e['text'])
        return {'name': name, 'candidate_id': element['id'], 'attribute': 'text'}


class SimulatedHumanGate(LoginGate):
    """Only the local test acts as the human; production LoginGate waits for the UI."""
    def wait_for_login(self, driver, start_url, reason):
        from selenium.webdriver.common.by import By
        self.used = True
        self.on_wait(reason)
        assert detect_challenge(driver, start_url)
        snapshot = observe(driver)
        assert 'local-only' not in json.dumps(snapshot)
        assert all(e['type'] != 'password' for e in snapshot['elements'])
        driver.find_element(By.NAME, 'username').send_keys('demo')
        password = driver.find_element(By.NAME, 'password')
        password.clear()
        password.send_keys('local-only')
        driver.find_element(By.CSS_SELECTOR, 'button[type=submit]').click()
        driver.get(start_url)
        assert not detect_challenge(driver, start_url)
        self.on_resume()


def run_native(task, profile_dir=None, timeout=210, task_id=None, output_name=None):
    if sys.platform != 'win32' or not EXECUTOR.is_file() or not (
            SANDBOX / 'EasySpider' / 'resources' / 'app' / 'chrome_win64').is_dir():
        raise RuntimeError('需要 Windows 原版打包执行器和 README 中的隔离资源目录连接')
    task_id = task_id or time.time_ns() // 1000000
    task = {**task, 'id': task_id, 'saveName': f'complex_{task_id}', 'recordLog': True}
    instances = SANDBOX / 'execution_instances'
    instances.mkdir(parents=True, exist_ok=True)
    (instances / f'{task_id}.json').write_text(json.dumps(task, ensure_ascii=False), encoding='utf-8')
    (SANDBOX / 'config.json').write_text(json.dumps({
        'webserver_address': 'http://127.0.0.1', 'webserver_port': 8074,
        'user_data_folder': './user_data', 'debug': False,
        'mysql_config_path': './mysql_config.json',
        'absolute_user_data_folder': str(profile_dir or SANDBOX / 'unused_profile')},
        ensure_ascii=False), encoding='utf-8')
    command = [str(EXECUTOR), '--ids', f'[{task_id}]', '--read_type', 'local',
               '--headless', '1', '--keyboard', '0', '--user_data', '1' if profile_dir else '0']
    log_path = SANDBOX / f'complex_{task_id}.log'
    with log_path.open('wb') as log:
        process = subprocess.Popen(command, cwd=SANDBOX, stdout=log, stderr=subprocess.STDOUT,
                                   env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
            raise RuntimeError(f'原版执行器超时：{log_path}')
    extension = '.json' if task['outputFormat'] == 'json' else '.csv'
    output_dir = SANDBOX / 'Data' / f'Task_{task_id}'
    output_files = ([output_dir / (output_name + extension)] if output_name else
                    list(output_dir.glob('*' + extension)))
    if process.returncode or len(output_files) != 1 or not output_files[0].is_file():
        tail = log_path.read_bytes().decode('utf-8', 'replace')[-6000:]
        raise RuntimeError(f'原版执行器失败，退出码 {process.returncode}：{tail}')
    if extension == '.json':
        rows = json.loads(output_files[0].read_text(encoding='utf-8-sig'))
    else:
        with output_files[0].open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
    return rows, output_files[0], log_path


def run(case, fault=None, recover=False, page_limit=None):
    profile_test_root = _bootstrap.ROOT / 'profiles'
    profile_test_root.mkdir(exist_ok=True)
    with fixture_server() as (base, fixture), tempfile.TemporaryDirectory(dir=profile_test_root) as temp:
        if fault == 'temporary_503' and case == 't5':
            fixture.fail_once_items.add('2')
        elif fault == 'persistent_503' and case == 't5':
            fixture.always_fail_items.add('2')
        elif fault == 'session_expires' and case == 't6':
            pass  # Arm the fault after exploration so only native execution expires.
        elif fault == 'detail_login' and case == 't1':
            fixture.detail_requires_login = True
        elif fault:
            raise ValueError('Unsupported fault for case: ' + str(fault))
        path = {'t1': '/catalog?page=1', 't2': '/news?page=1',
                't3': '/search', 't4': '/comments', 't5': '/item/1',
                't6': '/orders?page=1'}[case]
        model = DeterministicModel(case)
        plan = {'start_url': base + path, 'task_name': 'Complex ' + case,
                'pagination': 'page_limit' if page_limit else
                               ('until_end' if case in ('t1', 't2', 't3', 't6') else 'none'),
                'page_limit': page_limit,
                'requires_detail': case in ('t1', 't2', 't6'),
                'requires_login': case == 't6', 'fields': [],
                'keywords': ['A', 'B'] if case == 't3' else [],
                'deduplicate': case == 't3',
                'output_format': 'json' if case == 't5' else 'csv'}
        excel_path = None
        if case == 't5':
            inputs = _bootstrap.ROOT / 'inputs'
            inputs.mkdir(exist_ok=True)
            excel_path = inputs / f'complex_urls_{time.time_ns()}.xlsx'
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(['urlList_0'])
            for number in range(1, 4):
                sheet.append([f'{base}/item/{number}'])
            workbook.save(excel_path)
            plan['input_excel'] = excel_path.name
        gate = None
        events = []
        if case == 't6' or fault == 'detail_login':
            with patch.object(auth, 'PROFILE_ROOT', Path(temp)):
                gate = SimulatedHumanGate(plan['start_url'],
                    lambda reason: events.append('awaiting_login'),
                    lambda: events.append('authenticated'))
                with patch('agent.ask_json', side_effect=model):
                    result = explore(case, plan, 'deepseek', 'stub', '', events.append,
                                     login_gate=gate)
        else:
            with patch('agent.ask_json', side_effect=model):
                result = explore(case, plan, 'deepseek', 'stub', '', events.append)
        task = result['task']
        if fault == 'session_expires':
            fixture.expire_orders_page2 = True
        try:
            rows, csv_path, log_path = run_native(task, gate.profile_dir if gate else None)
        finally:
            if excel_path:
                excel_path.unlink(missing_ok=True)
        if recover:
            if fault != 'session_expires' or case != 't6':
                raise ValueError('Recovery test only supports T6 session expiry')
            first_rows = rows
            fixture.expire_orders_page2 = False
            with patch.object(auth, 'PROFILE_ROOT', Path(temp)):
                with patch('agent.ask_json', side_effect=DeterministicModel(case)):
                    explore(case, plan, 'deepseek', 'stub', '', events.append,
                            login_gate=gate)
            resumed = {**task, 'startFromExit': 0, 'dataWriteMode': 3}
            same_id = int(csv_path.parent.name.removeprefix('Task_'))
            rows, csv_path, log_path = run_native(resumed, gate.profile_dir,
                                                  task_id=same_id,
                                                  output_name=f'complex_{same_id}_2')
            assert len(first_rows) == 3, first_rows
            assert rows == [{'title': f'Order {number}',
                             'date': f'2026-09-{number:02d}',
                             'amount': f'{number * 7}.00'}
                            for number in range(1, 7)], rows
            return {'case': case, 'fault': fault, 'before_recovery': first_rows,
                    'after_recovery': rows, 'login_events': [event for event in events
                        if event in ('awaiting_login', 'authenticated')],
                    'log': str(log_path)}
        if fault:
            if fault == 'temporary_503':
                assert rows == [{'title': f'Product {number}', 'spec': f'Spec {number}'}
                                for number in range(1, 4)], rows
            elif fault == 'persistent_503':
                assert len(rows) == 3 and rows[1]['title'] == base + '/item/2', rows
                assert rows[1]['spec'].startswith('ERROR:'), rows
            elif fault == 'session_expires':
                assert len(rows) == 3 and fixture.expired_orders, rows
            elif fault == 'detail_login':
                assert len(rows) == 6 and gate.used, rows
            return {'case': case, 'fault': fault, 'rows': rows,
                    'csv': str(csv_path), 'log': str(log_path),
                    'triggered': ('2' in fixture.failed_items or '2' in fixture.always_fail_items)
                    if case == 't5' else (fixture.expired_orders if case == 't6' else gate.used),
                    'login_events': [event for event in events
                                     if event in ('awaiting_login', 'authenticated')]}
        expected = {'t1': 6, 't2': 5, 't3': 7, 't4': 4, 't5': 3, 't6': 6}[case]
        if len(rows) != expected:
            raise AssertionError(f'{case}: expected {expected} rows, got {len(rows)}; {csv_path}; {log_path}')
        for number, row in enumerate(rows, 1):
            if case == 't1':
                assert row == {'title': f'Item {number}', 'price': f'{number*10}.00',
                               'stock': f'Stock {number}', 'sku': f'SKU-{number}'}, row
            elif case == 't2':
                assert row == {'title': f'News {number}',
                               'published': '未知' if number == 4 else f'2026-09-{number:02d}',
                               'body': f'Full body for News {number}'}, row
            elif case == 't3':
                name = ['Common', 'A-1', 'A-2', 'A-3', 'B-1', 'B-2', 'B-3'][number - 1]
                assert row == {'title': name, 'link': f'{base}/item/{name}'}, row
            elif case == 't4':
                assert row == {'score': str((6-number) if number <= 2 else (7-number)),
                               'comment': f'Comment {number}',
                               'date': f'2026-09-{number:02d}'}, row
            elif case == 't5':
                assert row == {'title': f'Product {number}', 'spec': f'Spec {number}'}, row
            else:
                assert row == {'title': f'Order {number}', 'date': f'2026-09-{number:02d}',
                               'amount': f'{number*7}.00'}, row
        if case == 't6':
            assert events.count('awaiting_login') == 1 and events.count('authenticated') == 1
            assert result['profile_dir'] == str(gate.profile_dir)
            assert 'demo_session' not in json.dumps(task)
            assert 'local-only' not in json.dumps(task)
        return {'case': case, 'rows': len(rows), 'model_calls': model.calls,
                'nodes': [node['option'] for node in task['graph']],
                'first': rows[0], 'last': rows[-1], 'csv': str(csv_path), 'log': str(log_path),
                'login_events': [event for event in events if event in ('awaiting_login', 'authenticated')]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('case', choices=('t1', 't2', 't3', 't4', 't5', 't6'))
    parser.add_argument('--fault', choices=('temporary_503', 'persistent_503',
                                            'session_expires', 'detail_login'))
    parser.add_argument('--recover', action='store_true')
    parser.add_argument('--page-limit', type=int)
    sys.stdout.reconfigure(encoding='utf-8')
    args = parser.parse_args()
    print(json.dumps(run(args.case, args.fault, args.recover, args.page_limit),
                     ensure_ascii=False, indent=2))
