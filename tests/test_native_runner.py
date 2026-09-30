"""Regression checks for using the design-time profile in native execution."""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import _bootstrap
from run_native import filter_nonrecord_cards, resolve_profile, run
from workflow import compile_task


def example_task():
    return compile_task("https://example.org/products", "Products", [], {
        "mode": "list", "list_xpath": "//article[@class='card']",
        "fields": [{"name": "title", "attribute": "text", "xpath": "//h2",
                    "relative_xpath": "//h2", "sample": "A"}],
        "next_xpath": "//a[@rel='next']"}, page_limit=3)


class NativeRunnerTests(unittest.TestCase):
    def test_older_list_task_can_exclude_nonrecord_cards(self):
        original = example_task()
        repaired = filter_nonrecord_cards(original)
        list_nodes = [node for node in repaired["graph"] if node["option"] == 8
                      and node["parameters"].get("loopType") == 1]
        self.assertEqual(len(list_nodes), 2)
        self.assertTrue(all(node["parameters"]["xpath"].endswith("[.//h2]")
                            for node in list_nodes))
        page_loop = next(node for node in repaired["graph"] if node["option"] == 8
                         and node["parameters"].get("loopType") == 0)
        self.assertTrue(page_loop["parameters"]["exitElement"].endswith("[.//h2]"))
        self.assertEqual(original["graph"][2]["parameters"]["xpath"],
                         "//article[@class='card']")

    def test_nonrecord_filter_can_be_applied_twice(self):
        once = filter_nonrecord_cards(example_task())
        twice = filter_nonrecord_cards(once)
        self.assertEqual(twice, once)

    def test_filter_preserves_guard_using_another_collected_field(self):
        task = compile_task("https://example.org/products", "Products", [], {
            "mode": "list", "list_xpath": "//article[@class='card']",
            "fields": [
                {"name": "title", "attribute": "text", "xpath": "//h2",
                 "relative_xpath": "//h2", "sample": "A"},
                {"name": "price", "attribute": "text", "xpath": "//span[@class='price']",
                 "relative_xpath": "//span[@class='price']", "sample": "10"}],
            "next_xpath": "//a[@rel='next']"}, page_limit=3)
        base = "//article[@class='card']"
        guarded = f"({base})[.//span[@class='price']]"
        for node in task["graph"]:
            params = node["parameters"]
            if params.get("xpath") == base and node["option"] == 8:
                params["xpath"] = guarded
            if params.get("exitElement") == base:
                params["exitElement"] = guarded
        self.assertEqual(filter_nonrecord_cards(task), task)

    def test_dry_run_passes_ai_profile_explicitly_to_native_executor(self):
        task_file = Mock()
        task_file.read_text.return_value = json.dumps(example_task(), ensure_ascii=False)
        profile = _bootstrap.ROOT / "profiles" / "test_profile"
        with (patch("run_native.verify_bundled_executor"),
              patch("run_native.resolve_profile", return_value=profile)):
            result = run(task_file, None, dry_run=True)
        command = result["command"]
        self.assertEqual(command[command.index("--user_data") + 1], "1")
        self.assertEqual(command[command.index("--user_folder") + 1], str(profile))
        self.assertEqual(result["profile"], str(profile))

    def test_profile_must_stay_inside_prototype(self):
        outside = _bootstrap.ROOT.parent / "other_profile"
        with self.assertRaisesRegex(ValueError, "资料目录必须位于"):
            resolve_profile({"url": "https://example.org"}, str(outside))


if __name__ == "__main__":
    unittest.main()
