"""Graph preservation, transactional edits, candidate binding and API confirmation."""
import copy
import io
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import _bootstrap
import editor
import server
from workflow import compile_task, validate_task
from debugger import BrowserDebugger
from types import SimpleNamespace


def example_task(url="http://127.0.0.1:12345/"):
    return compile_task(url, "商品采集", [
        {"action": "input", "xpath": "//input[@id='q']", "value": "羽毛球拍"},
        {"action": "click", "xpath": "//button[@id='search']"},
        {"action": "click", "xpath": "//button[@id='sales']"}],
        {"mode": "list", "list_xpath": "//article[@class='card']",
         "next_xpath": "//a[@rel='next']", "fields": [
             {"name": name, "attribute": attr, "xpath": f"//div[@class='{cls}']",
              "relative_xpath": f"//div[@class='{cls}']", "sample": ""}
             for name, cls, attr in (("商品名称", "title", "text"), ("价格", "price", "number"),
                                    ("销量", "sales", "text"), ("店铺名称", "shop", "text"))]}, page_limit=3)


def example_session(url="http://127.0.0.1:12345/"):
    task = example_task(url)
    return {"status": "preview", "edit_version": 1, "revision": 1,
            "events": [], "messages": [], "history": [],
            "plan": {"task_name": task["name"], "start_url": url, "steps": ["搜索、销量排序并提取"],
                     "fields": [f["name"] for f in task["outputParameters"]],
                     "pagination": "page_limit", "page_limit": 3, "needs_clarification": False},
            "result": {"task": task, "samples": [{"商品名称": "商品A", "价格": "19.50"}],
                       "mapping": [], "pagination": "page_limit", "page_limit": 3, "edit_version": 1,
                       "next_xpath": "//a[@rel='next']"}}


