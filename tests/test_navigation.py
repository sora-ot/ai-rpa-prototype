"""Search redirects must preserve the replay trace without inventing a login gate."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import _bootstrap
import agent


class NavigationTests(unittest.TestCase):
    def test_cross_site_search_with_login_plan_preserves_actions(self):
        start = "https://news.example.org/"
        results = "https://search.example.net/news?word=吉林大学"

        class Target:
            def send_keys(self, *_): pass
            def click(self): driver.current_url = results

        class Driver:
            current_url = start
            window_handles = ["main"]
            def set_page_load_timeout(self, *_): pass
            def get(self, url): self.current_url = url
            def find_element(self, *_): return Target()
            def quit(self): pass

        driver = Driver()
        elements = [{"id": "e1", "tag": "input", "editable": True,
                     "type": "text", "xpath": "//input", "editable_xpath": "//input",
                     "absolute_xpath": "/html/body/input"},
                    {"id": "e2", "tag": "button", "text": "搜索", "xpath": "//button",
                     "absolute_xpath": "/html/body/button"}]

        def snapshot(*_, **__):
            return {"url": driver.current_url, "title": "新闻", "text": "", "elements": elements,
                    "lists": [], "next_candidates": []}

        extraction = {"mode": "single", "fields": [
            {"name": "标题", "xpath": "//h1", "attribute": "text"}], "next_xpath": ""}
        gate = SimpleNamespace(profile_dir=_bootstrap.ROOT / "profiles/test_navigation",
                               used=False, acquire=Mock(), release=Mock(), wait_for_login=Mock())
        decisions = [{"action": "input", "candidate_id": "e1", "value": "吉林大学"},
                     {"action": "click", "candidate_id": "e2"},
                     {"action": "extract", "mode": "single"}]
        with patch("selenium.webdriver.Chrome", return_value=driver), \
             patch("agent.time.sleep"), patch("agent.detect_challenge", return_value=""), \
             patch("agent._wait_for_loading_placeholders", return_value=False), \
             patch("agent.observe", side_effect=snapshot), patch("agent.risk_review_enabled", return_value=False), \
             patch("agent._prepare_extract", return_value=(extraction, [{"标题": "吉林大学新闻"}])), \
             patch("agent.ask_json", side_effect=decisions) as ask:
            result = agent.explore("搜索吉林大学新闻", {"start_url": start, "task_name": "新闻",
                "requires_login": True, "keywords": ["吉林大学"], "pagination": "none"},
                "deepseek", "fixture", "model", lambda _: None, login_gate=gate)
        gate.wait_for_login.assert_not_called()
        context = json.loads(ask.call_args.args[4])
        self.assertTrue(context["navigation_context"]["cross_host"])
        self.assertTrue(context["search_progress"]["ready"])
        self.assertEqual([a["action"] for a in result["trace"]], ["input", "click"])
        self.assertEqual([n["option"] for n in result["task"]["graph"]].count(4), 1)
        self.assertEqual(result["task"]["url"], start)

    def test_navigation_keeps_normal_web_url_validation(self):
        for url in ("file:///C:/private", "javascript:alert(1)", "data:text/html,hello",
                    "https://username:secret@example.org/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                agent._navigation_context({"start_url": "https://news.example.org/"}, {"url": url})

    def test_cross_site_page_does_not_prove_a_search(self):
        page = {"url": "https://elsewhere.example.net/", "text": "首页推荐", "lists": []}
        self.assertFalse(agent._search_progress({"keywords": ["吉林大学"]}, page, [], "")["ready"])


if __name__ == "__main__":
    unittest.main()
