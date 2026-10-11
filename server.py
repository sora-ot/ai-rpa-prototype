"""Local test UI and isolated workflow export service."""
import argparse
import atexit
import copy
import json
import os
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
from auth import LoginGate, profile_path
from settings import (MAX_PLAN_TURNS, MAX_USER_MESSAGE_CHARS, max_actions,
                      public_config, resolve_model_config)
from workflow import validate_task
from editor import (MAX_EDIT_HISTORY, apply_changes, changes_between,
                    normalize_native_task, pagination_info, propose_changes, validate_native_task)
from debugger import BrowserDebugger
from native_bridge import NativeClient, NativeDesigner, NativeSavePending, editor_url, fingerprint, read_normalized


GENERATED = BASE / "generated"
SESSIONS = {}
LOGIN_GATES = {}
DEBUGGERS = {}
NATIVE_DESIGNERS = {}
LOCK = threading.RLock()


def validate_plan_task(plan, task):
    expected = (plan.get("pagination") == "until_end" or
                (plan.get("pagination") == "page_limit" and plan.get("page_limit", 2) != 1))
    actual = pagination_info(task)["pagination"] != "none"
    if expected != actual:
        raise ValueError("生成的任务与确认计划的翻页方式不一致，请重新探索后再保存")


def public_session(session):
    value = {k: v for k, v in session.items() if k not in (
        "history", "undo_stack", "edit_dialogue", "edit_busy", "native_fingerprint")}
    if value.get("pending_edit"):
        value["pending_edit"] = {k: v for k, v in value["pending_edit"].items()
                                 if k not in ("candidate_task", "source_fingerprint")}
    value["undo_count"] = len(session.get("undo_stack", []))
    return value


def commit_edit(session, task, message):
    previous = copy.deepcopy({"result": session["result"], "plan": session["plan"]})
    undo = session.setdefault("undo_stack", [])
    undo.append(previous)
    del undo[:-20]
    session["edit_version"] = session.get("edit_version", 1) + 1
    update_edited_result(session, task)
    session.setdefault("edit_dialogue", []).append({"accepted": message})
    session["pending_edit"] = None
    session["events"].append(message)


def update_edited_result(session, task):
    info = pagination_info(task)
    mapping = [{"name": f["name"], "xpath": f["relativeXPath"]}
               for n in task["graph"] if n["option"] == 3
               for f in n["parameters"]["params"]]
    session["result"].update({"task": task, "samples": [], "samples_stale": True,
        "mapping": list({item["name"]: item for item in mapping}.values()),
        "pagination": info["pagination"], "page_limit": info["page_limit"],
        "next_xpath": info["next_xpath"], "edit_version": session["edit_version"]})
    session["plan"].update({"fields": [f["name"] for f in task["outputParameters"]],
        "start_url": task["url"], "pagination": info["pagination"], "page_limit": info["page_limit"],
        "output_format": task["outputFormat"], "task_name": task["name"]})
    session["status"] = "preview"
    session.pop("file", None)


def native_client():
    return NativeClient()


def native_designer(sid, session):
    folder = profile_path(session["result"]["task"]["url"])
    attached = NATIVE_DESIGNERS.get(sid)
    if attached is None or attached.profile != folder.resolve():
        if attached:
            attached.disconnect()
        attached = NATIVE_DESIGNERS[sid] = NativeDesigner(native_client(), folder)
    return attached


def native_metadata(session, message=None, active=None):
    task_id = session["imported_id"]
    folder = profile_path(session["result"]["task"]["url"])
    session.setdefault("native", {}).update({"task_id": task_id, "editor_url": editor_url(task_id),
        "profile_dir": str(folder), "profile_relative_source": os.path.relpath(folder, BASE.parent / "EasySpider" / "ElectronJS")})
    if message is not None:
        session["native"]["message"] = message
    if active is not None:
        session["native"]["active"] = active


def sync_native(session):
    task_id = session.get("imported_id")
    if not isinstance(task_id, int):
        raise ValueError("请先点击“在 EasySpider 原版设计器打开流程”")
    task, source = read_normalized(native_client(), task_id)
    if task != session["result"]["task"] or (session.get("native_fingerprint") is not None and
                                             source != session["native_fingerprint"]):
        commit_edit(session, task, "已读取 EasySpider 中最新保存的流程")
    session["native_fingerprint"] = source
    native_metadata(session, "已读取原版已保存流程；AI 将基于这份图修改")
    return task


