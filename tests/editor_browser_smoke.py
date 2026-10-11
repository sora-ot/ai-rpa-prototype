"""Original Electron design/save/debug -> prototype AI edit -> same task.

python tests/editor_browser_smoke.py [--real-model] [--require-discovery]
Local product fixture and test-owned tasks/profile only. Requires Windows native
Electron assets and free 8084. Does not touch a user's EasySpider instance.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from contextlib import nullcontext
from unittest.mock import patch

from _bootstrap import ROOT
from test_editor import example_session
from native_bridge import NativeClient, _driver_candidates
import editor
import server
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        keyword = query.get('q', ['羽毛球拍'])[0]
        html = '<form><input id="q" name="q" placeholder="关键词"><button id="search">搜索</button></form>'
        html += '<button id="sales" onclick="document.body.dataset.sorted=\'sales\'">销量</button>'
        for i in (1, 2):
            html += f'<article class="card"><div class="title">{keyword}{i}</div><div class="price">19.50</div><div class="sales">99人付款</div><div class="shop">店铺{i}</div><div class="rating">评分4.5</div></article>'
        html += '<a rel="next" href="?page=2">下一页</a>'
        body = ('<!doctype html><meta charset="utf-8">' + html).encode('utf-8')
        self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_args):
        pass


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); return listener.getsockname()[1]


def eventually(fn, timeout=30):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            answer = fn()
            if answer: return answer
        except Exception as exc:
            last = exc
        time.sleep(.1)
    raise AssertionError(f'Fixture timeout: {last}')


class FixtureDirectory(tempfile.TemporaryDirectory):
    def cleanup(self):
        if not Path(self.name).resolve().is_relative_to((ROOT / 'browser_tmp').resolve()):
            raise RuntimeError('Test directory escaped the prototype workspace')
        # Electron renderers can release their directory handles just after exit.
        for attempt in range(30):
            try:
                super().cleanup(); return
            except PermissionError:
                if attempt == 29: raise
                time.sleep(.1)


def run(real_model=False, require_discovery=False):
    with socket.socket() as listener:
        try: listener.bind(('127.0.0.1', 8084))
        except OSError: raise RuntimeError('8084 正被占用，请先关闭原版 EasySpider 再运行隔离测试') from None
    source = ROOT.parent / 'EasySpider' / 'ElectronJS'
    executable = source / 'node_modules' / 'electron' / 'dist' / 'electron.exe'
    if not executable.is_file(): raise RuntimeError('此测试需要 Windows 原版 Electron 安装目录')
    (ROOT / 'browser_tmp').mkdir(exist_ok=True)
    site = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    web = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    for http in (site, web): threading.Thread(target=http.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{site.server_port}/'
    sid = f'native-ui-{site.server_port}'
    state = server.SESSIONS[sid] = example_session(url)
    process = attached = None
    with FixtureDirectory(prefix='native-designer-', dir=ROOT/'browser_tmp') as temporary:
        sandbox = Path(temporary)
        folder = sandbox / 'profile'
        state['result'].update({'profile_dir':str(folder), 'profile_relative_source':os.path.relpath(folder, source)})
        storage_port, control_port = free_port(), free_port()
        client = NativeClient(f'http://127.0.0.1:{storage_port}')
        def control(action, script=None):
            payload = json.dumps({'script':script}).encode('utf-8')
            try:
                with urlopen(Request(f'http://127.0.0.1:{control_port}/{action}', data=payload,
                                     headers={'Content-Type':'application/json'}), timeout=15) as response:
                    return json.load(response).get('value')
            except HTTPError as exc:
                raise RuntimeError(exc.read().decode('utf-8')) from None
        with (sandbox/'electron.log').open('w', encoding='utf-8') as log:
            try:
                child_env = os.environ.copy()
                child_env.pop('ELECTRON_RUN_AS_NODE', None)
                process = subprocess.Popen([str(executable), '--no-sandbox',
                    str(Path(__file__).with_name('native_designer_fixture.js')), str(source),
                    str(sandbox), str(storage_port), str(control_port), str(folder)],
                    stdout=log, stderr=log, env=child_env,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                eventually(lambda: control('ready'))
                control('start')
                driver_info = sandbox/'driver.json'
                address = eventually(lambda: json.loads(driver_info.read_text())['address'] if driver_info.exists() else None)
                address = address.rstrip('/')
                if require_discovery:
                    discovered = _driver_candidates()
                    assert address in [value.rstrip('/') for value in discovered], '原版 ChromeDriver 进程发现失败'
                discovery = nullcontext() if require_discovery else patch('native_bridge._driver_candidates', return_value=[address])
                suggestion = {'summary':'页数改为5，价格改名','questions':[], 'operations':[
                    {'op':'set_page_limit','page_limit':5}, {'op':'rename_field','old_name':'价格','name':'商品价格'}]}
                model = nullcontext() if real_model else patch('server.propose_changes', return_value=suggestion)
                with patch('server.native_client', return_value=client), \
                     patch('server.profile_path', return_value=folder), \
                     discovery, model:
                    attached = server.native_designer(sid, state)
                    eventually(lambda: attached.connect() and '/taskGrid/taskList' in attached.driver.current_url, timeout=60)
                    options = webdriver.ChromeOptions(); options.binary_location = str(source/'chrome_win64'/'chrome.exe')
                    options.add_argument('--headless=new'); options.add_argument('--window-size=1400,1100')
                    options.add_argument('--no-sandbox'); options.add_argument('--disable-gpu')
                    with webdriver.Chrome(service=Service(str(source/'chrome_win64'/'chromedriver_win64.exe')), options=options) as browser:
                        wait = WebDriverWait(browser, 30)
                        def click(element_id):
                            element = browser.find_element(By.ID, element_id)
                            browser.execute_script("arguments[0].scrollIntoView({block:'center'})", element)
                            wait.until(lambda _: element.is_enabled()); element.click()
                        browser.get(f'http://127.0.0.1:{web.server_port}/')
                        browser.execute_script("sessionStorage.setItem('ai_rpa_session',arguments[0]);session=arguments[0]", sid)
                        if not real_model: browser.find_element(By.ID,'api-key').send_keys('fixture')
                        click('enter-chat'); wait.until(lambda _: browser.find_element(By.ID,'workflow-card').is_displayed())
                        assert not browser.find_elements(By.ID, 'node-editor')
                        click('native-open'); wait.until(lambda _: state.get('native',{}).get('active'))
                        task_id = state['imported_id']
                        eventually(lambda: control('flow', "typeof nodeList !== 'undefined' && Number(document.querySelector('#serviceId').value) === " + str(task_id)))
                        control('flow', "nodeList[2].parameters.value='网球拍';refresh();saveService(0);true")
                        eventually(lambda: client.read(task_id)['graph'][2]['parameters']['value'] == '网球拍')
                        click('native-read'); wait.until(lambda _: state['result']['task']['graph'][2]['parameters']['value'] == '网球拍')
                        control('flow', "vueData.nowNodeIndex=2;handleElement();trailElement(nodeList[2],1);true")
                        eventually(lambda: attached.driver.find_element(By.ID,'q').get_attribute('value') == '网球拍')
                        before = copy.deepcopy(client.read(task_id))
                        browser.find_element(By.ID,'edit-message').send_keys('只将采集页数从3页改为5页，把价格字段改名为商品价格，其他流程保持不变。')
                        click('propose-edit')
                        WebDriverWait(browser,180).until(lambda _: (state.get('pending_edit') or
                            browser.find_element(By.ID,'error').text) and not browser.execute_script('return busy'))
                        assert state.get('pending_edit'), browser.find_element(By.ID,'error').text
                        assert not state['pending_edit']['questions'], state['pending_edit']['summary']
                        assert client.read(task_id) == before
                        click('confirm-edit'); wait.until(lambda _: editor.pagination_info(client.read(task_id))['page_limit'] == 5)
                        eventually(lambda: control('flow', "typeof nodeList !== 'undefined' && nodeList[2].parameters.value==='网球拍' && nodeList.some(n=>n.option===8 && n.parameters.exitCount===4)"))
                        saved = client.read(task_id)
                        assert saved['id'] == task_id and saved['graph'][2]['parameters']['value'] == '网球拍'
                        assert any(f['name']=='商品价格' for f in saved['outputParameters'])
                        # Bind a real DOM candidate through the native connection, including
                        # field copies on both sides of pagination. The selector is never mocked.
                        def add_rating(task, _message, _provider, _key, _model, _history, observation, _selected):
                            rating = next(item for item in observation['elements'] if
                                item.get('text') == '评分4.5' and item.get('tag') == 'div')
                            extract = next(node for node in task['graph'] if node['option'] == 3)
                            return {'summary':'新增数字评分字段','questions':[], 'operations':[{
                                'op':'upsert_field', 'node_id':extract['index'], 'name':'评分数值',
                                'attribute':'number', 'candidate_id':rating['id']}]}
                        message = browser.find_element(By.ID,'edit-message'); message.clear()
                        message.send_keys('新增数字评分字段，保留其他采集设置')
                        with patch('server.propose_changes', side_effect=add_rating):
                            click('propose-edit')
                            wait.until(lambda _: not browser.execute_script('return busy'))
                            assert state.get('pending_edit'), browser.find_element(By.ID,'error').text
                            click('confirm-edit')
                            wait.until(lambda _: not browser.execute_script('return busy'))
                        assert not browser.find_element(By.ID,'error').text
                        saved = client.read(task_id)
                        extracts = [node for node in saved['graph'] if node['option'] == 3]
                        assert all(any(f['name']=='评分数值' for f in node['parameters']['params']) for node in extracts)
                        first = extracts[0]; loop = saved['graph'][first['parentId']]
                        field = next(f for f in first['parameters']['params'] if f['name']=='评分数值')
                        record = attached.driver.find_elements(By.XPATH, loop['parameters']['xpath'])[0]
                        assert record.find_element(By.XPATH, '.' + field['relativeXPath']).text == '评分4.5'
                        click('native-observe'); wait.until(lambda _: '元素候选' in browser.find_element(By.ID,'native-page').text)
                        click('native-open'); wait.until(lambda _: not browser.execute_script('return busy'))
                        assert state['imported_id'] == task_id and len(list((sandbox/'tasks').glob('*.json'))) == 1
                        browser.refresh(); click('enter-chat')
                        wait.until(lambda _: str(task_id) in browser.find_element(By.ID,'native-status').text)
                        assert not browser.find_element(By.ID,'error').text
                        print('Original Electron UI passed: native task open, real manual save, native node debug, latest graph read, confirmed AI edit, same-ID write/refresh, real DOM candidate field binding, UI reload')
            except Exception:
                log.flush(); print((sandbox/'electron.log').read_text(encoding='utf-8', errors='replace')[-7000:])
                raise
            finally:
                if process and process.poll() is None:
                    try: control('exit'); process.wait(timeout=15)
                    except Exception:
                        if attached and attached.driver:
                            try: attached.driver.execute('quit', {})
                            except Exception: pass
                        process.kill(); process.wait(timeout=5)
                server.NATIVE_DESIGNERS.pop(sid,None)
                server.SESSIONS.pop(sid,None)
                for http in (site, web): http.shutdown(); http.server_close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(); parser.add_argument('--real-model', action='store_true')
    parser.add_argument('--require-discovery', action='store_true')
    args = parser.parse_args(); run(args.real_model, args.require_discovery)
