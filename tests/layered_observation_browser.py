"""Real Chrome regressions for neutral lists, broad candidates and paged DOM discovery."""
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

from _bootstrap import ROOT
import agent
from auth import detect_challenge
from observer import candidate, inspect_dom, observe
from selenium import webdriver
from selenium.webdriver.chrome.service import Service


def fixture():
    nav = ''.join(f'<a href="/n/{i}">栏目导航{i}</a>' for i in range(1300))
    news = ''.join(f'<article class="news-row"><h2><a id="title-{i}" href="/news/{i}">公共新闻标题{i}</a></h2>'
                   f'<span id="author-{i}">作者{i}</span><time>2026-10-09</time><p>这是一条包含背景说明的公共新闻记录。</p></article>' for i in range(12))
    forum = ''.join(f'<li class="discussion"><h3>讨论主题{i}</h3><span>发布者{i}</span><time>2026-10-08</time></li>' for i in range(15))
    table = ''.join(f'<tr><td>机构{i}</td><td>公告编号{i}</td><td>2026-10-09</td></tr>' for i in range(100))
    blocks = ''.join(f'<p>普通正文段落{i}，应允许模型自行判断是否需要。</p>' for i in range(1100))
    archive = ''.join(f'<article class="news-row"><h2><a href="/archive/{i}">存档新闻标题{i}</a></h2>'
                      f'<span>存档作者{i}</span><time>2026-10-08</time><p>同一数据类型的另一分组。</p></article>' for i in range(3))
    return f'''<!doctype html><meta charset="utf-8"><title>通用资料页面</title>
      <style>body{{font:16px sans-serif}}nav{{height:150px;overflow:hidden}}a{{margin-right:8px}}
      article,li{{padding:8px}}#ghost{{opacity:0;pointer-events:none;position:absolute}}</style>
      <nav>{nav}</nav><header><form role="search"><label for="q">搜索资料</label>
      <input id="ghost" aria-hidden="true" placeholder="搜索资料"><input id="q" type="search">
      <button type="submit" aria-label="检索">搜索</button></form></header>
      <main><section id="news">{news}</section><section id="archive">{archive}</section><ul id="forum">{forum}</ul><table><tbody>{table}</tbody></table>
      <section id="article-body">{blocks}</section><p id="emoji-field">{'🧪' * 700}</p><section id="tail-wrapper"><p id="late-detail">末尾的补充资料字段</p>
      <x-command id="custom-command" tabindex="0" aria-label="展开资料">查看扩展信息</x-command></section></main>
      <input type="password" style="display:none" value="PASSWORD_SENTINEL"><input type="hidden" name="token" value="TOKEN_SENTINEL">
      <textarea name="verification">OTP_SENTINEL</textarea>
      <a href="/public?keyword=合肥&amp;token=URL_TOKEN_SENTINEL">公开入口</a>
      <button id="__captcha_btn_placeholder" style="opacity:0;position:absolute;left:-9999px;top:-9999px">初始化验证组件</button>
      <div style="opacity:0;pointer-events:none"><div class="login-overlay" role="dialog" style="position:fixed;inset:0">预先挂载的登录窗口</div></div>
      <script>window.privateValue='SCRIPT_SENTINEL';</script>'''.encode('utf-8')


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        body = fixture()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main():
    web = ThreadingHTTPServer(('127.0.0.1', 0), Site)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{web.server_port}/'
    chrome = ROOT.parent / 'EasySpider/ElectronJS/chrome_win64'
    options = webdriver.ChromeOptions()
    options.binary_location = str(chrome / 'chrome.exe')
    for value in ('--headless=new', '--no-sandbox', '--window-size=1366,900'):
        options.add_argument(value)
    (ROOT / 'browser_tmp').mkdir(exist_ok=True)
    report = {}
    try:
        with tempfile.TemporaryDirectory(dir=ROOT / 'browser_tmp', prefix='layered_') as folder:
            options.add_argument(f'--user-data-dir={folder}')
            with webdriver.Chrome(service=Service(str(chrome / 'chromedriver_win64.exe')), options=options) as driver:
                driver.get(url)
                assert not detect_challenge(driver, url), 'A transparent captcha placeholder is not an active challenge'
                base = observe(driver)
                compact = agent._compact(base)
                assert len(base['elements']) == 1000
                assert base['observation_stats']['omitted_elements'] > 0
                assert any(e.get('dom_id') == 'custom-command' for e in base['elements'])
                assert not any(e.get('dom_id') == 'ghost' for e in base['elements'])
                assert not any(e.get('dom_id') == 'late-detail' for e in base['elements'])
                news = next(g for g in base['lists'] if 'news' in g['xpath'] and g['count'] == 12)
                assert 'price' not in news['features'] and 'sales' not in news['features']
                assert base['lists'][0]['features']['navigation_context'] == 0
                assert any('/tr' in g['xpath'] and g['count'] == 100 for g in base['lists'])
                archive_list = next(g for g in base['lists'] if 'archive' in g['xpath'] and g['count'] == 3)
                extraction, samples = agent._prepare_extract(base, {'mode': 'list', 'list_ids': [news['id'], archive_list['id']],
                    'fields': [{'name': name, 'attribute': 'text', 'candidate_id': next(e['id'] for e in base['elements'] if e.get('dom_id') == dom_id)}
                               for name, dom_id in [('标题', 'title-0'), ('作者', 'author-0')]]}, driver)
                from selenium.webdriver.common.by import By
                assert len(driver.find_elements(By.XPATH, extraction['list_xpath'])) == 15
                assert samples[0] == {'标题': '公共新闻标题0', '作者': '作者0'}
                region = inspect_dom(driver, base, scope='list', candidate_id=news['id'])
                assert '<article' in region['dom']['html'] and '公共新闻标题0' in region['dom']['html']
                assert all(e['id'] in agent._available_candidate_ids(region)['elements'] for e in agent._compact(region)['elements'])
                search = inspect_dom(driver, base, scope='search', candidate_id=base['search_forms'][0]['input_id'])
                assert '<form' in search['dom']['html'] and '<button' in search['dom']['html']
                snapshot, offset, blocks = base, 0, []
                for _ in range(12):
                    snapshot = inspect_dom(driver, snapshot, scope='page', offset=offset, max_chars=48000)
                    blocks.append(snapshot['dom']['html'])
                    if snapshot['dom']['next_offset'] is None:
                        break
                    offset = snapshot['dom']['next_offset']
                else:
                    raise AssertionError('DOM pagination did not finish')
                cleaned = ''.join(blocks)
                for secret in ('PASSWORD_SENTINEL', 'TOKEN_SENTINEL', 'OTP_SENTINEL', 'SCRIPT_SENTINEL', 'URL_TOKEN_SENTINEL'):
                    assert secret not in cleaned, secret
                assert '<script' not in cleaned and '<style' not in cleaned
                late = next(e for e in snapshot['elements'] if e.get('dom_id') == 'late-detail')
                assert late['id'] in cleaned and candidate(snapshot, late['id'])['xpath'] == '//p[@id="late-detail"]'
                assert '合肥' in unquote(cleaned) and '[redacted]' in unquote(cleaned)
                emoji = next(e for e in snapshot['elements'] if e.get('dom_id') == 'emoji-field')
                for size in (1000, 1001):
                    part = inspect_dom(driver, snapshot, scope='element', candidate_id=emoji['id'], max_chars=size)
                    json.dumps(agent._compact(part), ensure_ascii=False).encode('utf-8')
                    rest = inspect_dom(driver, part, scope='element', candidate_id=emoji['id'], max_chars=size,
                                       offset=part['dom']['next_offset'])
                    json.dumps(agent._compact(rest), ensure_ascii=False).encode('utf-8')
                with patch('observer.OBSERVATION_MAX_LISTS', 1):
                    limited = observe(driver)
                restored = inspect_dom(driver, limited, scope='page', max_chars=48000)
                while len(restored['lists']) == len(limited['lists']) and restored['dom']['next_offset'] is not None:
                    restored = inspect_dom(driver, restored, scope='page', offset=restored['dom']['next_offset'], max_chars=48000)
                assert len(restored['lists']) > len(limited['lists'])
                report.update({'default_elements': len(base['elements']), 'eligible': base['observation_stats']['eligible_elements'],
                               'list_types': ['news', 'discussion', 'table'], 'dom_chunks': len(blocks),
                               'omitted_field_recovered': True, 'sanitization': True,
                               'extra_lists_registered': True, 'split_lists_merge': True,
                               'model_input_chars': len(json.dumps(compact, ensure_ascii=False))})

            def model(_provider, _key, _model, _system, user, _screenshot=None):
                context = json.loads(user)
                observation = context['observation']
                if not observation.get('dom'):
                    group = next(g for g in observation['lists'] if g['count'] == 12 and '公共新闻' in g['samples'][0])
                    return {'action': 'inspect_dom', 'scope': 'list', 'candidate_id': group['id']}
                group = next(g for g in observation['lists'] if g['count'] == 12 and '公共新闻' in g['samples'][0])
                fields = [{'name': name, 'attribute': 'text', 'candidate_id': next(e['id'] for e in observation['elements'] if e.get('dom_id') == dom_id)}
                          for name, dom_id in [('标题', 'title-0'), ('作者', 'author-0')]]
                return {'action': 'extract', 'mode': 'list', 'list_id': group['id'], 'fields': fields}

            with patch('agent.ask_json', side_effect=model), patch('selenium.webdriver.Chrome', side_effect=lambda *a, **kw: webdriver_constructor(*a, **add_options(kw))):
                result = agent.explore('采集当前页新闻标题和作者', {'start_url': url, 'task_name': '公共新闻',
                    'pagination': 'page_limit', 'page_limit': 1}, 'deepseek', 'fixture', 'model', print)
            assert not result['trace'], result['trace']
            assert len(result['samples']) == 5 and result['samples'][0] == {'标题': '公共新闻标题0', '作者': '作者0'}
            assert not any(n['title'] == 'inspect_dom' for n in result['task']['graph'])
            report['dom_tool_to_native_graph'] = True
    finally:
        web.shutdown()
        web.server_close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


webdriver_constructor = webdriver.Chrome


def add_options(kwargs):
    kwargs['options'].add_argument('--headless=new')
    kwargs['options'].add_argument('--no-sandbox')
    return kwargs


if __name__ == '__main__':
    main()
