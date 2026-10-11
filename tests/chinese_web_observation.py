"""Read-only coverage audit on public Chinese sites; never signs in or clicks links."""
import argparse
import json
import tempfile
import time
from pathlib import Path

from _bootstrap import ROOT
import agent
from auth import detect_challenge
from observer import inspect_dom, observe
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service


SITES = {
    'baidu': ('百度', 'https://www.baidu.com/'),
    'people': ('人民网', 'https://www.people.com.cn/'),
    'chinanews': ('中国新闻网', 'https://www.chinanews.com.cn/china/'),
    'thepaper': ('澎湃新闻', 'https://www.thepaper.cn/'),
    'cctv': ('央视网', 'https://www.cctv.com/'),
    'sciencenet': ('科学网', 'https://news.sciencenet.cn/'),
    'bilibili': ('哔哩哔哩', 'https://www.bilibili.com/'),
    'douban': ('豆瓣读书', 'https://book.douban.com/'),
}


def run(names):
    chrome = ROOT.parent / 'EasySpider/ElectronJS/chrome_win64'
    folder = ROOT / 'generated/chinese_observation'
    folder.mkdir(parents=True, exist_ok=True)
    (ROOT / 'browser_tmp').mkdir(exist_ok=True)
    previous = json.loads((folder / 'report.json').read_text(encoding='utf-8')) if (folder / 'report.json').exists() else []
    reports = [r for r in previous if r.get('site') not in [SITES[n][0] for n in names]]
    with tempfile.TemporaryDirectory(prefix='chinese_audit_', dir=ROOT / 'browser_tmp') as profile:
        options = webdriver.ChromeOptions()
        options.binary_location = str(chrome / 'chrome.exe')
        for value in ('--headless=new', '--window-size=1366,900', '--disable-notifications', f'--user-data-dir={profile}'):
            options.add_argument(value)
        with webdriver.Chrome(service=Service(str(chrome / 'chromedriver_win64.exe')), options=options) as driver:
            driver.set_page_load_timeout(25)
            driver.set_script_timeout(30)
            for name in names:
                label, url = SITES[name]
                report = {'site': label, 'requested_url': url}
                try:
                    try:
                        driver.get(url)
                    except TimeoutException:
                        driver.execute_script('window.stop()')
                    time.sleep(3)
                    start = time.monotonic()
                    base = observe(driver)
                    report.update({'url': base['url'], 'title': base['title'], 'default_seconds': round(time.monotonic() - start, 2),
                                   'default': base['observation_stats'], 'challenge': detect_challenge(driver, url),
                                   'search_forms': base['search_forms'], 'model_input_chars': len(json.dumps(agent._compact(base), ensure_ascii=False))})
                    if report['challenge']:
                        report['challenge_widgets'] = driver.execute_script('''return [...document.querySelectorAll('iframe,[id],[class]')]
                          .filter(e=>/captcha|recaptcha|hcaptcha|turnstile/i.test((e.id||'')+' '+(typeof e.className==='string'?e.className:'')+' '+(e.getAttribute('src')||'')))
                          .slice(0,8).map(e=>{let r=e.getBoundingClientRect(),s=getComputedStyle(e);return {tag:e.tagName,id:e.id,cls:e.getAttribute('class'),
                            display:s.display,visibility:s.visibility,opacity:s.opacity,visible:e.checkVisibility?.({checkOpacity:true,checkVisibilityCSS:true}),
                            bbox:[r.x,r.y,r.width,r.height]}});''')
                    expanded = observe(driver, expanded=True)
                    report['expanded'] = expanded['observation_stats']
                    detailed = inspect_dom(driver, expanded, scope='page')
                    report['cleaned_dom'] = {k: v for k, v in detailed['dom'].items() if k != 'html'}
                    report['dom_chunk_candidates'] = detailed['observation_stats']['dom_candidates']
                    report['lists'] = [{'id': g['id'], 'count': g['count'], 'score': g['score'],
                                        'features': g['features'], 'samples': g['samples']} for g in base['lists'][:6]]
                    report['status'] = 'challenge' if report['challenge'] else ('observed' if len(base['elements']) >= 10 else 'empty_or_blocked')
                    (folder / f'{name}_model_input.json').write_text(json.dumps(agent._compact(detailed), ensure_ascii=False, indent=2), encoding='utf-8')
                except Exception as exc:
                    report.update({'status': 'error', 'error': ' '.join(str(exc).splitlines()[:2])[:400]})
                reports.append(report)
                (folder / 'report.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({k: v for k, v in report.items() if k not in ('lists', 'search_forms')}, ensure_ascii=False), flush=True)
    return reports


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sites', nargs='*', help='站点代号：' + ', '.join(SITES))
    args = parser.parse_args()
    if any(name not in SITES for name in args.sites):
        parser.error('未知站点代号')
    run(args.sites or list(SITES))
