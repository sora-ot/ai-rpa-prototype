"""Optional real Chrome smoke test, using only a local fixture and fake model decisions."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import _bootstrap
from agent import explore


HTML = ("""<!doctype html><html><head><title>Fixture</title></head><body>
<div id="search-wrap"><label>Search</label><input id="query" placeholder="Search"></div><button id="search" onclick="document.getElementById('results').style.display='block'">Search</button>
<div id="results" style="display:none"><article class="card"><h2>Article A</h2><a href="/a">Link A</a></article>
<article class="card"><h2>Article B</h2><a href="/b">Link B</a></article></div>""" +
"<div>" + "".join(f'<a href="/noise/{i}">Noise {i}</a>' for i in range(180)) + "</div>" +
'<nav><a class="n" href="/page2">下一页 &gt;</a></nav></body></html>').encode("utf-8")


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(HTML)))
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, *_args):
        pass


def fake_model(_provider, _key, _model, _system, user, _screenshot=None):
    data = json.loads(user)
    obs = data["observation"]
    actions = data["performed_actions"]
    if not actions:
        target = next(e for e in obs["elements"] if e["tag"] == "div" and "search-wrap" in e["xpath"])
        return {"action": "input", "candidate_id": target["id"], "value": "AI"}
    if len(actions) == 1:
        target = next(e for e in obs["elements"] if e["tag"] == "button")
        return {"action": "click", "candidate_id": target["id"]}
    group = next(g for g in obs["lists"] if g["count"] == 2 and "Article A" in g["samples"][0])
    first = group["first_xpath"]
    title = next(e for e in obs["elements"] if e["tag"] == "h2" and e["absolute_xpath"].startswith(first + "/"))
    link = next(e for e in obs["elements"] if e["tag"] == "a" and e["absolute_xpath"].startswith(first + "/"))
    next_button = next(e for e in obs["elements"] if e["tag"] == "a" and "下一页" in e["text"])
    return {"action": "extract", "mode": "list", "list_id": group["id"],
            "fields": [{"name": "title", "candidate_id": title["id"], "attribute": "text"},
                       {"name": "url", "candidate_id": link["id"], "attribute": "href"}],
            "next_id": next_button["id"]}


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        with patch("agent.ask_json", side_effect=fake_model):
            result = explore("search and extract", {"start_url": url, "task_name": "fixture", "pagination": "until_end"},
                             "deepseek", "dummy", "", print)
        assert result["trace"][0]["xpath"] == '//input[@id="query"]'
        assert "下一页" in result["next_xpath"] and not result["next_xpath"].startswith("/html")
        assert [n["option"] for n in result["task"]["graph"]] == [0, 1, 4, 2, 8, 3, 8, 9, 10, 2, 8, 3]
        print(json.dumps({"samples": result["samples"], "input_xpath": result["trace"][0]["xpath"],
                          "node_options": [n["option"] for n in result["task"]["graph"]]}, ensure_ascii=False))
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