class EditorTests(unittest.TestCase):
    def test_page_and_name_edit_preserves_actions_and_structure(self):
        task = example_task()
        old = copy.deepcopy(task)
        changed = editor.apply_changes(task, [{"op": "set_page_limit", "page_limit": 5},
                                             {"op": "rename_field", "old_name": "价格", "name": "商品价格"}])
        self.assertEqual(task, old)
        self.assertEqual(changed["graph"][:5], old["graph"][:5])
        self.assertEqual([(n["option"],n["parentId"],n["sequence"]) for n in changed["graph"]],
                         [(n["option"],n["parentId"],n["sequence"]) for n in old["graph"]])
        self.assertEqual(editor.pagination_info(changed)["page_limit"], 5)
        self.assertTrue(all("商品价格" in [f["name"] for f in n["parameters"]["params"]]
                            for n in changed["graph"] if n["option"] == 3))
        self.assertEqual(changed["inputParameters"][-1]["value"], 4)
        self.assertIn("5", changed["inputParameters"][-1]["desc"])

    def test_field_update_and_remove_sync_both_pages(self):
        changed = editor.apply_changes(example_task(), [
            {"op":"upsert_field","node_id":6,"old_name":"价格","name":"价格","attribute":"text","default":"未知"},
            {"op":"remove_field","node_id":6,"name":"销量"}])
        for n in changed["graph"]:
            if n["option"] == 3:
                self.assertEqual(len(n["parameters"]["params"]),3)
                price = n["parameters"]["params"][1]
                self.assertEqual(price["contentType"], 0)
                self.assertEqual(price["default"], "未知")
        self.assertEqual(len(changed["outputParameters"]),3)

    def test_failed_patch_is_atomic(self):
        task = example_task()
        before = copy.deepcopy(task)
        with self.assertRaises(ValueError):
            editor.apply_changes(task,[{"op":"set_page_limit","page_limit":5},
                                       {"op":"update_node","node_id":2,"parameters":{"beforeJS":"alert(1)"}}])
        self.assertEqual(task,before)

    def test_ai_cannot_inject_xpath_or_script(self):
        for params in ({"xpath":"//button"},{"JS":"alert(1)"}):
            with self.assertRaises(ValueError):
                editor.apply_changes(example_task(),[{"op":"update_node","node_id":3,"parameters":params}],ai=True)

    def test_move_add_remove_rebuilds_native_references(self):
        task = editor.apply_changes(example_task(),[{"op":"move_node","node_id":4,"parent_id":0,"after_id":2}])
        self.assertEqual(task["graph"][0]["sequence"][:4],[1,2,4,3])
        self.assertEqual(task["graph"][4]["parameters"]["xpath"],"//button[@id='sales']")
        added = editor.apply_changes(task,[{"op":"add_node","parent_id":0,"after_id":2,"action":"input",
                                           "xpath":"//input[@id='extra']","value":"x"}])
        self.assertEqual(added["graph"][3]["parameters"]["value"],"x")
        removed = editor.apply_changes(added,[{"op":"remove_node","node_id":3}])
        self.assertTrue(validate_task(removed))
        with self.assertRaises(ValueError):
            editor.apply_changes(task,[{"op":"move_node","node_id":5,"parent_id":6}])

    def test_single_page_removes_pager_and_can_add_it_back(self):
        task = editor.apply_changes(example_task(),[{"op":"set_page_limit","page_limit":1}])
        self.assertEqual(editor.pagination_info(task)["pagination"],"none")
        task = editor.apply_changes(task,[{"op":"set_next_page","xpath":"//a[@rel='next']","page_limit":4}])
        self.assertEqual(editor.pagination_info(task)["page_limit"],4)
        self.assertTrue(validate_task(task))

    def test_candidate_field_requires_first_record_membership(self):
        snapshot = {"lists":[{"id":"l1","xpath":"//article[@class='card']","first_xpath":"/html/body/article[1]"}],
                    "elements":[{"id":"e1","tag":"div","xpath":"//div[@class='rating']",
                                 "absolute_xpath":"/html/body/article[1]/div[5]"}]}
        changed = editor.apply_changes(example_task(),[{"op":"upsert_field","node_id":6,"name":"评分",
                            "candidate_id":"e1","attribute":"text"}],snapshot=snapshot,ai=True)
        self.assertTrue(all(len(n["parameters"]["params"])==5 for n in changed["graph"] if n["option"]==3))
        snapshot["elements"][0]["absolute_xpath"]="/html/body/article[2]/div[5]"
        with self.assertRaises(ValueError):
            editor.apply_changes(example_task(),[{"op":"upsert_field","node_id":6,"name":"评分",
                            "candidate_id":"e1"}],snapshot=snapshot,ai=True)

    def test_input_container_resolves_real_editable(self):
        snapshot={"elements":[{"id":"e1","tag":"div","xpath":"//div[@id='wrap']",
                               "editable_xpath":"//input[@id='q']"}],"lists":[]}
        task=editor.apply_changes(example_task(),[{"op":"bind_candidate","node_id":2,"candidate_id":"e1"}],snapshot=snapshot,ai=True)
        self.assertEqual(task["graph"][2]["parameters"]["xpath"],"//input[@id='q']")

    def test_new_extract_in_list_uses_relative_candidate_path(self):
        task = example_task()
        original = copy.deepcopy(task)
        snapshot = {"lists": [{"id": "l1", "xpath": "//article[@class='card']",
                               "first_xpath": "/html/body/article[1]"}],
                    "elements": [{"id": "e1", "tag": "span", "xpath": "//span[@id='rating-1']",
                                  "absolute_xpath": "/html/body/article[1]/span[1]"}]}
        changed = editor.apply_changes(task, [{"op": "add_node", "parent_id": 5,
            "after_id": 6, "action": "extract", "candidate_id": "e1", "name": "评分"}],
            snapshot=snapshot, ai=True)
        field = changed["graph"][7]["parameters"]["params"][0]
        self.assertTrue(field["relative"])
        self.assertEqual(field["relativeXPath"], "/span[1]")
        self.assertEqual(changed["graph"][7]["parentId"], 5)
        self.assertEqual(changed["graph"][5]["sequence"], [6, 7])
        self.assertIn("评分", [f["name"] for f in changed["outputParameters"]])
        self.assertEqual(task, original)
        self.assertTrue(validate_task(changed))

    def test_new_extract_rejects_candidate_outside_first_record_atomically(self):
        task = example_task()
        original = copy.deepcopy(task)
        snapshot = {"lists": [{"id": "l1", "xpath": "//article[@class='card']",
                               "first_xpath": "/html/body/article[1]"}],
                    "elements": [{"id": "e1", "tag": "span", "xpath": "//span[@id='rating-2']",
                                  "absolute_xpath": "/html/body/article[2]/span[1]"}]}
        with self.assertRaisesRegex(ValueError, "第一条记录"):
            editor.apply_changes(task, [{"op": "add_node", "parent_id": 5,
                "after_id": 6, "action": "extract", "candidate_id": "e1", "name": "评分"}],
                snapshot=snapshot, ai=True)
        self.assertEqual(task, original)

    def test_original_designer_display_ids_are_normalized(self):
        task=example_task()
        for n in task["graph"]:
            n["id"]+=30
            n["parentId"]+=30
        native=editor.normalize_native_task(task)
        self.assertTrue(validate_task(native))
        self.assertEqual(native["graph"][6]["parentId"],5)

    def test_relative_extraction_cannot_be_moved_outside_list(self):
        with self.assertRaises(ValueError):
            editor.apply_changes(example_task(),[{"op":"move_node","node_id":6,"parent_id":0}])

    def test_condition_branch_cannot_be_moved_outside_judge(self):
        with self.assertRaises(ValueError):
            editor.apply_changes(example_task(),[{"op":"move_node","node_id":9,"parent_id":0}])

    def test_next_binding_updates_condition_and_click_together(self):
        snapshot={"elements":[{"id":"e1","tag":"a","is_next":True,"text":"下一页","xpath":"//a[@id='next']"}],"lists":[]}
        changed=editor.apply_changes(example_task(),[{"op":"set_next_page","candidate_id":"e1","page_limit":5}],snapshot=snapshot,ai=True)
        info=editor.pagination_info(changed)
        self.assertEqual(info['page_limit'],5)
        self.assertIn("@id='next'",info['next_xpath'])
        self.assertEqual(changed['graph'][info['branch_id']]['parameters']['value'],info['next_xpath'])


