"""Observe live DOM and expose bounded, grounded candidate IDs to the agent."""
import base64
import re
from pathlib import Path
from urllib.parse import urlparse
from settings import (OBSERVATION_MAX_ELEMENTS, OBSERVATION_EXPANDED_ELEMENTS,
                      OBSERVATION_MAX_LISTS, OBSERVATION_TEXT_CHARS,
                      DOM_SUBTREE_CHARS, DOM_PAGE_CHARS, DOM_MAX_CHUNK_CHARS)


OBSERVE_JS = (Path(__file__).resolve().parent / "dom_observer.js").read_text(encoding="utf-8")


RISK_WORDS = ("支付", "付款", "购买", "下单", "提交订单", "删除", "发送", "转账", "pay", "checkout", "delete", "purchase", "send", "confirm order")


def observe(driver, with_screenshot=False, *, focus="balanced", expanded=False):
    if focus not in ("balanced", "controls", "content"):
        raise ValueError("网页观察范围必须为 balanced、controls 或 content")
    result = driver.execute_script(OBSERVE_JS, {
        "focus": focus, "limit": OBSERVATION_EXPANDED_ELEMENTS if expanded else OBSERVATION_MAX_ELEMENTS,
        "maxLists": OBSERVATION_MAX_LISTS, "textChars": OBSERVATION_TEXT_CHARS})
    if with_screenshot:
        result["screenshot"] = base64.b64encode(driver.get_screenshot_as_png()).decode("ascii")
    return result


def inspect_dom(driver, snapshot, *, scope="element", candidate_id=None, depth=0,
                offset=0, max_chars=None):
    """Read a cleaned region/page, registering every candidate exposed by the chunk.

    Existing IDs remain grounded in this snapshot. No model-provided selector or
    script is evaluated. Chunk offsets refer to the cleaned HTML, not raw source.
    """
    if scope not in ("element", "search", "list", "page"):
        raise ValueError("DOM 范围必须为 element、search、list 或 page")
    for label, value, upper in (("祖先层数", depth, 5), ("DOM 偏移", offset, 20_000_000)):
        if type(value) is not int or not 0 <= value <= upper:
            raise ValueError(f"{label}无效")
    if max_chars is None:
        max_chars = DOM_PAGE_CHARS if scope == "page" else DOM_SUBTREE_CHARS
    if type(max_chars) is not int or not 1000 <= max_chars <= DOM_MAX_CHUNK_CHARS:
        raise ValueError(f"DOM 分块长度应为 1000 至 {DOM_MAX_CHUNK_CHARS}")
    root_xpath = ""
    previous = snapshot.get("dom", {})
    if offset and previous.get("scope") == scope and previous.get("candidate_id") == candidate_id:
        if previous.get("depth", 0) != depth:
            raise ValueError("DOM 分块读取必须保持相同 depth，或从 offset=0 重新读取")
    if scope != "page":
        if scope == "search" and not candidate_id:
            forms = snapshot.get("search_forms", [])
            candidate_id = forms[0]["input_id"] if forms else None
        target = candidate(snapshot, candidate_id, "lists" if scope == "list" else "elements")
        root_xpath = target["first_xpath"] if scope == "list" else target["xpath"]
    config = {
        "mode": "dom", "scope": scope, "root_xpath": root_xpath,
        "depth": depth, "offset": offset, "max_chars": max_chars,
        "known": [{"id": e["id"], "absolute_xpath": e["absolute_xpath"]}
                  for e in snapshot.get("elements", []) if e.get("absolute_xpath")],
        "known_lists": [{"id": g["id"], "xpath": g["xpath"], "first_xpath": g["first_xpath"]}
                        for g in snapshot.get("lists", [])]}
    try:
        result = driver.execute_script(OBSERVE_JS, config)
    except Exception as exc:
        if type(exc).__name__ == "JavascriptException" and "DOM 区域已变化" in str(exc):
            raise ValueError("DOM 区域已变化，请重新 observe") from None
        raise
    if snapshot.get("url") and result["url"] != snapshot["url"]:
        raise ValueError("网页地址已变化，请重新 observe 后读取 DOM")
    if offset and previous.get("scope") == scope and previous.get("candidate_id") == candidate_id:
        if previous.get("fingerprint") != result["dom"]["fingerprint"]:
            raise ValueError("DOM 在分块读取期间发生变化，请从 offset=0 重新读取")
    merged = {e["id"]: e for e in snapshot.get("elements", [])}
    merged.update({e["id"]: e for e in result["elements"]})
    lists = {g["id"]: g for g in snapshot.get("lists", [])}
    lists.update({g["id"]: g for g in result.get("lists", [])})
    current_ids = [e["id"] for e in result["elements"]]
    baseline_ids = snapshot.get("model_base_ids") or [e["id"] for e in snapshot.get("elements", [])]
    model_ids = list(dict.fromkeys(current_ids + baseline_ids))
    # Current chunk IDs always remain selectable; fill remaining room from the base.
    model_ids = model_ids[:max(OBSERVATION_EXPANDED_ELEMENTS, len(current_ids))]
    updated = dict(snapshot)
    updated.update({"elements": list(merged.values()), "model_base_ids": baseline_ids,
                    "model_element_ids": model_ids, "lists": list(lists.values()),
                    "dom": {**result["dom"], "candidate_id": candidate_id, "depth": depth, "max_chars": max_chars}})
    updated["observation_stats"] = {**snapshot.get("observation_stats", {}),
        "dom_scope": scope, "dom_candidates": len(current_ids), "model_elements": len(model_ids),
        "registered_elements": len(merged)}
    selected = set(model_ids)
    updated["next_candidates"] = [{"id": e["id"], "text": e.get("text", ""),
                                   "href": e.get("href", ""), "xpath": e["xpath"]}
                                  for e in merged.values() if e.get("is_next") and e["id"] in selected]
    return updated