def refresh_native(sid, session):
    attached = native_designer(sid, session)
    try:
        outcome = attached.refresh(session["imported_id"])
        native_metadata(session, outcome["message"], True)
    except Exception as exc:
        native_metadata(session, "任务已写回原版，设计器尚未刷新：" + str(exc), False)


def handle_editor(path, data, session):
    sid = data["session"]
    with LOCK:
        if session["status"] not in ("preview", "saved") or not session.get("result", {}).get("task"):
            raise ValueError("请先完成探索，生成流程图")
        if session.get("edit_busy"):
            raise ValueError("正在处理此流程的修改或调试，请稍候")
        version = session.get("edit_version", 1)
        if path in ("patch", "propose", "apply", "discard", "undo", "pull-native", "native/open", "native/read", "native/observe", "debug/trial", "debug/highlight") and (
                isinstance(data.get("version"), bool) or data.get("version") != version):
            raise ValueError("流程版本已变化，请刷新后重试")
        session["edit_busy"] = True
        task = copy.deepcopy(session["result"]["task"])
    try:
        if path == "patch":
            if session.get("native"):
                raise ValueError("当前流程由 EasySpider 原版设计器管理，请在原版进行手工修改并保存")
            operations = data.get("operations")
            if not isinstance(operations, list) or not all(isinstance(op, dict) for op in operations):
                raise ValueError("修改操作须为 JSON 对象数组")
            if any(op.get("candidate_id") for op in operations or []):
                debugger = DEBUGGERS.get(sid)
                if not debugger:
                    raise ValueError("请先打开调试浏览器")
                edited = debugger.patch(task, operations, data.get("observation_id"))
            else:
                edited = apply_changes(task, operations)
            with LOCK:
                commit_edit(session, edited, "已应用手动流程修改")
        elif path == "propose":
            if session.get("edit_turns", 0) >= MAX_EDIT_HISTORY:
                raise ValueError("局部修改对话已达 15 轮；请应用现有建议或手动编辑")
            provider, key, model = resolve_model_config(data)
            if session.get("native"):
                task = sync_native(session)
                version = session["edit_version"]
                debugger = native_designer(sid, session)
                observed = debugger.observe() if debugger.connect() else None
            else:
                debugger = DEBUGGERS.get(sid)
                observed = debugger.observe() if debugger and debugger.driver else None
            proposal = propose_changes(task, data.get("message"), provider, key, model,
                session.get("edit_dialogue", []), observed.get("observation") if observed else None,
                data.get("selected_node"))
            operations = proposal["operations"]
            edited = None
            if operations:
                if any(op.get("candidate_id") for op in operations):
                    if not debugger or not observed or not observed.get("observation_id"):
                        raise ValueError("AI 修改涉及网页元素，请先打开设计浏览器并完成登录")
                    edited = debugger.patch(task, operations, observed["observation_id"], ai=True)
                else:
                    edited = apply_changes(task, operations, ai=True, allow_native=bool(session.get("native")))
            elif not proposal["questions"]:
                raise ValueError("AI 没有给出修改或具体问题，请补充要求")
            with LOCK:
                proposal.update({"version": version, "proposal_id": uuid.uuid4().hex, "candidate_task": edited,
                                 "changes": changes_between(task, edited) if edited else []})
                if session.get("native"):
                    proposal["source_fingerprint"] = session["native_fingerprint"]
                session["pending_edit"] = proposal
                session["edit_turns"] = session.get("edit_turns", 0) + 1
                session.setdefault("edit_dialogue", []).extend([
                    {"request": data["message"]}, {"summary": proposal["summary"], "questions": proposal["questions"]}])
                session["messages"].extend([{"role": "user", "content": data["message"]},
                                            {"role": "assistant", "edit": {k: v for k, v in proposal.items()
                                                if k not in ("candidate_task", "source_fingerprint")}}])
        elif path == "apply":
            proposal = session.get("pending_edit")
            if not proposal or proposal["version"] != version or not proposal.get("candidate_task") or proposal["proposal_id"] != data.get("proposal_id"):
                raise ValueError("没有可应用的当前版本建议")
            if proposal.get("source_fingerprint"):
                validate_native_task(proposal["candidate_task"])
                _, persisted = native_client().write(proposal["candidate_task"], session["imported_id"],
                    expected=proposal["source_fingerprint"])
                proposal["candidate_task"]["id"] = session["imported_id"]
                session["native_fingerprint"] = fingerprint(persisted)
            else:
                validate_task(proposal["candidate_task"])
            with LOCK:
                commit_edit(session, proposal["candidate_task"], "已确认 AI 局部修改：" + proposal["summary"])
            if session.get("native"):
                refresh_native(sid, session)
        elif path == "discard":
            with LOCK:
                if (session.get("pending_edit") or {}).get("proposal_id") != data.get("proposal_id"):
                    raise ValueError("修改建议已变化，请刷新后重试")
                session["pending_edit"] = None
        elif path == "undo":
            if session.get("native") and session.get("undo_stack"):
                restored = session["undo_stack"][-1]["result"]["task"]
                _, persisted = native_client().write(restored, session["imported_id"],
                    expected=session["native_fingerprint"])
                restored["id"] = session["imported_id"]
                session["native_fingerprint"] = fingerprint(persisted)
            with LOCK:
                if not session.get("undo_stack"):
                    raise ValueError("没有可撤销的修改")
                previous = session["undo_stack"].pop()
                session["edit_version"] = version + 1
                session["plan"] = previous["plan"]
                session["result"] = previous["result"]
                session["result"]["edit_version"] = version + 1
                session["status"] = "preview"
                session["pending_edit"] = None
                session.pop("file", None)
                session.setdefault("edit_dialogue", []).append({"accepted": "用户撤销了上一版修改，以当前图为准"})
                session["events"].append("已撤销上一版流程修改；浏览器状态未撤销")
            if session.get("native"):
                refresh_native(sid, session)
        elif path in ("pull-native", "native/read"):
            sync_native(session)
        elif path == "native/open":
            debugger = DEBUGGERS.pop(sid, None)
            if debugger:
                debugger.dispose()
            if isinstance(session.get("imported_id"), int):
                task = sync_native(session)
            else:
                validate_task(task)
                try:
                    task_id, persisted = native_client().write(task)
                except NativeSavePending as exc:
                    # Retain the assigned ID so a retry never creates a second task.
                    task["id"] = session["imported_id"] = exc.task_id
                    commit_edit(session, task, str(exc))
                    native_metadata(session, str(exc), False)
                    return public_session(session)
                task["id"] = task_id
                commit_edit(session, task, f"已将流程连接到 EasySpider 任务 {task_id}")
                session["imported_id"] = task_id
                session["imported_version"] = session["edit_version"]
                session["native_fingerprint"] = fingerprint(persisted)
                native_metadata(session)
            attached = native_designer(sid, session)
            try:
                outcome = attached.open(session["imported_id"], task)
                native_metadata(session, outcome["message"], True)
            except Exception as exc:
                native_metadata(session, f"任务 {session['imported_id']} 已在 EasySpider 中。" + str(exc), False)
        elif path == "native/observe":
            if not session.get("native"):
                raise ValueError("请先打开原版设计器")
            outcome = native_designer(sid, session).observe()
            native_metadata(session, outcome["message"], True)
            return {**outcome, "state": public_session(session)}
        elif path.startswith("debug/"):
            action = path.split("/", 1)[1]
            debugger = DEBUGGERS.get(sid)
            if action == "open":
                if debugger is None:
                    debugger = DEBUGGERS[sid] = BrowserDebugger(task["url"])
                outcome = debugger.start()
            elif action == "close":
                outcome = debugger.close() if debugger else {"active": False}
            elif not debugger:
                raise ValueError("请先打开调试浏览器")
            elif action == "observe":
                outcome = debugger.observe()
            elif action == "trial":
                outcome = debugger.trial(task, data.get("node_id"), replay=data.get("replay") is True,
                                         record_index=data.get("record_index", 0))
            elif action == "highlight":
                outcome = debugger.highlight(task, data.get("node_id"), data.get("record_index", 0))
            else:
                raise ValueError("未知调试操作")
            with LOCK:
                session["debug"] = {k: v for k, v in outcome.items() if k != "observation"}
            return outcome
        else:
            raise ValueError("未知流程编辑操作")
        new_url = session["result"]["task"]["url"]
        debugger = DEBUGGERS.get(sid)
        new_profile = profile_path(new_url)
        if debugger:
            if debugger.gate.profile_dir != new_profile:
                debugger.dispose()
                DEBUGGERS.pop(sid, None)
                session["debug"] = {"active": False, "message": "目标站点已变化，请重新打开调试浏览器"}
            else:
                debugger.start_url = new_url
        session["result"]["profile_dir"] = str(new_profile)
        session["result"]["profile_relative_source"] = os.path.relpath(
            new_profile, BASE.parent / "EasySpider" / "ElectronJS")
        with LOCK:
            return public_session(session)
    except Exception:
        debugger = DEBUGGERS.get(sid)
        if debugger and debugger.driver is None:
            session["debug"] = {"active": False}
        raise
    finally:
        with LOCK:
            session["edit_busy"] = False


