"""Search prerequisites and bounded adaptive observation regressions."""
import copy
import unittest
from unittest.mock import patch

import _bootstrap
import agent
from observer import check_action_target, inspect_dom, observe
from workflow import compile_task


class ObservationTests(unittest.TestCase):
    def test_recommendations_are_rejected_before_search(self):
        page = {"url": "https://example.org/", "text": "推荐商品", "lists": [],
                "search_forms": [{"input_id": "e1", "submit_ids": ["e2"], "query": ""}]}
        plan = {"keywords": ["合肥"]}
        original = agent._result_signature(page)
        self.assertFalse(agent._search_progress(plan, page, [], original)["ready"])
        typed = copy.deepcopy(page)
        typed["search_forms"][0]["query"] = "合肥"
        actions = [{"action": "input", "value": "合肥"}]
        self.assertFalse(agent._search_progress(plan, typed, actions, original)["ready"])
        actions.append({"action": "click"})
        self.assertFalse(agent._search_progress(plan, typed, actions, original)["ready"])
        typed["text"] = "合肥搜索结果"
        self.assertTrue(agent._search_progress(plan, typed, actions, original)["ready"])

    def test_existing_url_query_can_prove_search_without_typing(self):
        page = {"url": "https://example.org/search?keyword=%E5%90%88%E8%82%A5"}
        self.assertTrue(agent._search_progress({"keywords": ["合肥"]}, page, [], "")["ready"])
        self.assertFalse(agent._search_progress({"keywords": ["南京"]}, page, [], "")["ready"])

    def test_rich_control_information_is_forwarded_without_local_xpaths(self):
        snapshot = {"elements": [{"id": "e1", "tag": "button", "icon_hint": "search",
            "semantic_label": "搜索", "clickable": True, "xpath": "//button[@id='s']",
            "absolute_xpath": "/html/body/button[1]"}], "lists": [],
            "search_forms": [{"input_id": "e2", "submit_ids": ["e1"], "query": "合肥"}],
            "observation_stats": {"truncated": True, "focus": "controls"}}
        compact = agent._compact(snapshot)
        self.assertEqual(compact["elements"][0]["icon_hint"], "search")
        self.assertEqual(compact["search_forms"], snapshot["search_forms"])
        self.assertNotIn("xpath", compact["elements"][0])
        self.assertNotIn("absolute_xpath", compact["elements"][0])

    def test_invalid_observation_focus_is_rejected(self):
        with self.assertRaises(ValueError):
            observe(None, focus="arbitrary_script")

    def test_dom_request_rejects_selectors_and_unbounded_inputs(self):
        for values in ({"scope": "//body"}, {"scope": "page", "offset": -1},
                       {"scope": "page", "depth": True}, {"scope": "page", "max_chars": 1_000_000}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                inspect_dom(None, {}, **values)

    def test_dom_discovered_elements_are_grounded_and_forwarded(self):
        class Driver:
            def execute_script(self, _script, config):
                self.config = config
                return {"url": "https://example.org/", "elements": [
                    {"id": "e2", "tag": "p", "text": "新增字段", "xpath": "//p[@id='late']",
                     "absolute_xpath": "/html[1]/body[1]/p[1]", "editable": False}],
                    "dom": {"scope": "page", "html": '<p data-rpa-id="e2">新增字段</p>',
                            "fingerprint": "same", "offset": 0, "next_offset": None}}
        base = {"url": "https://example.org/", "lists": [], "elements": [
            {"id": "e1", "tag": "input", "xpath": "//input", "absolute_xpath": "/html[1]/body[1]/input[1]"}]}
        driver = Driver()
        updated = inspect_dom(driver, base, scope="page")
        compact = agent._compact(updated)
        self.assertEqual(compact["dom"]["html"], updated["dom"]["html"])
        self.assertIn("e2", agent._available_candidate_ids(updated)["elements"])
        self.assertEqual(next(e for e in compact["elements"] if e["id"] == "e2")["editable"], False)
        self.assertNotIn("xpath", compact["elements"][0])
        self.assertEqual(len(base["elements"]), 1, "Inspection should not mutate the previous snapshot")
        self.assertNotIn("xpath", driver.config["known"][0])

    def test_dom_pagination_rejects_changed_page(self):
        class Driver:
            def execute_script(self, *_):
                return {"url": "https://example.org/", "elements": [],
                        "dom": {"scope": "page", "html": "new", "fingerprint": "changed"}}
        base = {"url": "https://example.org/", "elements": [],
                "dom": {"scope": "page", "candidate_id": None, "fingerprint": "previous"}}
        with self.assertRaisesRegex(ValueError, "发生变化"):
            inspect_dom(Driver(), base, scope="page", offset=1000)

    def test_detail_page_can_read_dom_with_remaining_shared_budget(self):
        class Element:
            def click(self): pass
        class Driver:
            window_handles = ["main"]
            def find_element(self, *_): return Element()
        base = {"elements": [{"id": "e1", "tag": "a", "xpath": "//a",
                               "absolute_xpath": "/html/body/article[1]/a"}],
                "lists": [{"id": "l1", "first_xpath": "/html/body/article[1]"}]}
        detail_page = {"url": "https://details.example.net/news/1", "elements": [], "lists": [],
                       "dom": {"scope": "page", "html": "<h1>详情</h1>"}}
        extraction = {"fields": [{"name": "标题", "attribute": "text", "xpath": "//h1"}]}
        with patch("agent.detect_challenge", return_value=""), patch("agent.time.sleep"), \
             patch("agent.observe", return_value=detail_page), \
             patch("agent.inspect_dom", return_value=detail_page) as inspect, \
             patch("agent.ask_json", side_effect=[{"action": "inspect_dom", "scope": "page"}, {"fields": []}]), \
             patch("agent._prepare_extract", return_value=(extraction, [{"标题": "详情"}])):
            detail, sample = agent._probe_detail(base, "l1", "e1", Driver(), "采集详情", {"start_url": "https://example.org/"},
                                                 "deepseek", "fixture", "model", observation_budget=1)
        self.assertEqual(detail["_observation_requests"], 1)
        self.assertEqual(sample, {"标题": "详情"})
        inspect.assert_called_once()

    def test_multi_list_extract_rejects_unbounded_or_unregistered_groups(self):
        for ids in (["l1"] * 17, "l1", ["l_missing"]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                agent._prepare_extract({"elements": [], "lists": []},
                                       {"mode": "list", "list_ids": ids}, None)

    def test_dom_readonly_tool_generates_native_flow_without_recording_action(self):
        class Driver:
            def set_page_load_timeout(self, _): pass
            def get(self, _): pass
            def quit(self): pass
        page = {"url": "https://example.org/", "title": "", "text": "", "elements": [], "lists": []}
        dom_page = {**page, "dom": {"scope": "page", "html": "<h1>记录</h1>"},
                    "observation_stats": {"dom_candidates": 0}}
        extracted = {"mode": "single", "fields": [{"name": "标题", "attribute": "text", "xpath": "//h1"}],
                     "list_xpath": "", "next_xpath": ""}
        with patch("selenium.webdriver.Chrome", return_value=Driver()), \
             patch("agent.detect_challenge", return_value=""), patch("agent.time.sleep"), \
             patch("agent._wait_for_loading_placeholders", return_value=False), \
             patch("agent.observe", return_value=page), patch("agent.inspect_dom", return_value=dom_page) as inspect, \
             patch("agent._prepare_extract", return_value=(extracted, [{"标题": "记录"}])), \
             patch("agent.ask_json", side_effect=[{"action": "inspect_dom", "scope": "page"},
                                                   {"action": "extract", "mode": "single"}]):
            result = agent.explore("采集标题", {"start_url": page["url"], "task_name": "test"},
                                   "deepseek", "fixture", "model", lambda _: None)
        inspect.assert_called_once()
        self.assertEqual(result["trace"], [])

    def test_double_encoded_public_search_query_is_recognized(self):
        page = {"url": "https://example.org/search?keyword=%25E5%2590%2588%25E8%2582%25A5"}
        self.assertTrue(agent._search_progress({"keywords": ["合肥"]}, page, [], "")["ready"])

    def test_icon_control_uses_its_semantics_for_existing_risk_guard(self):
        check_action_target({"tag": "div", "clickable": True, "icon_hint": "search"}, "click")
        with self.assertRaises(ValueError):
            check_action_target({"tag": "div", "clickable": True, "icon_hint": "delete"}, "click")

    def test_native_list_waits_for_last_record_content(self):
        ready = "(//article)[last()][count(.//h2) > 0]"
        task = compile_task("https://example.org/", "test", [], {
            "mode": "list", "list_xpath": "//article", "ready_xpath": ready, "next_xpath": "",
            "fields": [{"name": "标题", "attribute": "text", "xpath": "//h2", "relative_xpath": "//h2"}]})
        loop = next(node for node in task["graph"] if node["option"] == 8)
        self.assertEqual(loop["parameters"]["waitElement"], ready)
        self.assertEqual(loop["parameters"]["waitElementTime"], 15)
        self.assertEqual(loop["parameters"]["waitElementIframeIndex"], "0")

    def test_model_cannot_request_unbounded_observation(self):
        class Driver:
            def set_page_load_timeout(self, timeout): pass
            def get(self, url): pass
            def quit(self): pass
        page = {"url": "https://example.org/", "title": "", "text": "", "elements": [], "lists": []}
        with patch("selenium.webdriver.Chrome", return_value=Driver()), \
             patch("agent.detect_challenge", return_value=""), \
             patch("agent._wait_for_loading_placeholders", return_value=False), \
             patch("agent.time.sleep"), patch("agent.observe", return_value=page) as scanner, \
             patch("agent.ask_json", return_value={"action": "observe", "focus": "controls"}) as model:
            with self.assertRaisesRegex(ValueError, "补充网页观察次数上限"):
                agent.explore("采集", {"start_url": page["url"], "task_name": "test"},
                              "deepseek", "fixture", "model", lambda _: None)
        self.assertEqual(model.call_count, agent.MAX_OBSERVATION_REQUESTS + 1)
        self.assertEqual(scanner.call_count, agent.MAX_OBSERVATION_REQUESTS + 1)


if __name__ == "__main__":
    unittest.main()
