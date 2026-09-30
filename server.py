"""Local test UI and isolated workflow export service."""
import argparse
import json
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
if (BASE / ".deps").is_dir():
    sys.path.insert(0, str(BASE / ".deps"))

from agent import BROWSER_CLOSED_MESSAGE, BrowserClosedError, explore, make_plan
from auth import LoginGate
from settings import (MAX_PLAN_TURNS, MAX_USER_MESSAGE_CHARS, max_actions,
                      public_config, resolve_model_config)
from workflow import validate_task


GENERATED = BASE / "generated"
SESSIONS = {}
LOGIN_GATES = {}
LOCK = threading.RLock()


def validate_plan_task(plan, task):
    expected = (plan.get("pagination") == "until_end" or
                (plan.get("pagination") == "page_limit" and plan.get("page_limit", 2) != 1))
    actual = any(node["option"] == 2 and node["title"] == "点击下一页"
                 for node in task.get("graph", []))
    if expected != actual:
        raise ValueError("生成的任务与确认计划的翻页方式不一致，请重新探索后再保存")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def _json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise ValueError("请求必须为 JSON")
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > 20000:
            raise ValueError("请求体大小无效")
        return json.loads(self.rfile.read(size))

    def _session(self, data):
        sid = data.get("session")
        with LOCK:
            if not sid or sid not in SESSIONS:
                raise ValueError("会话无效，请重新规划")
            return SESSIONS[sid]

    def do_GET(self):
        path = urllib.parse.urlparse(self.path)
        if path.path == "/":
            body = (BASE / "index.html").read_bytes()
            mime = "text/html; charset=utf-8"
        elif path.path == "/app.js":
            body = (BASE / "app.js").read_bytes()
            mime = "application/javascript; charset=utf-8"
        elif path.path == "/api/state":
            params = urllib.parse.parse_qs(path.query)
            try:
                session = self._session({"session": params.get("session", [""])[0]})
                with LOCK:
                    return self._json(200, {k: v for k, v in session.items() if k != "history"})
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
        elif path.path == "/api/config":
            return self._json(200, public_config())
        else:
            return self._json(404, {"error": "路径不存在"})
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                raise ValueError("仅接受本地页面请求")
            data = self._read()
            if self.path == "/api/plan":
                message = data.get("message")
                if not isinstance(message, str) or not message.strip():
                    raise ValueError("请输入采集需求或修改意见")
                message = message.strip()
                if len(message) > MAX_USER_MESSAGE_CHARS:
                    raise ValueError(f"每条消息最多 {MAX_USER_MESSAGE_CHARS} 字")
                previous = self._session(data) if data.get("session") else None
                with LOCK:
                    if previous and previous["status"] in ("running", "planning", "awaiting_login"):
                        raise ValueError("当前任务正在处理，请等待完成后再修改")
                    history = list(previous["history"]) if previous else []
                    if len(history) >= MAX_PLAN_TURNS:
                        raise ValueError(f"本次对话最多规划 {MAX_PLAN_TURNS} 轮，请确认流程或创建新任务")
                    previous_status = previous["status"] if previous else None
                    if previous:
                        previous["status"] = "planning"
                try:
                    provider, key, model = resolve_model_config(data)
                    plan = make_plan(message, "", provider, key, model, history)
                except Exception:
                    if previous:
                        with LOCK:
                            previous["status"] = previous_status
                    raise
                sid = data.get("session") or uuid.uuid4().hex
                with LOCK:
                    updated_history = [*history, {"request": message, "url_hint": "",
                                                  "answers": [], "plan": plan}]
                    messages = list(previous["messages"]) if previous else []
                    messages.extend([{"role": "user", "content": message},
                                     {"role": "assistant", "plan": plan}])
                    SESSIONS[sid] = {"status": "clarifying" if plan["needs_clarification"] else "planned",
                                     "plan": plan, "events": [], "messages": messages,
                                     "history": updated_history, "revision": len(updated_history)}
                return self._json(200, {"session": sid, "plan": plan,
                                        "messages": messages, "revision": len(updated_history)})
            if self.path == "/api/run":
                session = self._session(data)
                provider, key, model = resolve_model_config(data)
                action_budget = max_actions()
                with LOCK:
                    if session["status"] != "planned":
                        raise ValueError("请先确认新计划")
                    session["status"] = "running"
                    session["events"].append("用户已确认计划")

                def progress(message):
                    with LOCK:
                        session["events"].append(message)

                def on_wait(reason):
                    with LOCK:
                        session["status"] = "awaiting_login"
                        session["events"].append(reason + "。请在打开的浏览器中自行登录，然后点击“已完成登录”。")

                def on_resume():
                    with LOCK:
                        session["status"] = "running"
                        session["events"].append("已验证登录状态，继续试运行")

                gate = LoginGate(session["plan"]["start_url"], on_wait, on_resume)
                with LOCK:
                    LOGIN_GATES[data["session"]] = gate

                def work():
                    try:
                        request_context = json.dumps({
                            "requirements_history": [
                                {"request": item["request"], "url_hint": item.get("url_hint", ""),
                                 "answers": item.get("answers", [])}
                                for item in session["history"]],
                            "confirmed_plan": session["plan"]}, ensure_ascii=False)
                        result = explore(request_context, session["plan"], provider,
                                         key, model, progress, max_actions=action_budget,
                                         login_gate=gate)
                        with LOCK:
                            session.update({"status": "preview", "result": result})
                    except BrowserClosedError:
                        with LOCK:
                            session.update({"status": "closed", "error": BROWSER_CLOSED_MESSAGE})
                            session["events"].append(BROWSER_CLOSED_MESSAGE)
                    except Exception as exc:
                        with LOCK:
                            session.update({"status": "error", "error": str(exc)})
                    finally:
                        with LOCK:
                            LOGIN_GATES.pop(data["session"], None)

                threading.Thread(target=work, daemon=True).start()
                return self._json(200, {"status": "running"})
            if self.path in ("/api/login-complete", "/api/cancel-login"):
                session = self._session(data)
                with LOCK:
                    gate = LOGIN_GATES.get(data["session"])
                    if session["status"] != "awaiting_login" or not gate:
                        raise ValueError("当前没有等待登录的浏览器")
                    if self.path == "/api/login-complete":
                        gate.complete()
                    else:
                        gate.cancel()
                return self._json(200, {"status": "verifying"})
            if self.path == "/api/save":
                session = self._session(data)
                with LOCK:
                    if session["status"] not in ("preview", "saved"):
                        raise ValueError("请先查看并确认试采样例")
                    task = session["result"]["task"]
                    validate_plan_task(session["plan"], task)
                    validate_task(task)
                    GENERATED.mkdir(exist_ok=True)
                    filename = f"easyspider_ai_{data['session']}.json"
                    (GENERATED / filename).write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
                    session.update({"status": "saved", "file": str(GENERATED / filename)})
                return self._json(200, {"file": session["file"]})
            if self.path == "/api/import":
                session = self._session(data)
                if session["status"] != "saved":
                    raise ValueError("请先确认样例并保存流程")
                validate_plan_task(session["plan"], session["result"]["task"])
                target = str(data.get("easyspider_url") or "http://127.0.0.1:8074").rstrip("/")
                if target not in ("http://127.0.0.1:8074", "http://localhost:8074"):
                    raise ValueError("仅支持本机 EasySpider 8074 端口")
                task = dict(session["result"]["task"])
                task["id"] = -1
                body = urllib.parse.urlencode({"params": json.dumps(task, ensure_ascii=False)}).encode("utf-8")
                req = urllib.request.Request(target + "/manageTask", data=body, method="POST")
                with urllib.request.urlopen(req, timeout=10) as response:
                    task_id = response.read(50).decode("utf-8").strip()
                if not task_id.isdecimal():
                    raise RuntimeError("EasySpider 未返回有效任务 ID")
                with LOCK:
                    session["imported_id"] = int(task_id)
                return self._json(200, {"task_id": int(task_id)})
            return self._json(404, {"error": "路径不存在"})
        except Exception as exc:
            return self._json(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"AI RPA prototype: http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
