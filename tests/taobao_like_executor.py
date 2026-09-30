"""Exercise sales sorting, four list fields, and exactly three native pages locally."""
import csv
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import _bootstrap
from executor_smoke import EXECUTOR, SANDBOX
from workflow import compile_task


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        page = int(query.get("page", ["1"])[0])
        sorted_by_sales = query.get("sort") == ["sales"]
        if parsed.path != "/search" or page not in (1, 2, 3):
            self.send_error(404)
            return
        cards = []
        for item in (1, 2):
            number = (page - 1) * 2 + item
            badge = '<div class="badge">推荐</div>' if number % 2 else ""
            cards.append(
                f'<article class="card">{badge}'
                f'<div class="product-title">羽毛球拍 {number}</div>'
                f'<div class="price"><span>{number}9</span><span>.50</span></div>'
                f'<div class="sales">{700 - number * 50} 人付款</div>'
                f'<div class="shop">店铺 {number}</div></article>')
        next_link = (f'<a rel="next" href="/search?sort=sales&page={page + 1}">下一页</a>'
                     if page < 3 and sorted_by_sales else "")
        body = (f'<!doctype html><html><head><meta charset="utf-8"></head><body>'
                f'<a role="tab" href="/search?sort=sales">销量</a>'
                f'<div id="sort-state">{"sales" if sorted_by_sales else "default"}</div>'
                f'<main>{"".join(cards)}</main><nav>{next_link}</nav></body></html>').encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if sys.exc_info()[0] not in (ConnectionResetError, ConnectionAbortedError):
            super().handle_error(request, client_address)


def run(real_model=False):
    if sys.platform != "win32" or not EXECUTOR.is_file():
        raise RuntimeError("需要 Windows 版 EasySpider 执行器")
    if not (SANDBOX / "EasySpider" / "resources" / "app" / "chrome_win64").is_dir():
        raise RuntimeError("缺少执行器隔离资源目录连接")
    server = QuietServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        task_id = int(time.time() * 1000)
        url = f"http://127.0.0.1:{server.server_port}/search"
        extraction = {
            "mode": "list",
            "list_xpath": '//article[contains(concat(" ",normalize-space(@class)," ")," card ")]',
            "fields": [
                {"name": name, "attribute": "number" if name == "价格" else "text",
                 "xpath": f'//div[@class="{cls}"]',
                 "relative_xpath": f'//div[@class="{cls}"]', "sample": ""}
                for name, cls in (("商品名称", "product-title"), ("价格", "price"),
                                  ("销量", "sales"), ("店铺名称", "shop"))],
            "next_xpath": '//a[@rel="next"]',
        }
        trace = []
        if real_model:
            from agent import explore
            from settings import resolve_model_config

            provider, key, model = resolve_model_config({})
            request = "在当前测试站点采集羽毛球拍商品，按销量排序，采集 3 页，字段为商品名称、价格、销量、店铺名称"
            plan = {"task_name": "本地三页商品采集验证", "start_url": url,
                    "steps": ["按销量排序", "采集商品列表的四个字段", "采集 3 页"],
                    "fields": ["商品名称", "价格", "销量", "店铺名称"],
                    "pagination": "page_limit", "page_limit": 3,
                    "requires_login": False, "requires_detail": False,
                    "output_format": "csv", "deduplicate": False}
            result = explore(request, plan, provider, key, model,
                             lambda message: print(message, file=sys.stderr, flush=True))
            task = result["task"]
            trace = result["trace"]
        else:
            task = compile_task(url, "本地三页商品采集验证", [
                {"action": "click", "xpath": '//a[@role="tab" and normalize-space(.)="销量"]'}
            ], extraction, page_limit=3)
        task.update({"id": task_id, "saveName": "taobao_like_executor", "recordLog": True})
        instances = SANDBOX / "execution_instances"
        instances.mkdir(parents=True, exist_ok=True)
        (instances / f"{task_id}.json").write_text(json.dumps(task, ensure_ascii=False),
                                                      encoding="utf-8")
        (SANDBOX / "config.json").write_text(json.dumps({
            "webserver_address": "http://127.0.0.1", "webserver_port": 8074,
            "user_data_folder": "./user_data", "debug": False,
            "mysql_config_path": "./mysql_config.json",
            "absolute_user_data_folder": str(SANDBOX / "user_data")},
            ensure_ascii=False), encoding="utf-8")
        log_path = SANDBOX / f"taobao_like_{task_id}.log"
        command = [str(EXECUTOR), "--ids", f"[{task_id}]", "--read_type", "local",
                   "--headless", "1", "--keyboard", "0"]
        with log_path.open("wb") as log:
            process = subprocess.Popen(command, cwd=SANDBOX, stdout=log,
                                       stderr=subprocess.STDOUT,
                                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            try:
                process.wait(timeout=150)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait(timeout=10)
                raise RuntimeError(f"执行器超时；日志：{log_path}") from exc
        files = list((SANDBOX / "Data" / f"Task_{task_id}").glob("*.csv"))
        if process.returncode != 0 or len(files) != 1:
            tail = log_path.read_bytes().decode("utf-8", "replace")[-2500:]
            raise RuntimeError(f"执行器退出码 {process.returncode}，CSV 文件 {files}；\n{tail}")
        with files[0].open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        expected_names = [f"羽毛球拍 {number}" for number in range(1, 7)]
        if [row.get("商品名称") for row in rows] != expected_names:
            raise AssertionError(f"三页商品序列不正确：{rows}；日志：{log_path}")
        if any(not all(row.get(name) for name in ("价格", "销量", "店铺名称")) for row in rows):
            raise AssertionError(f"四字段不完整：{rows}；日志：{log_path}")
        if [row["价格"] for row in rows] != [f"{number}9.50" for number in range(1, 7)]:
            raise AssertionError(f"价格拼接不正确：{rows}；日志：{log_path}")
        return {"rows": len(rows), "pages": 3, "first": rows[0], "last": rows[-1],
                "trace": trace, "csv": str(files[0]), "log": str(log_path)}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(run(real_model="--real-model" in sys.argv), ensure_ascii=False, indent=2))
