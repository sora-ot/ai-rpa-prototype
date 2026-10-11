"""Replay a public list-only news task and compare native CSV with current DOM fields."""
import argparse
import csv
import json
from pathlib import Path

from _bootstrap import ROOT
import agent
from run_native import resolve_profile, run
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def main(task_file):
    path = Path(task_file).resolve()
    task = json.loads(path.read_text(encoding='utf-8'))
    if any(n['option'] not in (0, 1, 3, 8) for n in task['graph']):
        raise ValueError('此核对脚本只支持无需输入或点击的公开新闻列表任务')
    loops = [n for n in task['graph'] if n['option'] == 8]
    if len(loops) != 1 or loops[0]['parameters'].get('loopType') != 1:
        raise ValueError('需要单个原生列表循环')
    profile = resolve_profile(task)
    result = run(path, str(profile), timeout=120)
    with Path(result['output']).open(encoding='utf-8-sig', newline='') as stream:
        output_rows = list(csv.DictReader(stream))
    chrome = ROOT.parent / 'EasySpider/ElectronJS/chrome_win64'
    options = webdriver.ChromeOptions()
    options.binary_location = str(chrome / 'chrome.exe')
    for value in ('--headless=new', '--window-size=1366,900', f'--user-data-dir={profile}'):
        options.add_argument(value)
    source_rows = []
    with webdriver.Chrome(service=Service(str(chrome / 'chromedriver_win64.exe')), options=options) as driver:
        driver.set_page_load_timeout(35)
        driver.get(task['url'])
        loop = loops[0]['parameters']
        if loop.get('waitElement'):
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.XPATH, loop['waitElement'])))
        fields = next(n['parameters']['params'] for n in task['graph'] if n['option'] == 3)
        for row in driver.find_elements(By.XPATH, loop['xpath']):
            values = {}
            for field in fields:
                relative = field['relativeXPath']
                matches = row.find_elements(By.XPATH, '.' + relative) if relative else [row]
                element = matches[0] if matches else None
                if field['nodeType'] == 2:
                    value = element.get_attribute('href') if element else ''
                elif field['contentType'] == 9 and field['JS'] == 'return arguments[0].getAttribute("title") || "";':
                    value = element.get_attribute('title') if element else ''
                elif field['nodeType'] == 0:
                    value = element.text if element else ''
                else:
                    raise ValueError('此脚本仅核对新闻文本和链接')
                values[field['name']] = value
            source_rows.append(values)
    result.update({'source_rows': len(source_rows), 'exact_source_match': output_rows == source_rows,
                   'source_samples': source_rows[:2]})
    report = ROOT / 'generated' / (path.stem + '_native_check.json')
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    assert output_rows and result['nonempty_rows'] == len(output_rows), 'Incomplete native records'
    assert output_rows == source_rows, 'Native output differs from current source (check for news updates)'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_file')
    main(parser.parse_args().task_file)
