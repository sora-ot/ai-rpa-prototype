"""Run a generated task through the original packaged EasySpider executor.

This uses a local three-page fixture and a writable sandbox under this prototype.
On Windows, executor_sandbox/EasySpider/resources/app/chrome_win64 must be a
directory junction to EasySpider/ElectronJS/chrome_win64.
"""
import csv
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from _bootstrap import ROOT
from workflow import compile_task


BASE = ROOT
SANDBOX = BASE / "executor_sandbox"
CHROME_DIR = BASE.parent / "EasySpider" / "ElectronJS" / "chrome_win64"
EXECUTOR = CHROME_DIR / "easyspider_executestage.exe"


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/search":
            body = (b'<!doctype html><html><body><form action="/results" method="get">'
                    b'<div id="search-wrap"><input id="query" name="q"></div>'
                    b'<button id="search" type="submit">Search</button></form></body></html>')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/results" and parse_qs(parsed.query).get("q") != ["AI"]:
            self.send_error(400, "Expected q=AI")
            return
        page = {"/": 1, "/results": 1, "/page2": 2, "/page3": 3}.get(parsed.path)
        if page is None:
            self.send_error(404)
            return
        cards = "".join(
            f'<article class="card"><h2 title="Page {page} Item {item}">Page {page} I...</h2>'
            f'<a href="/detail/{page}/{item}">Details</a></article>'
            for item in (1, 2))
        previous_link = (f'<a class="n" href="/page{page - 1}">上一页</a>'
                         if page > 1 else "")
        next_link = (f'<a class="n" href="/page{page + 1}">下一页 &gt;</a>'
                     if page < 3 else "")
        body = (f'<!doctype html><html><head><title>Page {page}</title></head>'
                f'<body><main>{cards}<nav id="page">{previous_link}{next_link}</nav></main></body></html>').encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc_type = sys.exc_info()[0]
        if exc_type not in (ConnectionResetError, ConnectionAbortedError):
            super().handle_error(request, client_address)


def main():
    if sys.platform != "win32" or not EXECUTOR.is_file():
        raise RuntimeError("此验证脚本需要 Windows 打包执行器")
    junction = SANDBOX / "EasySpider" / "resources" / "app" / "chrome_win64"
    if not junction.is_dir():
        raise RuntimeError(f"缺少 Chrome 资源目录连接：{junction}")
    server = QuietServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        task_id = int(time.time())
        url = f"http://127.0.0.1:{server.server_port}/search"
        extraction = {
            "mode": "list", "list_xpath": '//article[contains(@class,"card")]',
            "fields": [
                {"name": "title", "attribute": "title", "xpath": "//h2",
                 "relative_xpath": "/h2[1]", "sample": "Page 1 Item 1"},
                {"name": "link", "attribute": "href", "xpath": "//a",
                 "relative_xpath": "/a[1]", "sample": f"http://127.0.0.1:{server.server_port}/detail/1/1"},
            ],
            "next_xpath": '//a[contains(normalize-space(.),"下一页") or contains(normalize-space(.),"下页")]',
        }
        task = compile_task(url, "Executor Smoke", [
            {"action": "input", "xpath": '//input[@id="query"]', "value": "AI"},
            {"action": "click", "xpath": '//button[@id="search"]'}], extraction)
        task.update({"id": task_id, "saveName": "executor_smoke", "recordLog": True})
        instances = SANDBOX / "execution_instances"
        instances.mkdir(parents=True, exist_ok=True)
        (instances / f"{task_id}.json").write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
        (SANDBOX / "config.json").write_text(json.dumps({
            "webserver_address": "http://127.0.0.1", "webserver_port": 8074,
            "user_data_folder": "./user_data", "debug": False,
            "mysql_config_path": "./mysql_config.json",
            "absolute_user_data_folder": str(SANDBOX / "user_data")}, ensure_ascii=False), encoding="utf-8")
        command = [str(EXECUTOR), "--ids", f"[{task_id}]", "--read_type", "local",
                   "--headless", "1", "--keyboard", "0"]
        log_path = SANDBOX / f"executor_{task_id}.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen(command, cwd=SANDBOX, stdout=log, stderr=subprocess.STDOUT,
                                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            try:
                process.wait(timeout=100)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait(timeout=10)
                output = log_path.read_bytes().decode("utf-8", "replace")
                raise RuntimeError(f"执行器超过 100 秒；日志：\n{output[-7000:]}") from exc
        output = log_path.read_bytes().decode("utf-8", "replace")
        csv_files = list((SANDBOX / "Data" / f"Task_{task_id}").glob("*.csv"))
        if process.returncode != 0 or not csv_files:
            raise RuntimeError(f"执行器退出码 {process.returncode}；CSV 文件 {csv_files}\n{output[-7000:]}")
        with csv_files[0].open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        titles = [row.get("title", "") for row in rows]
        expected = [f"Page {page} Item {item}" for page in (1, 2, 3) for item in (1, 2)]
        if titles != expected:
            raise AssertionError(f"预期 {expected}，实际 {titles}\n{output[-7000:]}")
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps({"executor_exit_code": process.returncode, "rows": rows,
                          "csv": str(csv_files[0])}, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