def close_debuggers():
    for debugger in list(DEBUGGERS.values()):
        debugger.dispose()
    for attached in NATIVE_DESIGNERS.values():
        attached.disconnect()


atexit.register(close_debuggers)


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
        elif path.path == "/editor.js":
            body = (BASE / "editor.js").read_bytes()
            mime = "application/javascript; charset=utf-8"
        elif path.path == "/flowchart.js":
            body = (BASE / "flowchart.js").read_bytes()
            mime = "application/javascript; charset=utf-8"
        elif path.path == "/flowchart.css":
            body = (BASE / "flowchart.css").read_bytes()
            mime = "text/css; charset=utf-8"
        elif path.path == "/api/state":
            params = urllib.parse.parse_qs(path.query)
            try:
                session = self._session({"session": params.get("session", [""])[0]})
                with LOCK:
                    return self._json(200, public_session(session))
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
            if self.path.startswith("/api/editor/"):
                return self._json(200, handle_editor(self.path[len("/api/editor/"):], data, self._session(data)))
            if self.path == "/api/plan":
                message = data.get("message")
                if not isinstance(message, str) or not message.strip():
                    raise ValueError("请输入采集需求或修改意见")
                message = message.strip()
                if len(message) > MAX_USER_MESSAGE_CHARS:
                    raise ValueError(f"每条消息最多 {MAX_USER_MESSAGE_CHARS} 字")
                previous = self._session(data) if data.get("session") else None
                with LOCK:
                    if previous and (previous["status"] in ("running", "planning", "awaiting_login") or previous.get("edit_busy")):
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
                            session.update({"status": "preview", "result": result, "edit_version": 1,
                                            "undo_stack": [], "edit_turns": 0})
                            result["edit_version"] = 1
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
                    if session.get("edit_busy"):
                        raise ValueError("请等待流程修改或调试完成后保存")
                    task = session["result"]["task"]
                    if session.get("native"):
                        task = sync_native(session)
                        validate_native_task(task)
                    else:
                        validate_plan_task(session["plan"], task)
                        validate_task(task)
                    GENERATED.mkdir(exist_ok=True)
                    filename = f"easyspider_ai_{data['session']}.json"
                    (GENERATED / filename).write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
                    session.update({"status": "saved", "file": str(GENERATED / filename)})
                return self._json(200, {"file": session["file"], "state": public_session(session)})
            if self.path == "/api/import":
                session = self._session(data)
                target = str(data.get("easyspider_url") or "http://127.0.0.1:8074").rstrip("/")
                if target not in ("http://127.0.0.1:8074", "http://localhost:8074"):
                    raise ValueError("仅支持本机 EasySpider 8074 端口")
                state = handle_editor("native/open", {**data, "version": data.get("version", session.get("edit_version", 1))}, session)
                return self._json(200, {"task_id": session["imported_id"], "state": state})
            return self._json(404, {"error": "路径不存在"})
        except Exception as exc:
            return self._json(400, {"error": str(exc) or "处理请求失败，请重新读取流程和网页信息后再试"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"AI RPA prototype: http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
