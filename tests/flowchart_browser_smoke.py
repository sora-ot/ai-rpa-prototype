"""Read-only preview, AI confirmation and native saved-graph refresh in real Chrome.

python tests/flowchart_browser_smoke.py
Native storage runs on an ephemeral port with test-owned tasks. No original
EasySpider instance or logged-in profile is opened; model responses are fixtures.
"""
import copy
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from _bootstrap import ROOT
from test_editor import example_session
from native_bridge import NativeClient
import editor
import server
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait


def run():
    source = ROOT.parent / 'EasySpider' / 'ElectronJS'
    (ROOT / 'browser_tmp').mkdir(exist_ok=True)
    screenshot = ROOT / 'browser_tmp' / 'flowchart_preview.png'
    web = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    sid = 'readonly-graph-fixture'
    state = server.SESSIONS[sid] = example_session()
    state['result']['profile_relative_source'] = '../../ai-rpa-prototype/profiles/local_fixture'
    process = None
    with tempfile.TemporaryDirectory(prefix='readonly-graph-', dir=ROOT / 'browser_tmp') as temporary:
        folder = Path(temporary)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        client = NativeClient(f'http://127.0.0.1:{port}')
        with (folder / 'storage.log').open('w', encoding='utf-8') as log:
            try:
                process = subprocess.Popen(['node', str(Path(__file__).with_name('native_storage_fixture.js')),
                    str(source), str(folder), str(port)], stdin=subprocess.PIPE, stdout=log, stderr=log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                for _ in range(100):
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2): break
                    except OSError: time.sleep(.05)
                    if process.poll() is not None: raise RuntimeError('Native storage fixture failed')
                else: raise RuntimeError('Native storage startup timeout')
                suggestion = {'summary':'页数改为5并更新字段名','questions':[], 'operations':[
                    {'op':'set_page_limit','page_limit':5}, {'op':'rename_field','old_name':'价格','name':'商品价格'}]}
                options = webdriver.ChromeOptions()
                options.binary_location = str(source / 'chrome_win64' / 'chrome.exe')
                for argument in ('--headless=new', '--no-sandbox', '--disable-gpu', '--window-size=1440,1180'):
                    options.add_argument(argument)
                with patch('server.native_client', return_value=client), \
                     patch('server.NativeDesigner.connect', return_value=False), \
                     patch('server.propose_changes', return_value=suggestion), \
                     webdriver.Chrome(service=Service(str(source / 'chrome_win64' / 'chromedriver_win64.exe')),
                                      options=options) as browser:
                    wait = WebDriverWait(browser, 20)
                    def click(element_id):
                        button = browser.find_element(By.ID, element_id)
                        browser.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
                        wait.until(lambda _: button.is_enabled()); button.click()
                        wait.until(lambda _: not browser.execute_script('return busy'))
                        assert not browser.find_element(By.ID,'error').text, browser.find_element(By.ID,'error').text
                    def node(index):
                        return browser.find_element(By.CSS_SELECTOR, f'.wf-node[data-node-id="{index}"]')
                    browser.get(f'http://127.0.0.1:{web.server_port}/')
                    browser.execute_script("sessionStorage.setItem('ai_rpa_session',arguments[0]);session=arguments[0]", sid)
                    browser.find_element(By.ID,'api-key').send_keys('fixture-key')
                    click('enter-chat')
                    wait.until(lambda _: len(browser.find_elements(By.CSS_SELECTOR,'.wf-node')) == 12)
                    wait.until(lambda _: browser.execute_script("return document.querySelector('#workflow-links path[marker-end]') !== null"))
                    assert '最多 3 页' in browser.find_element(By.ID,'workflow-summary').text
                    assert len(browser.find_elements(By.CSS_SELECTOR,'.wf-unit-loop')) == 3
                    assert len(browser.find_elements(By.CSS_SELECTOR,'.wf-unit-judge')) == 1
                    assert not browser.find_elements(By.CSS_SELECTOR,
                        '#workflow-preview input, #workflow-preview textarea, #workflow-preview select, '
                        '#workflow-preview [contenteditable="true"], #workflow-preview [draggable="true"]')
                    before = copy.deepcopy(state['result']['task']); version = state['edit_version']
                    node(2).click()
                    assert '羽毛球拍' in browser.find_element(By.ID,'workflow-inspector').text
                    click('workflow-zoom-out'); click('workflow-fit')
                    assert state['result']['task'] == before and state['edit_version'] == version
                    assert not list((folder / 'tasks').glob('*.json'))
                    browser.find_element(By.ID,'edit-message').send_keys('将页数改为5页，价格字段改名为商品价格')
                    click('propose-edit')
                    assert '最多 3 页' in browser.find_element(By.ID,'workflow-summary').text
                    assert state['result']['task'] == before
                    click('confirm-edit')
                    assert '最多 5 页' in browser.find_element(By.ID,'workflow-summary').text
                    assert '最多采集 5 页' in node(7).text
                    assert '商品价格' in node(6).text
                    click('native-open')
                    task_id = state['imported_id']
                    assert editor.pagination_info(client.read(task_id))['page_limit'] == 5
                    assert len(list((folder / 'tasks').glob('*.json'))) == 1
                    manual = client.read(task_id)
                    manual['graph'][2]['parameters']['value'] = '网球拍'
                    # Native manual edits can add a second condition branch/custom node.
                    branch_id = len(manual['graph']); custom_id = branch_id + 1
                    manual['graph'].append({'index':branch_id, 'id':branch_id, 'parentId':8,
                        'option':10, 'title':'没有下一页', 'sequence':[custom_id], 'parameters':{'class':0, 'value':'', 'wait':0}})
                    manual['graph'].append({'index':custom_id, 'id':custom_id, 'parentId':branch_id,
                        'option':5, 'title':'记录结束状态', 'sequence':[], 'parameters':{
                            'codeMode':0, 'code':"return '结束';", 'wait':0, 'paraType':'text', 'recordASField':0}})
                    manual['graph'][8]['sequence'].append(branch_id)
                    client.write(manual, task_id)
                    click('native-read')
                    assert len(browser.find_elements(By.CSS_SELECTOR,'.wf-node')) == 14
                    assert len(browser.find_elements(By.CSS_SELECTOR,
                        '.wf-unit-judge > .wf-branches > .wf-unit-branch')) == 2
                    assert '输入：网球拍' in node(2).text
                    assert '记录结束状态' in browser.find_element(By.ID,'workflow-nodes').text
                    suggestion.update({'summary':'页数改为7页', 'operations':[{'op':'set_page_limit','page_limit':7}]})
                    message = browser.find_element(By.ID,'edit-message'); message.clear(); message.send_keys('改为7页')
                    click('propose-edit')
                    assert '最多 5 页' in browser.find_element(By.ID,'workflow-summary').text
                    click('confirm-edit')
                    assert '最多 7 页' in browser.find_element(By.ID,'workflow-summary').text
                    assert '输入：网球拍' in node(2).text
                    assert editor.pagination_info(client.read(task_id))['page_limit'] == 7
                    assert any(n['option'] == 5 for n in client.read(task_id)['graph'])
                    click('native-open'); click('native-open')
                    assert state['imported_id'] == task_id and len(list((folder / 'tasks').glob('*.json'))) == 1
                    # Restore page and UI state, then inspect the final diagram visually.
                    browser.refresh(); click('enter-chat')
                    wait.until(lambda _: '最多 7 页' in browser.find_element(By.ID,'workflow-summary').text)
                    node(12).click()
                    assert '商品价格' in browser.find_element(By.ID,'workflow-inspector').text
                    browser.execute_script("const viewport=document.getElementById('workflow-viewport');"
                        "const loop=document.querySelector('[data-unit-id=\"7\"]');"
                        "viewport.scrollTop += loop.getBoundingClientRect().top-viewport.getBoundingClientRect().top-20;")
                    browser.find_element(By.ID,'workflow-preview').screenshot(str(screenshot))
                    print('Read-only graph UI passed: nested loops/parallel branches, selection/zoom without mutation, '
                          'local and same-ID native AI confirmation, native manual save/read refresh, reload, single task')
                    print('Screenshot: ' + str(screenshot))
            except Exception:
                log.flush(); print((folder / 'storage.log').read_text(encoding='utf-8', errors='replace')[-1500:])
                raise
            finally:
                attached = server.NATIVE_DESIGNERS.pop(sid, None)
                if attached: attached.disconnect()
                server.SESSIONS.pop(sid, None)
                if process and process.poll() is None:
                    try: process.communicate(b'exit', timeout=10)
                    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
                web.shutdown(); web.server_close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8'); run()
