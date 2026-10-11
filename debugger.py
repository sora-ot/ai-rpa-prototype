"""A serialized Selenium browser for local native-node trial runs."""
import copy
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent import BROWSER_CLOSED_MESSAGE, _is_browser_closed_error
from auth import LoginGate, detect_challenge
from editor import apply_changes, field_attribute
from observer import check_action_target, observe, validate_url
from workflow import validate_task


class BrowserDebugger:
    def __init__(self, start_url):
        self.start_url = start_url
        self.gate = LoginGate(start_url, lambda _: None, lambda: None)
        self.driver = None
        self.snapshot = None
        self.observation_id = None
        self.snapshot_url = None
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rpa-debug")
        self.lock = threading.Lock()
        self.disposed = False

    def _call(self, fn, *args):
        with self.lock:
            return self.worker.submit(self._guard, fn, args).result(timeout=180)

    def _guard(self, fn, args):
        try:
            return fn(*args)
        except Exception as exc:
            service = getattr(self.driver, "service", None)
            process = getattr(service, "process", None)
            driver_stopped = process is not None and process.poll() is not None
            transport_lost = type(exc).__name__ in {"MaxRetryError", "NewConnectionError", "ConnectionRefusedError"}
            if _is_browser_closed_error(exc) or driver_stopped or transport_lost:
                self._close()
                raise ValueError(BROWSER_CLOSED_MESSAGE) from None
            raise

    def start(self):
        return self._call(self._start)

    def _start(self):
        if self.driver:
            return self._status()
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        self.gate.acquire()
        try:
            options = webdriver.ChromeOptions()
            options.add_argument("--window-size=1366,900")
            options.add_argument("--disable-notifications")
            options.add_argument(f"--user-data-dir={self.gate.profile_dir}")
            options.add_argument("--profile-directory=Default")
            if os.environ.get("AI_RPA_DEBUG_HEADLESS") == "1":
                options.add_argument("--headless=new")
            bundled = Path(__file__).resolve().parent.parent / "EasySpider" / "ElectronJS" / "chrome_win64"
            binary = os.environ.get("AI_RPA_CHROME_BINARY")
            driver_path = os.environ.get("AI_RPA_CHROMEDRIVER")
            if os.name == "nt":
                binary = binary or (str(bundled / "chrome.exe") if (bundled / "chrome.exe").is_file() else None)
                driver_path = driver_path or (str(bundled / "chromedriver_win64.exe") if (bundled / "chromedriver_win64.exe").is_file() else None)
            if binary:
                options.binary_location = binary
            self.driver = webdriver.Chrome(service=Service(driver_path) if driver_path else Service(), options=options)
            self.driver.set_page_load_timeout(30)
            self.driver.set_script_timeout(15)
            self.driver.get(validate_url(self.start_url))
            return self._status()
        except Exception:
            self._close()
            raise

    def _require(self):
        if self.driver is None:
            raise ValueError("请先打开调试浏览器")

    def _status(self):
        if self.driver is None:
            return {"active": False}
        url = self.driver.current_url
        reason = detect_challenge(self.driver, self.start_url)
        return {"active": True, "url": url.split("?")[0].split("#")[0],
                "challenge": reason, "profile_dir": str(self.gate.profile_dir)}

    def status(self):
        return self._call(self._status)

    def observe(self):
        return self._call(self._observe)

    def _observe(self):
        self._require()
        state = self._status()
        if state["challenge"]:
            self.snapshot = None
            self.observation_id = None
            return {**state, "observation_id": None, "elements": [], "lists": []}
        self.snapshot = observe(self.driver, with_screenshot=False)
        self.snapshot_url = self.driver.current_url
        self.observation_id = uuid.uuid4().hex
        from agent import _compact
        return {**state, "observation_id": self.observation_id,
                "observation": _compact(self.snapshot),
                "elements": [{"id": e["id"], "tag": e["tag"], "text": e.get("text", ""),
                              "placeholder": e.get("placeholder", ""), "is_next": e.get("is_next", False)}
                             for e in self.snapshot["elements"]],
                "lists": [{"id": g["id"], "count": g["count"], "samples": g.get("samples", [])}
                          for g in self.snapshot["lists"]]}

    def patch(self, task, operations, observation_id, ai=False):
        return self._call(self._patch, task, operations, observation_id, ai)

    def _patch(self, task, operations, observation_id, ai):
        self._require()
        if not observation_id or observation_id != self.observation_id or self.snapshot is None:
            raise ValueError("网页候选已过期，请刷新网页信息")
        if self._status()["challenge"]:
            raise ValueError("请先在浏览器中完成登录或验证")
        # A navigation invalidates all candidate IDs even before the next scan.
        if self.driver.current_url.split("#")[0] != self.snapshot_url.split("#")[0]:
            raise ValueError("网页已跳转，请刷新网页信息")
        return apply_changes(task, operations, snapshot=self.snapshot, driver=self.driver, ai=ai)

    def trial(self, task, node_id, *, replay=False, record_index=0):
        return self._call(self._trial, copy.deepcopy(task), node_id, replay, record_index)

    def _trial(self, task, node_id, replay, record_index):
        from selenium.webdriver.common.by import By
        from selenium.common.exceptions import ElementClickInterceptedException
        self._require()
        validate_task(task)
        graph = task["graph"]
        if isinstance(node_id, bool) or not isinstance(node_id, int) or not 1 <= node_id < len(graph):
            raise ValueError("请选择一个有效操作节点")
        if isinstance(record_index, bool) or not isinstance(record_index, int) or not 0 <= record_index <= 10000:
            raise ValueError("列表项序号无效")
        if self._status()["challenge"]:
            return {**self._status(), "message": "请先在调试浏览器中自行登录，再刷新网页信息"}
        # A partial or intercepted trial can change the DOM without changing
        # the URL. Never keep candidate IDs from before such an attempt.
        self.snapshot = None
        self.observation_id = None
        self.snapshot_url = None
        output = {"executed": [], "samples": []}
        context = {"row": None, "keyword": "", "remaining": 60}

        def prepare_loop(n):
            p = n["parameters"]
            if p.get("loopType") == 1:
                rows = self.driver.find_elements(By.XPATH, p["xpath"])
                if record_index >= len(rows):
                    raise ValueError(f"当前列表有 {len(rows)} 条，所选序号超出范围")
                context["row"] = rows[record_index]
            elif p.get("loopType") == 3:
                context["keyword"] = p.get("textList", "").splitlines()[0]

        def ancestors(n):
            result = []
            while n["parentId"]:
                n = graph[n["parentId"]]
                result.insert(0, n)
            return result

        def target(p):
            if p.get("useLoop") and context["row"] is not None:
                return context["row"].find_element(By.XPATH, "." + p["xpath"])
            return self.driver.find_element(By.XPATH, p["xpath"])

        def fields(n):
            data = {}
            for f in n["parameters"]["params"]:
                path = f["relativeXPath"]
                if f.get("contentType") == 5:
                    data[f["name"]] = self.driver.current_url
                    continue
                try:
                    if f.get("relative"):
                        if context["row"] is None:
                            raise ValueError("相对字段需要先选择列表记录")
                        e = context["row"] if not path else context["row"].find_element(By.XPATH, "." + path)
                    else:
                        e = self.driver.find_element(By.XPATH, path)
                    attr = field_attribute(f)
                    data[f["name"]] = (re.sub(r"[^0-9.]", "", e.get_attribute("textContent") or "")
                                       if attr == "number" else e.text.strip() if attr == "text"
                                       else e.get_attribute(attr) or "")
                except Exception as exc:
                    if _is_browser_closed_error(exc):
                        raise
                    from selenium.common.exceptions import NoSuchElementException
                    if not isinstance(exc, NoSuchElementException):
                        raise
                    data[f["name"]] = f.get("default", "")
            output["samples"].append(data)

        def branch_ok(n):
            p = n["parameters"]
            return p.get("class") == 0 or bool(self.driver.find_elements(By.XPATH, p["value"]))

        def run(n):
            context["remaining"] -= 1
            if context["remaining"] < 0:
                raise ValueError("单次调试超过 60 个节点，请拆分试运行")
            option, p = n["option"], n["parameters"]
            if option == 1:
                url = p.get("links") or p.get("url")
                self.driver.get(validate_url(url.splitlines()[0]))
                context["row"] = None
            elif option in (2, 4, 6):
                e = target(p)
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", e)
                if option == 2:
                    check_action_target({"tag": e.tag_name, "text": e.text, "role": e.get_attribute("role") or "",
                                         "href": e.get_attribute("href") or ""}, "click")
                    before = self.driver.window_handles
                    e.click()
                    if len(self.driver.window_handles) > len(before):
                        self.driver.switch_to.window(self.driver.window_handles[-1])
                elif option == 4:
                    if e.get_attribute("type") == "password":
                        raise ValueError("密码输入须由用户自行完成")
                    value = context["keyword"] if p.get("useLoop") else p["value"]
                    if "<enter>" in value.lower() or "Field[" in value:
                        raise ValueError("调试不执行输入中的特殊指令")
                    from selenium.webdriver.common.keys import Keys
                    if e.tag_name.lower() in ("input", "textarea"):
                        e.clear()
                    else:
                        e.send_keys(Keys.CONTROL, "a")
                        e.send_keys(Keys.BACKSPACE)
                    e.send_keys(value)
                else:
                    from selenium.webdriver.support.ui import Select
                    Select(e).select_by_visible_text(p["optionValue"])
            elif option == 3:
                fields(n)
            elif option == 8:
                saved_row, saved_keyword = context["row"], context["keyword"]
                original_handle = self.driver.current_window_handle
                original_url = self.driver.current_url
                original_handles = set(self.driver.window_handles)
                prepare_loop(n)
                for i in n["sequence"]:
                    run(graph[i])
                if p.get("loopType") == 1:
                    for handle in set(self.driver.window_handles) - original_handles:
                        self.driver.switch_to.window(handle)
                        self.driver.close()
                    self.driver.switch_to.window(original_handle)
                    if self.driver.current_url != original_url:
                        self.driver.get(original_url)
                context["row"], context["keyword"] = saved_row, saved_keyword
            elif option == 9:
                for i in n["sequence"]:
                    if branch_ok(graph[i]):
                        run(graph[i])
                        break
            elif option == 10:
                if branch_ok(n):
                    for i in n["sequence"]:
                        run(graph[i])
            else:
                raise ValueError("该节点不支持局部试运行")
            if p.get("scrollType") == 3:
                from agent import _scroll_until_end
                _scroll_until_end(self.driver)
            if option in (1, 2, 4, 6):
                reason = detect_challenge(self.driver, self.start_url)
                if reason:
                    raise ValueError(reason + "；请在调试浏览器中自行登录后刷新网页信息")
            output["executed"].append(n["index"])
            time.sleep(min(float(p.get("wait", 0)), 3))

        n = graph[node_id]
        try:
            if replay:
                chain = [graph[0], *ancestors(n), n]
                for parent, child in zip(chain, chain[1:]):
                    if parent["option"] == 8:
                        prepare_loop(parent)
                    if parent["option"] == 10 and not branch_ok(parent):
                        raise ValueError("所选条件分支在当前页面不成立")
                    for sibling in parent["sequence"]:
                        if sibling == child["index"]:
                            break
                        # A judge selects one branch; other branches are alternatives.
                        if parent["option"] != 9:
                            run(graph[sibling])
            else:
                for parent in ancestors(n):
                    if parent["option"] == 8:
                        prepare_loop(parent)
            run(n)
        except ElementClickInterceptedException:
            return {**self._status(), **output,
                    "message": "点击被弹窗或登录遮罩拦截；请在浏览器中处理后重试"}
        self.snapshot = None
        self.observation_id = None
        return {**self._status(), **output, "message": "试运行完成；循环仅运行一轮，列表使用所选记录"}

    def highlight(self, task, node_id, record_index=0):
        return self._call(self._highlight, task, node_id, record_index)

    def _highlight(self, task, node_id, record_index):
        from selenium.webdriver.common.by import By
        self._require()
        validate_task(task)
        if not isinstance(node_id, int) or not 1 <= node_id < len(task["graph"]):
            raise ValueError("节点不存在")
        n = task["graph"][node_id]
        if n["option"] == 3:
            fields = n["parameters"]["params"]
            if fields[0].get("relative"):
                parent = task["graph"][n["parentId"]]
                path = parent["parameters"]["xpath"]
            else:
                path = fields[0]["relativeXPath"]
        else:
            path = n["parameters"].get("xpath", "")
        if not isinstance(record_index, int) or isinstance(record_index, bool) or record_index < 0:
            raise ValueError("列表项序号无效")
        if not path:
            return {"message": "该节点没有可高亮的网页定位器"}
        if n["parameters"].get("useLoop"):
            parent = task["graph"][n["parentId"]]
            while parent["index"] and not (parent["option"] == 8 and parent["parameters"].get("loopType") == 1):
                parent = task["graph"][parent["parentId"]]
            rows = self.driver.find_elements(By.XPATH, parent["parameters"].get("xpath") or "//body")
            elements = rows[min(record_index, len(rows)-1)].find_elements(By.XPATH, "."+path) if rows else []
        else:
            elements = self.driver.find_elements(By.XPATH, path)
        if not elements:
            raise ValueError("当前页面没有匹配元素，请先试运行前置步骤")
        e = elements[min(record_index, len(elements) - 1)]
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].style.outline='3px solid #e14b32';", e)
        return {"message": f"已高亮元素，共匹配 {len(elements)} 个"}

    def _close(self):
        try:
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
        finally:
            self.driver = self.snapshot = self.observation_id = None
            self.snapshot_url = None
            self.gate.release()
        return {"active": False, "message": "调试浏览器已关闭"}

    def close(self):
        return self._call(self._close)

    def dispose(self):
        if self.disposed:
            return
        self.close()
        self.disposed = True
        self.worker.shutdown(wait=False, cancel_futures=True)
