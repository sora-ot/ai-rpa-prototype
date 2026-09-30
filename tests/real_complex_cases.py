"""Live configured model decisions on the local complex fixture; then native execution."""
import argparse
import csv
import json
import sys
import time
from openpyxl import Workbook

import _bootstrap
from agent import explore
from complex_cases import SimulatedHumanGate, run_native
from complex_fixture import fixture_server
from settings import resolve_model_config


REQUESTS = {
    't1': '在指定本地商品站采集所有页。每件商品采集列表名称、价格，逐条进入详情页采集库存和 SKU，返回后继续下一件，直到没有下一页。',
    't2': '在指定本地新闻站采集所有页的新闻标题、日期及详情正文。详情会在新标签页打开；若列表日期缺失，填写“未知”。',
    't3': '在指定本地站按关键词 A、B 逐个搜索，分类选择 Research；每个关键词翻页到末页，采集标题和链接，重复链接只保留一条。',
    't4': '在指定本地评论页一直滚动到内容不再增加，跳过广告，采集每条评论的评分、正文和日期。',
    't5': '从指定的 Excel 文件读取 3 个商品网址，逐个采集商品标题和规格，输出 JSON。',
    't6': '在指定本地订单站采集我的订单，登录时暂停让我自行登录。每条订单采集列表编号，并进入详情采集日期和金额；翻页到末页。',
}


def run(case):
    provider, key, model = resolve_model_config({})
    if not key:
        raise RuntimeError('当前 .env 没有配置所选模型的 API Key')
    path = {'t1': '/catalog?page=1', 't2': '/news?page=1', 't3': '/search',
            't4': '/comments', 't5': '/item/1', 't6': '/orders?page=1'}[case]
    with fixture_server() as (base, _server):
        plan = {'start_url': base + path, 'task_name': 'Live ' + case,
                'pagination': 'until_end' if case not in ('t4', 't5') else 'none',
                'requires_detail': case in ('t1', 't2', 't6'),
                'requires_login': case == 't6',
                'keywords': ['A', 'B'] if case == 't3' else [],
                'deduplicate': case == 't3', 'output_format': 'json' if case == 't5' else 'csv',
                'fields': {'t1': ['名称', '价格', '库存', 'SKU'],
                           't2': ['标题', '日期', '正文'],
                           't3': ['标题', '链接'],
                           't4': ['评分', '正文', '日期'],
                           't5': ['标题', '规格'],
                           't6': ['订单编号', '日期', '金额']}[case]}
        excel_path = None
        if case == 't5':
            inputs = _bootstrap.ROOT / 'inputs'
            inputs.mkdir(exist_ok=True)
            excel_path = inputs / f'real_complex_urls_{time.time_ns()}.xlsx'
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(['urlList_0'])
            for number in range(1, 4):
                sheet.append([f'{base}/item/{number}'])
            workbook.save(excel_path)
            plan['input_excel'] = excel_path.name
        events = []
        def progress(message):
            events.append(message)
            print(message, file=sys.stderr, flush=True)
        gate = None
        if case == 't6':
            gate = SimulatedHumanGate(plan['start_url'],
                                      lambda reason: progress('awaiting_login'),
                                      lambda: progress('authenticated'))
        try:
            result = explore(REQUESTS[case], plan, provider, key, model, progress,
                             max_actions=6 if case == 't6' else 12, login_gate=gate)
            rows, output_path, log_path = run_native(result['task'],
                                                     gate.profile_dir if gate else None)
        finally:
            if excel_path:
                excel_path.unlink(missing_ok=True)
        expected = {'t1': 6, 't2': 5, 't3': 7, 't4': 4, 't5': 3, 't6': 6}[case]
        if len(rows) != expected:
            raise AssertionError(f'期望 {expected} 行，实际 {len(rows)}；{log_path}')
        if any(not all(value for value in row.values()) for row in rows):
            raise AssertionError('原版执行器输出了空字段：' + repr(rows[:3]))
        return {'case': case, 'provider': provider, 'model': model,
                'rows': len(rows), 'fields': list(rows[0]),
                'first': rows[0], 'last': rows[-1],
                'output': str(output_path), 'log': str(log_path),
                'events': events[-8:]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('case', choices=tuple(REQUESTS))
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(run(parser.parse_args().case), ensure_ascii=False, indent=2))
