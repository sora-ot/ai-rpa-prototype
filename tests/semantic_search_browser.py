"""Generic dense DOM/icon search/controlled input/post-login regression in Chrome."""
import json
import argparse
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from _bootstrap import ROOT
import agent
from auth import LoginGate
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from observer import observe


def page(logged, variant):
    suffix = "2" if logged else "1"
    icon = ('<img alt="" class="magnifier" src="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' width=\'18\' height=\'18\'%3E%3Ccircle cx=\'8\' cy=\'8\' r=\'6\'/%3E%3C/svg%3E">'
            if variant == "image" else '<svg width="22" height="22"><use href="#search"></use></svg>')
    control = (f'<div id="submit{suffix}" class="icon-control" role="button" tabindex="0" onclick="submitQuery()">{icon}</div>'
               if variant == "image" else f'<button id="submit{suffix}" class="icon-control" onclick="submitQuery()">{icon}</button>')
    noise = "".join(f'<a href="/noise/{i}">无关导航 {i}</a>' for i in range(360))
    return ("""<!doctype html><meta charset="utf-8"><title>公开内容搜索</title>
      <style>body{font:16px sans-serif}header{position:sticky;top:0;background:white;padding:8px}
      .query-region,.query-box{display:flex;position:relative;gap:8px;height:40px}input{width:250px}
      .icon-control{cursor:pointer;padding:8px;width:26px;display:inline-flex}
      nav{display:flex;flex-wrap:wrap;gap:6px;height:100px;overflow:hidden}.card{padding:12px}
      #login-dialog{position:fixed;inset:0;z-index:9999;background:#ffffffed;display:none}
      </style><nav>""" + noise + f"""</nav><header><div class="query-region">
      <input id="measure" aria-hidden="true" style="position:absolute;opacity:0;pointer-events:none" placeholder="搜索公开内容">
      <div class="query-box"><label for="query{suffix}">检索内容</label><input id="query{suffix}" class="query" type="search" placeholder="搜索公开内容" oninput="window.stateQuery=this.value">
      <button class="attachment" type="button"><svg width="20" height="20"><use href="#add"></use></svg></button></div>
      {control}<div style="position:relative;width:40px"><button class="covered-control" aria-label="搜索旧入口">搜索</button>
      <div class="cover-layer" style="position:absolute;inset:0;z-index:10;background:#ddd">浮层</div></div>
      </div></header><main><div id="results">
      <article class="card"><h2 class="note-title">推荐内容，尚未按用户条件搜索</h2><span class="note-author">推荐作者甲</span></article>
      <article class="card"><h2 class="note-title">首页推荐，与目标无关</h2><span class="note-author">推荐作者乙</span></article>
      </div></main><div id="login-dialog" class="login-dialog" role="dialog"><h2>请手工登录</h2>
      <button id="manual-login" onclick="document.cookie='logged=1; path=/; Max-Age=3600; SameSite=Lax';location.reload()">模拟用户完成登录</button></div>
      <svg style="display:none"><symbol id="search"><path d="M2 2h15v15H2z"/></symbol></svg>
      <script>window.stateQuery='';window.logged={str(logged).lower()};
      function submitQuery(){{if(!window.logged){{document.getElementById('login-dialog').style.display='block';return;}}
        const q=window.stateQuery;history.replaceState(null,'','?keyword='+encodeURIComponent(q));
        const card=i=>'<h2 class="note-title">'+q+'公开笔记标题'+(i+1)+'</h2><span class="note-author">作者'+(i+1)+'</span>';
        document.getElementById('results').innerHTML=Array.from({{length:8}},(_,i)=>
          '<article class="card">'+(i===7?'':card(i))+'</article>').join('');
        setTimeout(()=>document.querySelector('#results article:last-child').innerHTML=card(7),2500);}}
      </script>""").encode("utf-8")


class Fixture(BaseHTTPRequestHandler):
    variant = "image"
    def do_GET(self):
        body = page("logged=1" in self.headers.get("Cookie", ""), self.variant)
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *_args): pass


class ManualTestGate(LoginGate):
    def wait_for_login(self, driver, start_url, reason):
        # Simulates only the human action at the local fixture, not AI login.
        self.used = True
        self.logins += 1
        driver.find_element(By.ID, "manual-login").click()
        driver.get(start_url)


