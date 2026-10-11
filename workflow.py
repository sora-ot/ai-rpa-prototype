"""Compile a small grounded action language into native EasySpider task JSON."""
from datetime import datetime, timezone
from lxml import etree

TITLE_SCRIPT = 'return arguments[0].getAttribute("title") || "";'
NUMBER_SCRIPT = 'return (arguments[0].textContent || "").replace(/[^0-9.]/g, "");'
SAFE_FIELD_SCRIPTS = (TITLE_SCRIPT, NUMBER_SCRIPT)


def _base(xpath="", wait=0):
    return {"history": 1, "tabIndex": 0, "useLoop": False, "xpath": xpath,
            "iframe": False, "wait": wait, "waitType": 0,
            "beforeJS": "", "beforeJSWaitTime": 0,
            "afterJS": "", "afterJSWaitTime": 0,
            "waitElement": "", "waitElementTime": 10, "waitElementIframeIndex": 0}


def _field_param(field, in_list):
    attr = field["attribute"]
    if attr not in ("text", "href", "src", "value", "title", "number"):
        raise ValueError("仅支持提取文本、href、src、value、title 和 number")
    node_type = {"text": 0, "href": 2, "src": 4, "value": 3, "title": 0, "number": 0}[attr]
    script = TITLE_SCRIPT if attr == "title" else (NUMBER_SCRIPT if attr == "number" else "")
    return {"nodeType": node_type, "contentType": 9 if script else (0 if attr == "text" else 1),
            "relative": in_list, "name": field["name"], "desc": "",
            "extractType": 0, "relativeXPath": field["relative_xpath"] if in_list else field["xpath"],
            "recordASField": 1, "paraType": "text", "splitLine": 0,
            "allXPaths": [], "exampleValues": [{"num": 0, "value": field.get("sample", "")}],
            "default": field.get("default", ""), "beforeJS": "", "beforeJSWaitTime": 0,
            "JS": script, "JSWaitTime": 0, "afterJS": "", "afterJSWaitTime": 0,
            "downloadPic": 0, "iframe": False}


def _node(graph, option, title, params, parent=0):
    index = len(graph)
    ancestor = parent
    in_loop = False
    while ancestor:
        if graph[ancestor]["option"] == 8:
            in_loop = True
            break
        ancestor = graph[ancestor]["parentId"]
    n = {"id": index, "index": index, "parentId": parent,
         "type": {8: 1, 9: 2, 10: 3}.get(option, 0), "option": option, "title": title,
         "sequence": [], "isInLoop": in_loop, "position": len(graph[parent]["sequence"]),
         "parameters": params}
    graph.append(n)
    graph[parent]["sequence"].append(index)
    return index


def _extract_nodes(graph, extraction, parent=0):
    if extraction["mode"] == "list":
        p = _base(extraction["list_xpath"])
        # Original executor keeps the wait settings only when this field is
        # truthy before int conversion: string "0" means the main document.
        p.update({"waitElement": extraction.get("ready_xpath", ""), "waitElementTime": 15,
                  "waitElementIframeIndex": "0"})
        p.update({"loopType": 1, "pathList": "", "textList": "", "code": "",
                  "waitTime": 0, "exitCount": 0, "historyWait": 2,
                  "breakMode": 0, "breakCode": "", "breakCodeWaitTime": 0,
                  "scrollType": 0, "scrollCount": 1, "scrollWaitTime": 1,
                  "skipCount": 0, "allXPaths": ""})
        parent = _node(graph, 8, "循环列表项", p, parent)
    p = _base()
    if extraction["mode"] != "list":
        p.update({"waitElement": extraction.get("ready_xpath", ""), "waitElementTime": 15,
                  "waitElementIframeIndex": "0"})
    p["params"] = [_field_param(f, extraction["mode"] == "list") for f in extraction["fields"]]
    p.update({"clear": 1 if extraction.get("detail") else 0,
              "newLine": 0 if extraction.get("detail") else 1})
    _node(graph, 3, "提取列表字段" if extraction.get("detail") else "提取数据", p, parent)
    if extraction.get("detail"):
        detail = extraction["detail"]
        click = _base(detail["link_relative_xpath"], 1)
        click.update({"useLoop": True, "newTab": int(bool(detail.get("new_tab"))),
                      "scrollType": 0, "scrollCount": 1, "scrollWaitTime": 1,
                      "clickWay": 0, "maxWaitTime": 15, "params": [], "allXPaths": [],
                      "alertHandleType": 0})
        _node(graph, 2, "打开当前列表项详情", click, parent)
        p = _base()
        p["params"] = [_field_param(f, False) for f in detail["fields"]]
        p.update({"clear": 0, "newLine": 1})
        _node(graph, 3, "提取详情字段并生成数据行", p, parent)


