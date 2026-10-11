"""Use existing EasySpider storage and its own running design Chrome."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from agent import BROWSER_CLOSED_MESSAGE, _compact, _is_browser_closed_error
from editor import apply_changes, normalize_native_task
from observer import observe


NATIVE_URL = "http://127.0.0.1:8074"
_DRIVER_LOCK = threading.RLock()


def fingerprint(task):
    return hashlib.sha256(json.dumps(task, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def editor_url(task_id, base=NATIVE_URL):
    return base + "/taskGrid/FlowChart_CN.html?" + urllib.parse.urlencode({
        "id": task_id, "wsport": 8084, "backEndAddressServiceWrapper": base,
    })


class NativeSavePending(ValueError):
    """The native server assigned an ID, but persistence is not yet confirmed."""
    def __init__(self, task_id):
        self.task_id = task_id
        super().__init__(f"任务 {task_id} 已提交，但无法确认保存结果；请在原版检查并重新读取，勿重复创建任务")


class NativeClient:
    def __init__(self, base=NATIVE_URL):
        parsed = urllib.parse.urlparse(base)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or parsed.path not in ("", "/") or parsed.username:
            raise ValueError("仅连接本机 EasySpider HTTP 服务")
        self.base = base.rstrip("/")

    def read(self, task_id):
        if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 0:
            raise ValueError("原版任务 ID 无效")
        for attempt in range(8):
            try:
                with urllib.request.urlopen(self.base + f"/queryTask?id={task_id}", timeout=10) as response:
                    task = json.load(response)
                break
            except json.JSONDecodeError:
                # Original fs.writeFile truncates the file before asynchronously
                # filling it. A concurrent query can briefly return empty JSON.
                if attempt == 7:
                    raise ValueError("原版任务内容暂不可读取，请等待 EasySpider 保存完成后重试") from None
                time.sleep(0.1)
            except urllib.error.HTTPError as exc:
                raise ValueError(f"无法读取 EasySpider 任务 {task_id}（HTTP {exc.code}），请检查任务是否已删除") from None
            except urllib.error.URLError:
                raise ValueError("无法连接 EasySpider；请先启动原版程序（本机 8074 端口）") from None
        if not isinstance(task, dict):
            raise ValueError("原版返回的任务格式无效，请检查是否已保存有效流程")
        if task.get("id", task_id) != task_id:
            raise ValueError("原版任务已删除或 ID 不一致")
        return task

    def write(self, task, task_id=None, expected=None):
        if task_id is not None:
            current = self.read(task_id)
            if expected is not None and fingerprint(current) != expected:
                raise ValueError("EasySpider 中的流程已变化，请保存原版改动并重新读取后再修改")
        saved = copy.deepcopy(task)
        saved["id"] = -1 if task_id is None else task_id
        body = urllib.parse.urlencode({"params": json.dumps(saved, ensure_ascii=False)}).encode("utf-8")
        request = urllib.request.Request(self.base + "/manageTask", data=body, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                answer = response.read(50).decode("utf-8").strip()
        except urllib.error.URLError as exc:
            raise ValueError("保存到 EasySpider 失败，请检查原版服务是否运行") from exc
        if not answer.isdecimal() or (task_id is not None and int(answer) != task_id):
            raise ValueError("EasySpider 返回的任务 ID 无效")
        task_id = int(answer)
        saved["id"] = task_id
        # The original server replies before its asynchronous file write ends.
        for attempt in range(12):
            try:
                persisted = self.read(task_id)
                if fingerprint(persisted) == fingerprint(saved):
                    return task_id, persisted
            except ValueError:
                pass
            time.sleep(0.1)
        raise NativeSavePending(task_id)


def _driver_candidates():
    """Inspect only ChromeDrivers whose parent is the original EasySpider app."""
    if os.name != "nt":
        return []
    command = r"""
    $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new();
    $items = @(Get-CimInstance Win32_Process -Filter "Name='chromedriver_win64.exe' OR Name='chromedriver.exe'" | ForEach-Object {
        $driverProcess = $_;
        $ownerProcess = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $driverProcess.ParentProcessId);
        if ($ownerProcess.Name -match '^(electron|EasySpider)\.exe$' -and
            (($ownerProcess.Name -match '^EasySpider\.exe$') -or ($ownerProcess.ExecutablePath -match 'EasySpider')) -and
            $driverProcess.CommandLine -match '--port[= ](\d+)') {
            [PSCustomObject]@{port=[int]$Matches[1]}
        }
    });
    ConvertTo-Json -InputObject $items -Compress
    """
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, encoding="utf-8-sig", errors="replace", timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            return []
        return [f"http://127.0.0.1:{item['port']}" for item in json.loads(result.stdout or "[]")]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        return []


class NativeDesigner:
    """Attach to, but never create or quit, the original design browser."""
    def __init__(self, client, profile):
        self.client = client
        self.profile = Path(profile).resolve()
        self.driver = None
        self.snapshot = None
        self.observation_id = None
        self.snapshot_url = None

    def connect(self):
        from selenium import webdriver
        if self.driver:
            try:
                self.driver.current_url
                return True
            except Exception:
                self.disconnect()
        for address in _driver_candidates():
            address = address.rstrip("/")
            try:
                with urllib.request.urlopen(address + "/sessions", timeout=2) as response:
                    sessions = json.load(response).get("value", [])
                for entry in sessions:
                    caps = entry.get("capabilities", {})
                    folder = caps.get("chrome", {}).get("userDataDir")
                    if not folder or Path(folder).resolve() != self.profile:
                        continue
                    class ExistingDriver(webdriver.Remote):
                        def start_session(attached, _capabilities):
                            attached.session_id = entry["id"]
                            attached.caps = caps
                        def quit(attached):
                            # The original Electron app owns this browser.
                            pass
                    self.driver = ExistingDriver(command_executor=address, options=webdriver.ChromeOptions())
                    self.driver.current_url
                    return True
            except Exception:
                self.driver = None
        return False

    def disconnect(self):
        self.driver = None
        self.snapshot = self.observation_id = self.snapshot_url = None

    def _require(self):
        if not self.driver and not self.connect():
            raise ValueError("请在 EasySpider 首页启动“使用带用户信息浏览器设计”，填写上方显示的资料目录，再点击打开原版设计器")

    def _guard(self, fn):
        with _DRIVER_LOCK:
            try:
                self._require()
                return fn()
            except Exception as exc:
                if _is_browser_closed_error(exc) or type(exc).__name__ in {"MaxRetryError", "NewConnectionError"}:
                    self.disconnect()
                    raise ValueError(BROWSER_CLOSED_MESSAGE + "请重新启动 EasySpider 的设计浏览器。") from None
                raise

    def open(self, task_id, task):
        def action():
            from selenium.webdriver.support.ui import WebDriverWait
            url = self.client.base + "/taskGrid/taskList.html?" + urllib.parse.urlencode({
                "lang": "zh", "wsport": 8084, "backEndAddressServiceWrapper": self.client.base,
            })
            self.driver.get(url)
            WebDriverWait(self.driver, 12).until(lambda browser: browser.execute_script(
                "return typeof app !== 'undefined' && typeof ws !== 'undefined' && ws.readyState === 1;"))
            # Use exactly the same entrypoint as clicking Modify in the native list.
            self.driver.execute_script("app.modifyTask(-1, arguments[0]);", {
                "id": task_id, "links": task.get("links") or task["url"],
            })
            self.snapshot = self.observation_id = None
            return {"active": True, "message": "已打开原版流程图及其设计 Chrome；请在 EasySpider 中修改、调试并保存"}
        return self._guard(action)

    def refresh(self, task_id):
        def action():
            answer = self.driver.execute_async_script("""
                const taskId=arguments[0], done=arguments[arguments.length-1];
                const socket=new WebSocket('ws://127.0.0.1:8084'); let finished=false;
                const finish=value=>{if(finished)return; finished=true;socket.close();done(value);};
                const timer=setTimeout(()=>finish(false),2500);
                socket.onopen=()=>{socket.send(JSON.stringify({type:1,message:{id:taskId}}));
                    setTimeout(()=>{clearTimeout(timer);finish(true);},150);};
                socket.onerror=()=>{clearTimeout(timer);finish(false);};
            """, task_id)
            if not answer:
                raise ValueError("无法刷新原版流程图，请在 EasySpider 中重新打开此任务")
            self.snapshot = self.observation_id = None
            return {"active": True, "message": "已写回并刷新原版流程图"}
        return self._guard(action)

    def observe(self):
        def action():
            self.snapshot = observe(self.driver, with_screenshot=False)
            self.snapshot_url = self.driver.current_url
            self.observation_id = uuid.uuid4().hex
            return {"observation": _compact(self.snapshot), "observation_id": self.observation_id,
                    "active": True, "message": "已读取原版设计 Chrome 的当前网页"}
        return self._guard(action)

    def patch(self, task, operations, observation_id, ai=True):
        def action():
            if not observation_id or observation_id != self.observation_id or self.snapshot is None:
                raise ValueError("原版网页候选已过期，请重新生成修改建议")
            if self.driver.current_url != self.snapshot_url:
                raise ValueError("原版设计 Chrome 已跳转，请重新生成修改建议")
            return apply_changes(task, operations, snapshot=self.snapshot, driver=self.driver,
                                 ai=ai, allow_native=True)
        return self._guard(action)


def read_normalized(client, task_id):
    raw = client.read(task_id)
    return normalize_native_task(raw, allow_native=True), fingerprint(raw)
