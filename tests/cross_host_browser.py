"""Real Chrome cross-host search, redirects, login handoff and three-page native replay."""
import argparse
import csv
import html
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from unittest.mock import patch

from _bootstrap import ROOT
import agent
from auth import LoginGate
from selenium import webdriver
from selenium.webdriver.common.by import By


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        variant = query.get("variant", ["same_tab"])[0]
        remote = f"http://127.0.0.1:{self.server.server_port}"
        if parsed.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", remote + "/?variant=redirect")
            self.end_headers()
            return
        if parsed.path == "/signed-in":
            self.send_response(302)
            self.send_header("Set-Cookie", "logged=1; Path=/; Max-Age=3600; SameSite=Lax")
            self.send_header("Location", "/?variant=login")
            self.end_headers()
            return
        if parsed.path == "/search" and variant == "login" and "logged=1" not in self.headers.get("Cookie", ""):
            content = '<div id="login-dialog" role="dialog" style="position:fixed;inset:0;background:white"><h1>请手工登录</h1><a id="manual-login" href="/signed-in">模拟用户完成登录</a></div>'
        elif parsed.path == "/search":
            word = query["word"][0]
            page = int(query.get("page", ["1"])[0])
            content = "<main>" + "".join(
                f'<article class="news-card"><h2><a class="news-title" href="{remote}/article/{page}/{i}">{html.escape(word)}新闻第{page}页第{i}条</a></h2></article>'
                for i in (1, 2)) + "</main>"
            if page < 4:
                href = "/search?" + urlencode({"word": word, "page": page + 1, "variant": variant})
                content += f'<a rel="next" href="{html.escape(href, quote=True)}">下一页</a>'
        else:
            target = ' target="_blank"' if variant == "new_tab" else ""
            content = f'<form action="{remote}/search"{target}><label for="q">新闻搜索</label><input id="q" name="word" type="search"><input name="variant" type="hidden" value="{variant}"><button id="submit" type="submit">搜索</button></form>'
        body = ('<!doctype html><meta charset="utf-8"><title>公开新闻搜索测试</title>'
                '<style>body{font:18px sans-serif;margin:30px}article{padding:12px}</style>' + content).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_): pass


class ManualFixtureGate(LoginGate):
    logins = 0

    def wait_for_login(self, driver, start_url, reason):
        # Only simulates the user's action on this local fixture.
        self.used = True
        self.logins += 1
        driver.find_element(By.ID, "manual-login").click()
        driver.get(start_url)


def model(_provider, _key, _model, _system, user, _screenshot=None):
    context = json.loads(user)
    observation = context["observation"]
    actions = context["performed_actions"]
    if not actions:
        target = next(e for e in observation["elements"] if e.get("editable"))
        return {"action": "input", "candidate_id": target["id"], "value": "吉林大学"}
    if len(actions) == 1:
        target = next(e for e in observation["elements"] if e.get("dom_id") == "submit")
        return {"action": "click", "candidate_id": target["id"]}
    assert context["navigation_context"]["cross_host"], context["navigation_context"]
    assert context["search_progress"]["ready"]
    group = next(g for g in observation["lists"] if g["count"] == 2 and "第1页" in g["samples"][0])
    title = next(e for e in observation["elements"] if "news-title" in e.get("class_name", "")
                 and group["id"] in e.get("first_record_of", []))
    return {"action": "extract", "mode": "list", "list_id": group["id"],
            "fields": [{"name": "新闻标题", "candidate_id": title["id"], "attribute": "text"},
                       {"name": "网页链接", "candidate_id": title["id"], "attribute": "href"}],
            "next_id": observation["next_candidates"][0]["id"]}


def main(native, cases=None):
    original = webdriver.Chrome

    def browser(*args, **kwargs):
        kwargs["options"].add_argument("--headless=new")
        return original(*args, **kwargs)

    web = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    report = []
    try:
        (ROOT / "profiles").mkdir(exist_ok=True)
        for variant in cases or ("same_tab", "new_tab", "redirect", "login"):
            start = f"http://localhost:{web.server_port}/" + ("redirect" if variant == "redirect" else "?variant=" + variant)
            with tempfile.TemporaryDirectory(prefix="cross_host_", dir=ROOT / "profiles") as folder:
                gate = ManualFixtureGate(start, lambda _: None, lambda: None)
                gate.profile_dir = Path(folder)
                events = []
                with patch("selenium.webdriver.Chrome", side_effect=browser), patch("agent.ask_json", side_effect=model), \
                     patch("agent.risk_review_enabled", return_value=False):
                    result = agent.explore("搜索吉林大学新闻，采集标题和链接，3页", {
                        "start_url": start, "task_name": "通用跨域新闻搜索", "keywords": ["吉林大学"],
                        "pagination": "page_limit", "page_limit": 3, "requires_login": True},
                        "deepseek", "fixture", "model", events.append, max_actions=2, login_gate=gate)
                assert gate.logins == (1 if variant == "login" else 0), events
                assert [a["action"] for a in result["trace"]] == ["input", "click"], result["trace"]
                assert len(result["samples"]) == 2 and all("第1页" in row["新闻标题"] for row in result["samples"])
                artifact = ROOT / "generated" / f"cross_host_{variant}_task.json"
                artifact.parent.mkdir(exist_ok=True)
                artifact.write_text(json.dumps(result["task"], ensure_ascii=False, indent=2), encoding="utf-8")
                item = {"variant": variant, "manual_logins": gate.logins, "events": events,
                        "task_file": str(artifact), "sample_rows": len(result["samples"])}
                if native:
                    from run_native import run
                    output = run(artifact, folder, headless=True, timeout=90)
                    with Path(output["output"]).open(encoding="utf-8-sig", newline="") as stream:
                        rows = list(csv.DictReader(stream))
                    expected = [f"吉林大学新闻第{page}页第{i}条" for page in (1, 2, 3) for i in (1, 2)]
                    assert [row["新闻标题"] for row in rows] == expected, output
                    assert output["nonempty_rows"] == 6, output
                    item["native"] = output
                report.append(item)
                print(json.dumps(item, ensure_ascii=False), flush=True)
    finally:
        web.shutdown()
        web.server_close()
        suffix = "_" + "_".join(cases) if cases else ""
        path = ROOT / "generated" / f"cross_host{suffix}_browser_report.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--case", action="append", choices=("same_tab", "new_tab", "redirect", "login"))
    args = parser.parse_args()
    main(args.native, args.case)