class DebuggerTests(unittest.TestCase):
    def test_intercepted_trial_invalidates_old_candidates(self):
        from selenium.common.exceptions import ElementClickInterceptedException
        debugger = BrowserDebugger("http://example.org")
        def intercepted():
            raise ElementClickInterceptedException("login overlay")
        target = SimpleNamespace(tag_name="button", text="搜索", get_attribute=lambda _: "",
                                 click=intercepted)
        debugger.driver = SimpleNamespace(find_element=lambda *_: target,
            execute_script=lambda *_: None, window_handles=["main"], quit=lambda: None)
        debugger.snapshot = {"elements": []}
        debugger.observation_id = "old"
        debugger.snapshot_url = "http://example.org"
        try:
            with patch.object(debugger, "_status", return_value={"active": True, "challenge": ""}):
                outcome = debugger.trial(example_task(), 3)
            self.assertIn("遮罩拦截", outcome["message"])
            self.assertIsNone(debugger.snapshot)
            self.assertIsNone(debugger.observation_id)
            self.assertIsNone(debugger.snapshot_url)
        finally:
            debugger.dispose()

    def test_driver_service_exit_becomes_friendly_close_and_releases_browser(self):
        debugger=BrowserDebugger('http://example.org')
        debugger.driver=SimpleNamespace(service=SimpleNamespace(process=SimpleNamespace(poll=lambda:0)),quit=lambda:None)
        def fail():
            raise RuntimeError('driver transport unavailable')
        try:
            with self.assertRaisesRegex(ValueError,'浏览器已关闭'):
                debugger._call(fail)
            self.assertIsNone(debugger.driver)
        finally:
            debugger.dispose()

    def test_stale_observation_rejected_before_candidate_binding(self):
        debugger=BrowserDebugger('http://example.org')
        debugger.driver=SimpleNamespace(quit=lambda:None)
        debugger.snapshot={}; debugger.observation_id='new'
        try:
            with self.assertRaisesRegex(ValueError,'候选已过期'):
                debugger.patch(example_task(),[], 'old')
        finally:
            debugger.dispose()