def model(_provider, _key, _model, _system, user, _screenshot=None):
    context = json.loads(user); observation = context["observation"]
    actions = context["performed_actions"]
    if not actions:
        if observation["observation_stats"]["focus"] != "controls":
            return {"action": "observe", "focus": "controls"}
        target = next(e for e in observation["elements"] if e.get("editable"))
        assert not any("measure" in e.get("class_name", "") for e in observation["elements"] if e.get("editable"))
        form = next(form for form in observation["search_forms"] if form["input_id"] == target["id"])
        assert not any(e["id"] in form["submit_ids"] for e in observation["elements"] if "attachment" in e.get("class_name", ""))
        assert any(e.get("obscured") for e in observation["elements"] if "covered-control" in e.get("class_name", ""))
        return {"action": "input", "candidate_id": target["id"], "value": "合肥"}
    if len(actions) == 1:
        target = next(e for e in observation["elements"] if e.get("clickable") and "icon-control" in e.get("class_name", ""))
        return {"action": "click", "candidate_id": target["id"]}
    if observation["observation_stats"]["focus"] != "content":
        return {"action": "observe", "focus": "content"}
    group = next(g for g in observation["lists"] if g["count"] == 8 and "合肥" in g["samples"][0])
    fields = []
    for name, class_name in [("标题", "note-title"), ("作者", "note-author")]:
        field = next(e for e in observation["elements"] if class_name in e.get("class_name", "") and group["id"] in e.get("first_record_of", []))
        fields.append({"name": name, "candidate_id": field["id"], "attribute": "text"})
    assert context["search_progress"]["ready"]
    return {"action": "extract", "mode": "list", "list_id": group["id"], "fields": fields}


def main(native=False):
    chrome = ROOT.parent / "EasySpider" / "ElectronJS" / "chrome_win64"
    constructor = webdriver.Chrome
    def browser(*args, **kwargs):
        kwargs["options"].add_argument("--no-sandbox")
        kwargs["options"].add_argument("--headless=new")
        return constructor(*args, **kwargs)
    for variant in ("image", "svg"):
        Fixture.variant = variant
        web = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{web.server_port}/"
        (ROOT / "browser_tmp").mkdir(exist_ok=True)
        try:
            profile_root = ROOT / ("profiles" if native else "browser_tmp")
            profile_root.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="semantic_fixture_", dir=profile_root) as folder:
                gate = ManualTestGate(url, lambda _: None, lambda: None)
                gate.profile_dir = Path(folder); gate.logins = 0
                with patch("agent.ask_json", side_effect=model), patch("selenium.webdriver.Chrome", side_effect=browser):
                    result = agent.explore("搜索合肥，采集一页标题、作者",
                        {"start_url": url, "task_name": "通用图标搜索", "keywords": ["合肥"],
                         "pagination": "page_limit", "page_limit": 1, "requires_login": True},
                        "deepseek", "fixture", "model", print, max_actions=2, login_gate=gate)
                assert gate.logins == 1
                assert len(result["trace"]) == 2, result["trace"]
                assert 'query2' in result["trace"][0]["xpath"] and 'submit2' in result["trace"][1]["xpath"]
                assert len(result["samples"]) == 5 and all("合肥" in row["标题"] and row["作者"] for row in result["samples"])
                assert not any('query1' in n["parameters"].get("xpath", "") for n in result["task"]["graph"])
                assert not any(n["title"].startswith("翻页") for n in result["task"]["graph"])
                if native:
                    from run_native import run
                    artifact = Path(folder) / "task.json"
                    artifact.write_text(json.dumps(result["task"], ensure_ascii=False), encoding="utf-8")
                    output = run(artifact, str(gate.profile_dir), headless=True, timeout=90)
                    assert output["rows"] == 8 and output["nonempty_rows"] == 8, output
                    print("Native delayed-page readiness passed: 8 complete rows, no partial 7-row output")
        finally:
            web.shutdown(); web.server_close()
        print(variant + " passed: dense navigation, ghost input, icon control, adaptive observation, login restart, native graph")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    main(parser.parse_args().native)
