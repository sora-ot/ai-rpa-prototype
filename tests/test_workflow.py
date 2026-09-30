import json
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import _bootstrap
from workflow import NUMBER_SCRIPT, TITLE_SCRIPT, compile_task, validate_task
from llm import _decode_object, ask_json
from observer import check_action_target, input_target
from agent import (_available_candidate_ids, _check_pagination_decision, _compact,
                   _guard_list_records, _has_high_impact_action, _stable_relative_xpath,
                   _wait_for_loading_placeholders, assess_risk, make_plan)
from settings import resolve_model_config, public_config
import server


class WorkflowTests(unittest.TestCase):
    def test_list_guard_drops_survey_but_keeps_incomplete_product(self):
        from lxml import html

        page = html.fromstring("""<main>
          <article class="card" data-id="1"><div class="title">A</div><span class="price">10</span></article>
          <article class="card" data-id="survey"><div class="question">Survey</div></article>
          <article class="card" data-id="2"><div class="title">B</div><span class="price">20</span></article>
          <article class="card" data-id="3"><div class="title">C</div><span class="price">30</span></article>
          <article class="card" data-id="4"><span class="price">40</span></article>
        </main>""")

        class Row:
            def __init__(self, element):
                self.element = element
                self.id = element.get("data-id")

            def find_elements(self, _by, path):
                return self.element.xpath(path)

        class Browser:
            def find_elements(self, _by, path):
                ids = {element.get("data-id") for element in page.xpath(path)}
                return [row for row in rows if row.id in ids]

        rows = [Row(element) for element in page.xpath("//article")]
        fields = [{"relative_xpath": "//div[@class='title']"},
                  {"relative_xpath": "//span[@class='price']"}]
        path, kept = _guard_list_records(Browser(), "//article[@class='card']", rows, fields)
        self.assertEqual([row.id for row in kept], ["1", "2", "3", "4"])
        self.assertEqual([element.get("data-id") for element in page.xpath(path)],
                         ["1", "2", "3", "4"])

    def test_dynamic_placeholder_waits_for_product_cards(self):
        class Page:
            counts = iter((12, 8, 0))

            def execute_script(self, _script):
                return next(self.counts)

        with patch('agent.time.sleep') as pause:
            self.assertTrue(_wait_for_loading_placeholders(Page()))
        self.assertEqual(pause.call_count, 2)

    def test_record_field_xpath_survives_different_card_layouts(self):
        from lxml import html

        page = html.fromstring("""<main>
          <article class="card"><div class="badge">新品</div>
            <div class="price--abc"><span>69</span><span>.90</span></div></article>
          <article class="card"><div class="price--abc">72.00</div></article>
        </main>""")
        cards = page.xpath("//article")

        class Row:
            def __init__(self, element):
                self.element = element

            def find_elements(self, _by, path):
                return self.element.xpath(path)

        target = cards[0].xpath(".//div[contains(@class,'price--abc')]")[0]
        relative = _stable_relative_xpath(Row(cards[0]), target, "price--abc", "div",
                                          [Row(card) for card in cards])
        self.assertTrue(relative.startswith("//div["))
        self.assertEqual(["".join(node.itertext()) for node in page.xpath("(//article)[1]" + relative)],
                         ["69.90"])
        self.assertEqual(["".join(node.itertext()) for node in page.xpath("(//article)[2]" + relative)],
                         ["72.00"])

    def test_truncated_model_json_retries_without_exposing_response(self):
        partial = {"choices": [{"finish_reason": "length", "message": {"content": '{"action":'}}]}
        complete = {"choices": [{"finish_reason": "stop", "message": {
            "content": '{"action":"extract","mode":"list"}'}}]}
        with patch("llm._post", side_effect=[partial, complete]) as api:
            result = ask_json("deepseek", "secret", "deepseek-flash", "system", "user")
        self.assertEqual(result["action"], "extract")
        self.assertEqual(api.call_count, 2)
        self.assertEqual(api.call_args_list[0].args[1]["max_tokens"], 8192)
        self.assertEqual(api.call_args_list[1].args[1]["max_tokens"], 16384)
        self.assertIn("上次响应无法解析", api.call_args_list[1].args[1]["messages"][1]["content"])
        with patch("llm._post", return_value=partial):
            with self.assertRaisesRegex(ValueError, "输出达到长度限制"):
                ask_json("deepseek", "secret", "deepseek-flash", "system", "user")

    def test_fenced_json_and_semantic_observation(self):
        self.assertEqual(_decode_object('```json\n{"ok":true}\n```'), {"ok": True})
        snapshot = {"url": "https://example.org", "title": "catalog", "text": "items",
                    "elements": [{"id": "e1", "tag": "span", "text": "24.99",
                                  "absolute_xpath": "/html/body/article[1]/span[1]",
                                  "xpath": "//span[1]", "bbox": [1, 2, 3, 4]}],
                    "lists": [{"id": "l1", "count": 10, "score": 90,
                               "first_xpath": "/html/body/article[1]", "xpath": "//article",
                               "samples": ["Product 24.99"]}],
                    "next_candidates": []}
        compact = _compact(snapshot)
        self.assertEqual(compact["elements"][0]["first_record_of"], ["l1"])
        self.assertNotIn("absolute_xpath", json.dumps(compact))
        self.assertNotIn("//span", json.dumps(compact))

    def test_available_candidate_ids_are_explicitly_grounded(self):
        snapshot = {"elements": [{"id": "e1"}, {"id": "e2"}],
                    "lists": [{"id": "l1"}],
                    "next_candidates": [{"id": "e2"}]}
        self.assertEqual(_available_candidate_ids(snapshot),
                         {"elements": ["e1", "e2"], "lists": ["l1"], "next": ["e2"]})

    def test_model_risk_assessment_is_preserved_and_can_block(self):
        safe = {"task_name": "safe", "start_url": "https://example.org",
                "steps": ["Search and collect results"], "fields": ["title"],
                "pagination": "none", "needs_clarification": False,
                "clarifying_questions": [],
                "risk_assessment": {"risk": "low", "category": "none",
                                     "reason": "Read-only collection", "safe_to_continue": True}}
        with patch("agent.ask_json", return_value=safe):
            plan = make_plan("collect titles", "https://example.org", "deepseek", "dummy", "")
        self.assertEqual(plan["risk_assessment"]["risk"], "low")
        self.assertEqual(plan["risk_assessment"]["source"], "model")

        unsafe = {**safe, "risk_assessment": {"risk": "high", "category": "purchase",
                                               "reason": "It submits an order", "safe_to_continue": False}}
        with patch("agent.ask_json", return_value=unsafe), self.assertRaisesRegex(
                ValueError, "AI"):
            make_plan("collect titles", "https://example.org", "deepseek", "dummy", "")

    def test_risk_review_fails_closed_without_leaking_context(self):
        with patch("agent.ask_json", side_effect=RuntimeError("network")) as reviewer:
            result = assess_risk("deepseek", "secret", "model", {
                "kind": "click", "element": {"text": "Search"},
                "password": "must-not-be-added"})
        self.assertEqual(result["risk"], "high")
        self.assertEqual(result["source"], "error")
        self.assertNotIn("secret", json.dumps(result))
        reviewer.assert_called_once()
        self.assertNotIn("api_key", reviewer.call_args.args[4])
        self.assertNotIn("password", reviewer.call_args.args[4])

    def test_optional_second_pass_risk_review_is_used(self):
        plan_response = {"task_name": "safe", "start_url": "https://example.org",
                         "steps": ["Search and collect results"], "fields": ["title"],
                         "pagination": "none", "needs_clarification": False,
                         "clarifying_questions": [],
                         "risk_assessment": {"risk": "low", "category": "none",
                                              "reason": "Read only", "safe_to_continue": True}}
        audit_response = {"risk": "medium", "category": "account",
                          "reason": "The site may require login", "safe_to_continue": False}
        with patch("agent.risk_review_enabled", return_value=True), \
                patch("agent.ask_json", side_effect=[plan_response, audit_response]):
            plan = make_plan("collect titles", "https://example.org", "deepseek", "dummy", "")
        self.assertEqual(plan["risk_assessment"]["risk"], "medium")
        self.assertTrue(plan["risk_review_required"])
    def test_safe_collection_plan_can_mention_purchase_as_a_blocked_target(self):
        safe = {
            "task_name": "淘宝羽毛球拍",
            "start_url": "https://example.com/search",
            "steps": [
                "打开商品搜索页并搜索羽毛球拍",
                "按销量排序并翻页采集 3 页",
                "不要点击购买、支付或提交订单按钮，只提取商品列表信息",
            ],
            "fields": ["商品名称", "价格", "销量", "店铺名称"],
            "pagination": "until_end", "needs_clarification": False,
            "clarifying_questions": [],
        }
        with patch("agent.ask_json", return_value=safe):
            plan = make_plan("采集商品列表", "https://example.com/search",
                             "deepseek", "dummy", "")
        self.assertFalse(plan["needs_clarification"])
        self.assertFalse(_has_high_impact_action(safe["steps"][2]))

    def test_explicit_purchase_action_is_rejected_but_sorting_is_allowed(self):
        self.assertTrue(_has_high_impact_action("点击立即购买按钮并提交订单"))
        self.assertTrue(_has_high_impact_action("执行支付流程"))
        self.assertFalse(_has_high_impact_action("按销量排序并采集价格"))
        self.assertFalse(_has_high_impact_action("采集商品购买数量字段"))
        unsafe = {
            "task_name": "unsafe", "start_url": "https://example.com",
            "steps": ["打开页面", "点击立即购买按钮"], "fields": ["title"],
            "pagination": "none", "needs_clarification": False,
            "clarifying_questions": [],
        }
        with patch("agent.ask_json", return_value=unsafe), self.assertRaisesRegex(
                ValueError, "高风险"):
            make_plan("购买商品", "https://example.com", "deepseek", "dummy", "")

    def test_search_submit_and_sort_controls_are_not_blocked(self):
        check_action_target({"tag": "button", "text": "\u8d2d\u4e70\u6570\u91cf", "href": "", "role": ""}, "click")
        check_action_target({"tag": "button", "text": "提交筛选", "href": "", "role": ""}, "click")
        check_action_target({"tag": "a", "text": "按销量排序", "href": "?sort=sales", "role": ""}, "click")
        with self.assertRaisesRegex(ValueError, "高风险"):
            check_action_target({"tag": "button", "text": "立即购买", "href": "", "role": ""}, "click")
        with self.assertRaisesRegex(ValueError, "高风险"):
            check_action_target({"tag": "button", "text": "提交订单", "href": "", "role": ""}, "click")

    def test_excel_single_page_retry_uses_native_nodes_and_error_row(self):
        task = compile_task("https://example.org/item/1", "Excel items", [], {
            "mode": "single", "list_xpath": "", "next_xpath": "",
            "fields": [
                {"name": "title", "attribute": "text", "xpath": "//h1",
                 "relative_xpath": "//h1", "sample": "Product 1"},
                {"name": "spec", "attribute": "text", "xpath": "//span[@id='spec']",
                 "relative_xpath": "//span[@id='spec']", "sample": "Spec 1"}]},
            input_excel="inputs/items.xlsx", output_format="json")
        self.assertTrue(validate_task(task))
        self.assertTrue(task["containJudge"])
        self.assertEqual(sum(n["title"] == "重新打开当前网址" for n in task["graph"]), 2)
        self.assertEqual(sum(n["title"] == "记录失败网址与错误" for n in task["graph"]), 1)
        self.assertFalse(any(n["option"] == 5 for n in task["graph"]))
        self.assertFalse(next(n for n in task["graph"]
                              if n["title"] == "字段齐全，提取数据")["isInLoop"])
        self.assertEqual(next(n for n in task["graph"]
                              if n["title"] == "记录失败网址与错误")["parameters"]["params"][0]["contentType"], 5)

    def test_native_task_structure_and_pagination(self):
        task = compile_task(
            "https://example.org/list", "采集文章",
            [{"action": "input", "xpath": '//input[@id="q"]', "value": "AI"},
             {"action": "click", "xpath": '//button[@id="go"]'}],
            {"mode": "list", "list_xpath": '//article[contains(@class,"card")]',
             "fields": [
                 {"name": "标题", "attribute": "text", "xpath": "//h2", "relative_xpath": "/h2[1]", "sample": "文章 A"},
                 {"name": "链接", "attribute": "href", "xpath": "//a", "relative_xpath": "/a[1]", "sample": "https://example.org/a"}],
             "next_xpath": '//a[@rel="next"]'},
        )
        self.assertTrue(validate_task(json.loads(json.dumps(task))))
        self.assertEqual([n["option"] for n in task["graph"]], [0, 1, 4, 2, 8, 3, 8, 9, 10, 2, 8, 3])
        self.assertEqual(task["graph"][4]["sequence"], [5])
        self.assertEqual(task["graph"][4]["parameters"]["scrollType"], 0)
        self.assertEqual(task["graph"][4]["parameters"]["scrollCount"], 1)
        self.assertEqual(task["graph"][6]["parameters"]["exitCount"], 0)
        self.assertEqual(task["graph"][6]["parameters"]["xpath"], "//body")
        self.assertEqual(task["graph"][8]["parameters"]["class"], 2)
        self.assertIn("not(@disabled)", task["graph"][8]["parameters"]["value"])
        self.assertEqual(task["graph"][5]["parameters"]["params"][0]["relativeXPath"], "/h2[1]")
        self.assertEqual([p["name"] for p in task["outputParameters"]], ["标题", "链接"])
        self.assertTrue(task["containJudge"])
        self.assertEqual([p["name"] for p in task["inputParameters"]],
                         ["urlList_0", "inputText_1", "loopTimes_2"])
        self.assertEqual(task["graph"][5]["parameters"]["params"][0]["paraType"], "text")
        self.assertEqual(task["outputParameters"][0]["recordASField"], 1)
        self.assertEqual(task["id"], -1)

    def test_explicit_page_limit_keeps_three_pages_in_native_loop(self):
        task = compile_task(
            "https://example.org/list", "三页商品", [],
            {"mode": "list", "list_xpath": "//article", "next_xpath": "//a[@rel='next']",
             "fields": [{"name": "title", "attribute": "text", "xpath": "//h2",
                         "relative_xpath": "/h2[1]", "sample": "A"}]},
            page_limit=3)
        loop = next(node for node in task["graph"] if node["option"] == 8 and
                    node["title"] == "翻页最多 3 页")
        self.assertEqual(loop["parameters"]["exitCount"], 2)
        self.assertEqual(loop["title"], "翻页最多 3 页")
        self.assertEqual(next(item for item in task["inputParameters"]
                              if item["nodeId"] == loop["id"])["value"], 2)

    def test_plan_normalizes_explicit_page_limit(self):
        raw = {"task_name": "三页", "start_url": "https://example.org",
               "steps": ["搜索并按销量排序", "采集商品列表"], "fields": ["销量"],
               "pagination": "until_end", "page_limit": 3,
               "needs_clarification": False, "clarifying_questions": []}
        with patch("agent.ask_json", return_value=raw):
            plan = make_plan("采集 3 页", "https://example.org", "deepseek", "dummy", "")
        self.assertEqual(plan["pagination"], "page_limit")
        self.assertEqual(plan["page_limit"], 3)

    def test_api_adapter_request_shapes(self):
        calls = []
        def fake_post(url, payload, headers):
            calls.append((url, payload, headers))
            if "generativelanguage" in url:
                return {"candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}]}
            return {"choices": [{"message": {"content": '{"ok":true}'}}]}
        with patch("llm._post", side_effect=fake_post):
            for provider in ("deepseek", "gpt", "gemini"):
                self.assertEqual(ask_json(provider, "secret", "", "system", "user"), {"ok": True})
        self.assertIn("api.deepseek.com", calls[0][0])
        self.assertIn("api.openai.com", calls[1][0])
        self.assertIn("generativelanguage.googleapis.com", calls[2][0])
        self.assertEqual(calls[0][1]["response_format"], {"type": "json_object"})
        self.assertEqual(calls[2][1]["generationConfig"]["responseMimeType"], "application/json")

    def test_title_attribute_uses_only_fixed_easyspider_script(self):
        task = compile_task("https://example.org", "完整标题", [], {
            "mode": "list", "list_xpath": "//article", "next_xpath": "",
            "fields": [{"name": "title", "attribute": "title", "xpath": "//h2",
                        "relative_xpath": "/h2[1]", "sample": "Full Title"}]})
        field = task["graph"][3]["parameters"]["params"][0]
        self.assertEqual((field["contentType"], field["JS"]), (9, TITLE_SCRIPT))
        field["JS"] = "return document.cookie"
        with self.assertRaises(ValueError):
            validate_task(task)

    def test_number_field_uses_fixed_script(self):
        task = compile_task("https://example.org", "价格", [], {
            "mode": "list", "list_xpath": "//article", "next_xpath": "",
            "fields": [{"name": "price", "attribute": "number", "xpath": "//p",
                        "relative_xpath": "/p[1]", "sample": "51.77"}]})
        field = task["graph"][3]["parameters"]["params"][0]
        self.assertEqual((field["contentType"], field["JS"]), (9, NUMBER_SCRIPT))

    def test_editable_wrapper_maps_to_real_input(self):
        snapshot = {"elements": [
            {"id": "e1", "tag": "div", "editable_xpath": '//input[@id="q"]'},
            {"id": "e2", "tag": "input", "editable_xpath": '//input[@id="q"]'}]}
        self.assertEqual(input_target(snapshot, "e1"), '//input[@id="q"]')

    def test_plan_without_url_can_ask_human(self):
        raw = {"task_name": "采集房价", "start_url": "", "steps": ["选择公开房产网站", "提取房价"],
               "fields": ["小区", "均价"], "pagination": "until_end",
               "needs_clarification": True, "clarifying_questions": ["哪个城市和区域？"]}
        with patch("agent.ask_json", return_value=raw):
            plan = make_plan("采集房价", "", "deepseek", "dummy", "")
        self.assertTrue(plan["needs_clarification"])
        self.assertEqual(plan["pagination"], "until_end")
        self.assertTrue(any("网站" in q for q in plan["questions"]))

    def test_pagination_plan_cannot_be_saved_as_single_page(self):
        plan = {"pagination": "until_end"}
        snapshot = {"next_candidates": [{"id": "e7", "text": "下一页"}]}
        with self.assertRaisesRegex(ValueError, "e7"):
            _check_pagination_decision(plan, snapshot, {"action": "extract", "next_id": None})
        with self.assertRaisesRegex(ValueError, "翻页方式不一致"):
            server.validate_plan_task(plan, {"containJudge": False})
        _check_pagination_decision(plan, snapshot, {"action": "extract", "next_id": "e7"})
        server.validate_plan_task(plan, {"containJudge": True,
                                         "graph": [{"option": 2, "title": "点击下一页"}]})

    def test_replanning_keeps_all_requirements_and_user_url(self):
        history = [{"request": f"要求 {i}", "url_hint": "", "answers": [f"补充 {i}"],
                    "plan": {"task_name": f"第 {i} 版"}} for i in range(10)]
        raw = {"task_name": "更新后的任务", "start_url": "https://wrong.example/",
               "steps": ["打开网站", "提取标题"], "fields": ["标题"],
               "pagination": "none", "needs_clarification": False, "clarifying_questions": []}
        with patch("agent.ask_json", return_value=raw) as model_call:
            plan = make_plan("把标题加进来", "https://example.org/", "deepseek", "dummy", "",
                             history, ["只看英文内容"])
        prompt = json.loads(model_call.call_args.args[4])
        self.assertEqual(len(prompt["requirements_history"]), 10)
        self.assertEqual(prompt["requirements_history"][0]["request"], "要求 0")
        self.assertEqual(prompt["previous_plan"], history[-1]["plan"])
        self.assertEqual(prompt["additional_information"], ["只看英文内容"])
        self.assertEqual(plan["start_url"], "https://example.org/")

    def test_env_model_and_key_fallback(self):
        config = {"AI_RPA_PROVIDER": "gemini", "AI_RPA_MODEL": "gemini-2.5-flash",
                  "GEMINI_API_KEY": "secret", "AI_RPA_MAX_ACTIONS": "21"}
        self.assertEqual(resolve_model_config({}, config), ("gemini", "secret", "gemini-2.5-flash"))
        self.assertEqual(public_config(config)["max_actions"], 21)
        self.assertNotIn("secret", json.dumps(public_config(config)))

    def test_local_http_plan_confirmation_gate(self):
        web = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{web.server_port}"
        def post(path, obj):
            request = Request(base + path, json.dumps(obj).encode(), {"Content-Type": "application/json"})
            with urlopen(request, timeout=3) as res:
                return json.load(res)
        tentative = {"task_name": "test", "steps": ["选择网站", "提取"], "fields": ["title"],
                     "questions": ["选择哪个站点？"], "needs_clarification": True,
                     "start_url": "", "pagination": "none"}
        plan = {**tentative, "questions": [], "needs_clarification": False,
                "start_url": "https://example.org"}
        result = {"task": {"graph": []}, "samples": [{"title": "A"}], "mapping": []}
        try:
            with patch("server.make_plan", side_effect=[tentative,plan]) as planner, patch("server.explore", return_value=result) as explore:
                created = post("/api/plan", {"message": "采集标题", "api_key": "secret"})
                sid = created["session"]
                self.assertEqual(server.SESSIONS[sid]["status"], "clarifying")
                explore.assert_not_called()
                revised = post("/api/plan", {"session": sid,
                                             "message": "使用 example.org；再加入作者字段",
                                             "api_key": "secret"})
                self.assertEqual(revised["session"], sid)
                self.assertEqual(revised["revision"], 2)
                self.assertEqual(server.SESSIONS[sid]["status"], "planned")
                self.assertEqual(len(planner.call_args.kwargs), 0)
                self.assertEqual(len(planner.call_args.args[5]), 1)
                self.assertEqual(planner.call_args.args[5][0]["request"], "采集标题")
                self.assertEqual([item["role"] for item in revised["messages"]],
                                 ["user", "assistant", "user", "assistant"])
                post("/api/run", {"session": sid, "provider": "deepseek", "api_key": "secret"})
                for _ in range(20):
                    with urlopen(base + "/api/state?session=" + sid, timeout=3) as response:
                        state = json.load(response)
                    if state["status"] == "preview":
                        break
                    time.sleep(0.05)
                self.assertEqual(state["result"]["samples"], [{"title": "A"}])
                explore.assert_called_once()
                context = json.loads(explore.call_args.args[0])
                self.assertEqual(len(context["requirements_history"]), 2)
                self.assertEqual(context["requirements_history"][0]["request"], "采集标题")
                self.assertEqual(context["requirements_history"][1]["request"],
                                 "使用 example.org；再加入作者字段")
        finally:
            web.shutdown()
            web.server_close()

    def test_chat_limits_are_enforced_by_server(self):
        self.assertEqual(server.MAX_PLAN_TURNS, 15)
        self.assertEqual(server.MAX_USER_MESSAGE_CHARS, 1000)
        web = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{web.server_port}"
        def post(message, session=None):
            body = {"message": message, "api_key": "secret"}
            if session:
                body["session"] = session
            request = Request(base + "/api/plan", json.dumps(body).encode(),
                              {"Content-Type": "application/json"})
            with urlopen(request, timeout=3) as response:
                return json.load(response)
        plan = {"task_name": "test", "steps": ["提取标题"], "fields": ["标题"],
                "questions": [], "needs_clarification": False,
                "start_url": "https://example.org", "pagination": "none"}
        try:
            with patch("server.make_plan", return_value=plan) as planner, patch("server.MAX_PLAN_TURNS", 2):
                with self.assertRaises(HTTPError):
                    post("中" * 1001)
                first = post("采集标题")
                second = post("加上作者", first["session"])
                with self.assertRaises(HTTPError):
                    post("再加日期", first["session"])
                self.assertEqual(second["revision"], 2)
                self.assertEqual(planner.call_count, 2)
                self.assertEqual(server.SESSIONS[first["session"]]["status"], "planned")
        finally:
            web.shutdown()
            web.server_close()


if __name__ == "__main__":
    unittest.main()