class EditorAPITests(unittest.TestCase):
    def setUp(self):
        self.sid="editor-test"
        server.SESSIONS[self.sid]=example_session()
        self.web=ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        threading.Thread(target=self.web.serve_forever,daemon=True).start()
        self.base=f"http://127.0.0.1:{self.web.server_port}"

    def tearDown(self):
        self.web.shutdown(); self.web.server_close()
        server.SESSIONS.pop(self.sid,None)

    def post(self,path,**data):
        req=Request(self.base+'/api/editor/'+path,json.dumps({"session":self.sid,"version":1,**data}).encode(),
                    {"Content-Type":"application/json"})
        with urlopen(req,timeout=10) as r:
            return json.load(r)

    def test_proposal_does_not_apply_until_confirmation_and_undo_restores(self):
        before=copy.deepcopy(server.SESSIONS[self.sid]["result"]["task"])
        suggestion={"summary":"只改页数", "questions":[], "operations":[{"op":"set_page_limit","page_limit":5}]}
        with patch('server.propose_changes',return_value=suggestion):
            state=self.post('propose',message='采集五页',api_key='test')
        self.assertEqual(state["result"]["task"],before)
        self.assertNotIn('candidate_task',state['pending_edit'])
        self.assertNotIn('undo_stack',state)
        state=self.post('apply',proposal_id=state['pending_edit']['proposal_id'])
        self.assertEqual(state['result']['page_limit'],5)
        self.assertTrue(state['result']['samples_stale'])
        self.assertEqual(state['result']['samples'],[])
        state=self.post('undo',version=2)
        self.assertEqual(state['result']['task'],before)
        self.assertEqual(state['edit_version'],3)

    def test_manual_change_invalidates_pending_and_old_versions(self):
        suggestion={"summary":"改页数", "questions":[], "operations":[{"op":"set_page_limit","page_limit":5}]}
        with patch('server.propose_changes',return_value=suggestion):
            self.post('propose',message='五页',api_key='test')
        state=self.post('patch',operations=[{"op":"set_page_limit","page_limit":4}])
        self.assertIsNone(state['pending_edit'])
        with self.assertRaises(HTTPError): self.post('apply')
        with self.assertRaises(HTTPError): self.post('apply',version=2)

    def test_candidate_binding_requires_browser_and_questions_keep_graph(self):
        with self.assertRaises(HTTPError):
            self.post('patch',operations=[{"op":"bind_candidate","node_id":2,"candidate_id":"e1"}])
        before=copy.deepcopy(server.SESSIONS[self.sid]['result']['task'])
        with patch('server.propose_changes',return_value={"summary":"需要页面", "questions":["哪个价格？"],"operations":[]}):
            state=self.post('propose',message='换价格',api_key='test')
        self.assertEqual(state['result']['task'],before)
        self.assertEqual(state['pending_edit']['questions'],['哪个价格？'])

    def test_pull_native_is_read_only_and_versioned(self):
        server.SESSIONS[self.sid]['imported_id']=42
        native=example_task()
        native['id']=42
        native['graph'][2]['parameters']['value']='网球拍'
        with patch('server.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(native).encode())) as fetch:
            # Call directly so the mock only intercepts the EasySpider request.
            state=server.handle_editor('pull-native',{'session':self.sid,'version':1},server.SESSIONS[self.sid])
        self.assertEqual(fetch.call_args.args[0],'http://127.0.0.1:8074/queryTask?id=42')
        self.assertEqual(state['result']['task']['graph'][2]['parameters']['value'],'网球拍')
        self.assertEqual(state['edit_version'],2)

    def test_stale_debug_node_index_is_rejected_before_browser_access(self):
        self.post('patch',operations=[{'op':'set_page_limit','page_limit':4}])
        with self.assertRaises(HTTPError) as failure:
            self.post('debug/trial',node_id=6)
        self.assertIn('版本已变化',json.loads(failure.exception.read())['error'])

    def test_same_graph_version_cannot_confirm_another_tab_suggestion(self):
        suggestion={"summary":"改页数", "questions":[], "operations":[{"op":"set_page_limit","page_limit":5}]}
        with patch('server.propose_changes',return_value=suggestion):
            first=self.post('propose',message='五页',api_key='test')['pending_edit']['proposal_id']
            second=self.post('propose',message='再给建议',api_key='test')['pending_edit']['proposal_id']
        self.assertNotEqual(first,second)
        with self.assertRaises(HTTPError): self.post('apply',proposal_id=first)
        state=self.post('apply',proposal_id=second)
        self.assertEqual(state['result']['page_limit'],5)


if __name__=='__main__':
    unittest.main()