def _extract_excel_single_with_retry(graph, extraction, parent, url):
    """Retry transient pages, then write an identifiable failure row if still unavailable."""
    required = ([field["xpath"] for field in extraction["fields"] if not field.get("default")]
                or [extraction["fields"][0]["xpath"]])
    ready = "//body[" + " and ".join(f"count({xpath}) > 0" for xpath in required) + "]"
    for attempt in range(2):
        judge = _node(graph, 9, f"检查第 {attempt + 1} 次页面加载", _base(), parent)
        good = _base()
        good.update({"class": 2, "value": ready, "code": "", "waitTime": 0})
        _node(graph, 10, "所需字段已出现", good, judge)
        fallback = _base()
        fallback.update({"class": 0, "value": "", "code": "", "waitTime": 0})
        retry = _node(graph, 10, "页面不完整，刷新重试", fallback, judge)
        refresh = _base(wait=1)
        refresh.update({"url": url, "links": url, "maxWaitTime": 15,
                        "cookies": "", "scrollType": 0, "scrollCount": 1,
                        "scrollWaitTime": 1})
        _node(graph, 1, "重新打开当前网址", refresh, retry)
    judge = _node(graph, 9, "最终检查页面字段", _base(), parent)
    good = _base()
    good.update({"class": 2, "value": ready, "code": "", "waitTime": 0})
    success = _node(graph, 10, "字段齐全，提取数据", good, judge)
    _extract_nodes(graph, extraction, success)
    fallback = _base()
    fallback.update({"class": 0, "value": "", "code": "", "waitTime": 0})
    failure = _node(graph, 10, "仍未加载，记录失败网址", fallback, judge)
    fields = []
    for index, field in enumerate(extraction["fields"]):
        parameter = _field_param(field, False)
        if index == 0:
            parameter.update({"nodeType": 0, "contentType": 5,
                              "relativeXPath": "//body", "JS": ""})
        else:
            parameter.update({"nodeType": 0, "contentType": 0,
                              "relativeXPath": "//__ai_rpa_missing__",
                              "default": "ERROR: required fields missing after 3 attempts", "JS": ""})
        fields.append(parameter)
    p = _base()
    p.update({"params": fields, "clear": 1, "newLine": 1})
    _node(graph, 3, "记录失败网址与错误", p, failure)


