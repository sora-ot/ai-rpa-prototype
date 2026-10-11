"""Live search diagnosis and real-model test; artifacts remain in generated/.

Default case: 小红书 / 合肥 / 标题、作者 / 一页. Use --url and --request
for another site. No credentials or full HTML are printed. Login is manual.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from unittest.mock import patch

from _bootstrap import ROOT
import agent
from auth import LoginGate, detect_challenge, profile_path
from observer import observe
from settings import resolve_model_config


DEFAULT_URL = "https://www.xiaohongshu.com/explore"
DEFAULT_REQUEST = "帮我在小红书上获取合肥的相关内容，采集的信息包括标题、作者，采集一页数据"
ARTIFACT_PREFIX = "live_search"


def save(name, value):
    folder = ROOT / "generated"
    folder.mkdir(exist_ok=True)
    path = folder / (name.replace("live_search", ARTIFACT_PREFIX, 1) + ".json")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def inspect_page(args):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    chrome = ROOT.parent / "EasySpider" / "ElectronJS" / "chrome_win64"
    options = webdriver.ChromeOptions()
    options.binary_location = str(chrome / "chrome.exe")
    for value in ["--window-size=1366,900", "--disable-gpu", "--disable-notifications",
                  f"--user-data-dir={profile_path(args.url)}", "--profile-directory=Default"]:
        options.add_argument(value)
    if args.sandbox_browser:
        options.add_argument("--no-sandbox")
    with webdriver.Chrome(service=Service(str(chrome / "chromedriver_win64.exe")), options=options) as driver:
        driver.set_page_load_timeout(35)
        driver.get(args.url)
        time.sleep(4)
        if args.wait_login and detect_challenge(driver, args.url):
            print("WAIT_LOGIN 请在本次测试 Chrome 手工登录；完成后输入 done。", flush=True)
            while input().strip().lower() != "done":
                pass
            driver.get(args.url)
            time.sleep(3)
            # Allow observation changes to be tested after manual login.
            import importlib
            import observer
            import settings
            importlib.reload(settings)
            importlib.reload(observer)
            current_observe = observer.observe
        else:
            current_observe = observe
        snapshot = current_observe(driver)
        # Raw controls help compare observation coverage; never read input values.
        controls = driver.execute_script("""
          return [...document.querySelectorAll('input,button,[role="button"],svg,use,[class*="search"],[id*="search"]')]
            .map(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {
              tag:e.tagName,id:e.id,cls:typeof e.className==='string'?e.className:e.getAttribute('class'),
              type:e.getAttribute('type'),placeholder:e.getAttribute('placeholder'),label:e.getAttribute('aria-label'),
              aria_hidden:e.getAttribute('aria-hidden'),pointer_events:s.pointerEvents,opacity:s.opacity,
              text:(e.innerText||'').slice(0,60),icon:e.getAttribute('href')||e.getAttribute('xlink:href'),
              visible:r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'}})
            .filter(e=>e.visible).slice(0,45);
        """)
        report = {"url": snapshot["url"], "title": snapshot["title"],
                  "challenge": detect_challenge(driver, args.url),
                  "candidate_count": len(snapshot["elements"]),
                  "inputs": [e for e in snapshot["elements"] if e.get("editable")],
                  "controls": [e for e in snapshot["elements"] if e.get("clickable") and
                               any(token in e.get("class_name", "").lower() for token in ("search", "submit"))][:16],
                  "search_forms": snapshot.get("search_forms", []),
                  "raw_controls": controls}
        path = save("live_search_inspection", report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        print("Inspection: " + str(path), flush=True)


def run(args):
    provider, key, model = resolve_model_config({})
    plan = (json.loads(Path(args.plan_file).read_text(encoding="utf-8")) if args.plan_file
            else agent.make_plan(args.request, args.url, provider, key, model))
    save("live_search_plan", plan)
    print("PLAN " + json.dumps(plan, ensure_ascii=False), flush=True)
    if plan.get("needs_clarification"):
        raise ValueError("计划仍需澄清，请先在界面对话中确认细节")
    if not args.run:
        return

    def on_wait(reason):
        print("WAIT_LOGIN " + reason + "；请在本次测试 Chrome 手工登录后输入 done。", flush=True)
        while input().strip().lower() != "done":
            print("请在完成登录后输入 done", flush=True)
        gate.complete()

    gate = LoginGate(plan["start_url"], on_wait, lambda: print("LOGIN_OK", flush=True))
    original = agent.ask_json
    evidence = []

    def ask(*positional, **keyword):
        result = original(*positional, **keyword)
        if positional[3] == agent.ACT_SYSTEM:
            context = json.loads(positional[4])
            observation = context["observation"]
            item = {"url": observation.get("url"), "decision": result,
                    "elements": len(observation.get("elements", [])),
                    "lists": len(observation.get("lists", [])),
                    "search_forms": observation.get("search_forms", []),
                    "last_tool_error": context.get("last_tool_error", "")}
            evidence.append(item)
            print("DECISION " + json.dumps(item, ensure_ascii=False), flush=True)
        return result

    from selenium import webdriver
    constructor = webdriver.Chrome

    def browser(*positional, **keyword):
        if args.sandbox_browser:
            keyword["options"].add_argument("--no-sandbox")
        return constructor(*positional, **keyword)

    try:
        with patch("agent.ask_json", side_effect=ask), patch("selenium.webdriver.Chrome", side_effect=browser):
            result = agent.explore(args.request, plan, provider, key, model,
                lambda message: print("EVENT " + message, flush=True), login_gate=gate)
        path = save("live_search_task", result["task"])
        save("live_search_result", result)
        print(json.dumps({"task_file": str(path), "sample_count": len(result["samples"]),
                          "samples": result["samples"][:3], "trace": result["trace"]}, ensure_ascii=False), flush=True)
    finally:
        save("live_search_evidence", evidence)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--request", default=DEFAULT_REQUEST)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--wait-login", action="store_true", help="检查页面时允许手工登录")
    parser.add_argument("--run", action="store_true", help="调用真实模型规划并探索")
    parser.add_argument("--plan-file", help="复用本机已生成的测试计划")
    parser.add_argument("--artifact-prefix", default="live_search", help="隔离本次测试产物的文件名前缀")
    parser.add_argument("--sandbox-browser", action="store_true", help="仅测试沙箱需要的 Chrome 参数")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,50}", args.artifact_prefix):
        parser.error("产物前缀只能包含小写字母、数字、下划线")
    ARTIFACT_PREFIX = args.artifact_prefix
    try:
        inspect_page(args) if args.inspect else run(args)
    except Exception as exc:
        print("ERROR " + " ".join(str(exc).splitlines()[:3])[:600], flush=True)
        sys.exit(1)