def model_elements(snapshot):
    """Keep the local ID registry while bounding the candidates sent to the model."""
    selected = snapshot.get("model_element_ids")
    if selected is None:
        return snapshot.get("elements", [])
    registry = {e["id"]: e for e in snapshot.get("elements", [])}
    return [registry[item] for item in selected if item in registry]


def candidate(snapshot, candidate_id, kind="elements"):
    found = next((x for x in snapshot[kind] if x["id"] == candidate_id), None)
    if not found:
        labels = {"elements": "元素", "lists": "列表"}
        label = labels.get(kind, kind)
        available = "、".join(str(x.get("id")) for x in snapshot.get(kind, [])
                               if x.get("id"))
        suffix = available or "无"
        raise ValueError(f"{label}候选 ID 不存在：{candidate_id}；当前可用{label} ID：{suffix}")
    return found


def input_target(snapshot, candidate_id):
    selected = candidate(snapshot, candidate_id)
    if selected.get("editable_xpath"):
        return selected["editable_xpath"]
    suggestions = [e["id"] for e in snapshot["elements"] if e.get("editable_xpath")][:8]
    raise ValueError("所选元素不可输入。可选输入目标 ID：" + ("、".join(suggestions) or "无"))


_RISK_ACTION_WORDS = (
    "click", "press", "execute", "perform", "confirm", "submit",
    "\u70b9\u51fb", "\u6309\u4e0b", "\u6267\u884c", "\u8fdb\u884c", "\u53d1\u8d77", "\u64cd\u4f5c", "\u786e\u8ba4",
    "\u7acb\u5373", "\u9a6c\u4e0a",
)
_RISK_NEGATIONS = (
    "\u4e0d", "\u4e0d\u8981", "\u7981\u6b62", "\u907f\u514d", "\u65e0\u9700", "\u4e0d\u6267\u884c", "\u4e0d\u70b9\u51fb",
    "do not", "don't", "never", "avoid",
)
_RISK_DATA_SUFFIXES = set("\u91cf\u6570\u6b21\u4fe1\u606f\u6570\u636e\u6307\u6807\u540d\u79f0\u5b57\u6bb5")


def _has_direct_risk_intent(label):
    """Recognise an actionable risk target without blocking mere mentions."""
    lowered = label.lower()
    for word in RISK_WORDS:
        term = str(word).lower()
        start = 0
        while True:
            index = lowered.find(term, start)
            if index < 0:
                break
            prefix = lowered[max(0, index - 40):index]
            if any(re.search(rf"{re.escape(neg)}[^,;:!?，；：！？]*$", prefix)
                   for neg in _RISK_NEGATIONS):
                start = index + len(term)
                continue
            if any(action in prefix for action in _RISK_ACTION_WORDS):
                return True
            if index == 0 or lowered[index - 1] in " \t(/[|,-_":
                after = lowered[index + len(term):index + len(term) + 2]
                data_field = term in ("purchase", "\u8d2d\u4e70")
                if not data_field or not any(char in _RISK_DATA_SUFFIXES for char in after):
                    return True
            start = index + len(term)
    return False


def check_action_target(element, action):
    if action != "click":
        return
    # Search/filter forms commonly use a button labelled “提交”. It is not
    # itself a destructive action. Risk checks apply to actionable controls,
    # while a list container or product text may merely mention a keyword.
    if element.get("tag") not in ("a", "button", "input") and element.get("role") != "button" and not element.get("clickable"):
        return
    label = " ".join(str(element.get(k, "")) for k in (
        "text", "href", "role", "semantic_label", "aria_label", "icon_hint")).lower()
    if _has_direct_risk_intent(label):
        raise ValueError("检测到支付、删除、发送等高风险按钮，已停止")


def validate_url(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc or p.username or p.password:
        raise ValueError("请输入普通 http(s) 网页 URL")
    return url