def compile_task(url, name, actions, extraction, *, keywords=None,
                 output_format="csv", input_excel="", remove_duplicate=False,
                 page_limit=None):
    if not extraction or not extraction.get("fields"):
        raise ValueError("至少需要一个提取字段")
    if output_format not in ("csv", "txt", "xlsx", "json"):
        raise ValueError("输出格式只支持 csv、txt、xlsx、json")
    if page_limit is not None:
        try:
            page_limit = int(page_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("页数上限必须是整数") from exc
        if not 1 <= page_limit <= 1000:
            raise ValueError("页数上限必须在 1 到 1000 之间")
    keywords = keywords or []
    if keywords and (len(keywords) > 10 or any(not isinstance(x, str) or not x.strip() for x in keywords)):
        raise ValueError("关键词列表无效，最多 10 条")
    graph = [{"id": 0, "index": 0, "parentId": 0, "type": -1,
              "option": 0, "title": "root", "sequence": [],
              "isInLoop": False, "parameters": _base()}]
    p = _base()
    p.update({"url": url, "links": url, "maxWaitTime": 15,
              "scrollType": 0, "scrollCount": 1, "scrollWaitTime": 1})
    _node(graph, 1, "打开网页", p)
    inputs = [{"id": 0, "name": "urlList_0", "nodeId": 1, "nodeName": "打开网页",
               "value": url, "desc": "起始网址", "type": "text", "exampleValue": url}]
    parent = 0
    if keywords:
        p = _base()
        p.update({"loopType": 3, "pathList": "", "textList": "\n".join(keywords),
                  "code": "", "waitTime": 0, "exitCount": 0, "historyWait": 2,
                  "breakMode": 0, "breakCode": "", "breakCodeWaitTime": 0,
                  "scrollType": 0, "scrollCount": 1, "scrollWaitTime": 1,
                  "skipCount": 0, "allXPaths": ""})
        parent = _node(graph, 8, "逐个关键词采集", p)
        inputs.append({"id": len(inputs), "name": f"loopText_{len(inputs)}", "nodeId": parent,
                       "nodeName": "逐个关键词采集", "value": "\n".join(keywords),
                       "desc": "每行一个关键词", "type": "text", "exampleValue": keywords[0]})
        p = _base()
        p.update({"url": "about:blank", "links": url, "maxWaitTime": 15,
                  "scrollType": 0, "scrollCount": 1, "scrollWaitTime": 1,
                  "cookies": ""})
        _node(graph, 1, "返回搜索首页", p, parent)
    for action in actions:
        kind = action["action"]
        if kind == "input":
            p = _base(action["xpath"], 0.5)
            p.update({"value": action["value"], "allXPaths": [],
                      "useLoop": bool(keywords and action["value"] == keywords[0]), "index": 0})
            ix = _node(graph, 4, "输入文字", p, parent)
            inputs.append({"id": len(inputs), "name": f"inputText_{len(inputs)}", "nodeId": ix,
                           "nodeName": "输入文字", "value": action["value"],
                           "desc": "输入内容", "type": "text", "exampleValue": action["value"]})
        elif kind == "click":
            p = _base(action["xpath"], 1)
            p.update({"scrollType": 0, "scrollCount": 1, "scrollWaitTime": 1,
                      "clickWay": 0, "maxWaitTime": 10, "params": [], "allXPaths": []})
            _node(graph, 2, "点击元素", p, parent)
        elif kind == "select":
            p = _base(action["xpath"], 0.5)
            p.update({"optionMode": 3, "optionValue": action["value"], "index": 0})
            _node(graph, 6, "选择下拉选项", p, parent)
        elif kind == "scroll_until_end":
            target = graph[parent]["sequence"][-1]
            graph[target]["parameters"].update({"scrollType": 3, "scrollCount": 1,
                                                  "scrollWaitTime": 1})
        else:
            raise ValueError("不支持的操作")
    if input_excel and extraction["mode"] == "single" and not extraction.get("detail"):
        _extract_excel_single_with_retry(graph, extraction, parent, url)
    else:
        _extract_nodes(graph, extraction, parent)
    if extraction.get("next_xpath") and page_limit != 1:
        next_xpath = extraction["next_xpath"]
        guarded_next = (f"({next_xpath})[not(@disabled) and not(@aria-disabled='true') "
                        "and not(contains(concat(' ', normalize-space(@class), ' '), ' disabled '))]")
        p = _base("//body")
        repeat_limit = 0 if page_limit is None else page_limit - 1
        p.update({"loopType": 0, "exitCount": repeat_limit,
                  "exitElement": extraction["list_xpath"] if extraction["mode"] == "list" else "//body",
                  "pathList": "", "textList": "", "code": "", "waitTime": 0,
                  "historyWait": 2, "breakMode": 0, "breakCode": "", "breakCodeWaitTime": 0,
                  "scrollType": 0, "scrollCount": 1, "scrollWaitTime": 1,
                  "skipCount": 0, "allXPaths": ""})
        loop_title = "翻页直到没有下一页" if page_limit is None else f"翻页最多 {page_limit} 页"
        loop = _node(graph, 8, loop_title, p, parent)
        inputs.append({"id": len(inputs), "name": f"loopTimes_{len(inputs)}", "nodeId": loop,
                       "nodeName": loop_title, "value": repeat_limit,
                       "desc": "0 表示直到没有下一页" if page_limit is None else f"最多采集 {page_limit} 页",
                       "type": "int", "exampleValue": repeat_limit})
        judge = _node(graph, 9, "判断是否存在下一页", _base(), loop)
        branch_p = _base()
        branch_p.update({"class": 2, "value": guarded_next, "code": "", "waitTime": 0})
        branch = _node(graph, 10, "存在下一页", branch_p, judge)
        p = _base(guarded_next, 1)
        p.update({"scrollType": 0, "scrollCount": 1, "scrollWaitTime": 1,
                  "clickWay": 0, "maxWaitTime": 10, "params": [], "allXPaths": []})
        _node(graph, 2, "点击下一页", p, branch)
        _extract_nodes(graph, extraction, branch)
    all_fields = [*extraction["fields"], *extraction.get("detail", {}).get("fields", [])]
    if len({f["name"] for f in all_fields}) != len(all_fields):
        raise ValueError("列表与详情字段不能重名")
    task = {"id": -1, "name": name[:80] or "AI 生成流程", "url": url, "links": url,
            "create_time": datetime.now(timezone.utc).isoformat(), "version": "0.6.5",
            "containJudge": any(node["option"] == 9 for node in graph), "desc": "AI 原型生成；请在 EasySpider 中检查后执行",
            "inputParameters": inputs,
            "outputParameters": [{"id": i, "name": f["name"], "desc": "",
                                  "type": "text", "recordASField": 1,
                                  "exampleValue": f.get("sample", "")}
                                 for i, f in enumerate(all_fields)],
            "graph": graph, "outputFormat": output_format, "saveThreshold": 10,
            "inputExcel": input_excel,
            "quitWaitTime": 0,
            "removeDuplicate": int(bool(remove_duplicate)), "cloudflare": 0, "environment": 0, "browser": "chrome"}
    validate_task(task)
    return task


def validate_task(task):
    graph = task["graph"]
    if not graph or graph[0]["option"] != 0:
        raise ValueError("缺少 root 节点")
    for i, n in enumerate(graph):
        if n["index"] != i or n["id"] != i or n["option"] not in (0, 1, 2, 3, 4, 6, 8, 9, 10):
            raise ValueError("节点索引或类型不正确")
        if i and (n["parentId"] >= i or i not in graph[n["parentId"]]["sequence"]):
            raise ValueError("父节点关系不正确")
        for child in n["sequence"]:
            if child <= i or child >= len(graph) or graph[child]["parentId"] != i:
                raise ValueError("执行顺序引用无效")
        p = n["parameters"]
        if p.get("beforeJS") or p.get("afterJS") or p.get("code") or p.get("JS"):
            raise ValueError("不允许生成自定义代码")
        if p.get("xpath"):
            etree.XPath(p["xpath"])
        if p.get("waitElement"):
            etree.XPath(p["waitElement"])
        if n["option"] == 10:
            if p.get("class") == 2:
                etree.XPath(p["value"])
            elif p.get("class") != 0 or p.get("value"):
                raise ValueError("条件分支只能判断页面元素是否存在或作为默认分支")
        if n["option"] == 3:
            for f in p["params"]:
                etree.XPath(f["relativeXPath"] or ".")
                if f.get("beforeJS") or f.get("afterJS"):
                    raise ValueError("提取字段不允许前置或后置代码")
                if f.get("JS") and (f["JS"] not in SAFE_FIELD_SCRIPTS or f.get("contentType") != 9):
                    raise ValueError("提取字段只允许内置的固定读取脚本")
                if f.get("contentType") == 9 and f.get("JS") not in SAFE_FIELD_SCRIPTS:
                    raise ValueError("提取字段缺少内置读取脚本")
    names = [f["name"] for f in task["outputParameters"]]
    if len(names) != len(set(names)) or not names:
        raise ValueError("输出字段不能为空或重名")
    extracted = {f["name"] for n in graph if n["option"] == 3 for f in n["parameters"]["params"]}
    if extracted != set(names):
        raise ValueError("提取节点与输出字段不一致")
    return True
