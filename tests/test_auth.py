"""State transition and isolation checks for the manual-login HTTP handoff."""
import json
import threading
import time
import unittest
import uuid
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import ANY, patch
from urllib.request import Request, urlopen

import _bootstrap
import auth
import agent
import server
from agent import BROWSER_CLOSED_MESSAGE, BrowserClosedError


def post(base, path, payload):
    request = Request(base + path, json.dumps(payload).encode('utf-8'),
                      {'Content-Type': 'application/json'}, method='POST')
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def state(base, sid):
    with urlopen(base + '/api/state?session=' + sid, timeout=5) as response:
        return json.load(response)


class AuthenticationTests(unittest.TestCase):
    def test_invalid_session_from_driver_becomes_browser_closed_error(self):
        class ClosedDriver:
            def set_page_load_timeout(self, _timeout):
                pass
            def get(self, _url):
                raise RuntimeError('invalid session id: session deleted as the browser has closed')
            def quit(self):
                pass

        with patch('selenium.webdriver.Chrome', return_value=ClosedDriver()), \
             self.assertRaisesRegex(BrowserClosedError, '自动控制浏览器已关闭'):
            agent.explore(
                'collect products',
                {'start_url': 'https://example.org/list', 'task_name': 'products',
                 'pagination': 'none', 'requires_login': False, 'requires_detail': False},
                'deepseek', 'key', 'model', lambda _message: None, max_actions=1)

    def test_invalid_candidate_refreshes_snapshot_before_retry(self):
        class FakeElement:
            tag_name = 'h2'
            text = '商品 A'
            def get_attribute(self, name):
                return ''

        class FakeRow:
            def find_element(self, *_args):
                return FakeElement()

        class FakeDriver:
            current_url = 'https://example.org/list'
            window_handles = ['main']
            def set_page_load_timeout(self, _timeout):
                pass
            def get(self, _url):
                pass
            def find_elements(self, *_args):
                return [FakeRow()]
            def quit(self):
                pass

        initial = {'url': 'https://example.org/list', 'title': 'List', 'text': '',
                   'elements': [], 'lists': [], 'next_candidates': []}
        refreshed = {'url': 'https://example.org/list', 'title': 'List', 'text': '',
                     'elements': [{'id': 'e1', 'tag': 'h2', 'text': '商品 A',
                                   'absolute_xpath': '/html/body/article[1]/h2',
                                   'xpath': '//h2'}],
                     'lists': [{'id': 'l1', 'xpath': '//article',
                                'first_xpath': '/html/body/article[1]', 'count': 1}],
                     'next_candidates': []}
        decisions = [
            {'action': 'extract', 'mode': 'list', 'list_id': 'l10',
             'fields': [{'name': 'title', 'candidate_id': 'e10', 'attribute': 'text'}]},
            {'action': 'extract', 'mode': 'list', 'list_id': 'l1',
             'fields': [{'name': 'title', 'candidate_id': 'e1', 'attribute': 'text'}]},
        ]
        progress = []
        profile = _bootstrap.ROOT / 'profiles' / 'test_profile'
        gate = SimpleNamespace(profile_dir=profile, used=False,
                               acquire=lambda: None, release=lambda: None)
        with (patch('selenium.webdriver.Chrome', return_value=FakeDriver()),
              patch.object(agent, 'detect_challenge', return_value=''),
              patch.object(agent, 'observe', side_effect=[initial, refreshed]),
              patch.object(agent, 'ask_json', side_effect=decisions) as ask,
              patch.object(agent, 'compile_task', return_value={'graph': []})):
            result = agent.explore(
                'collect products',
                {'start_url': 'https://example.org/list', 'task_name': 'products',
                 'pagination': 'none', 'requires_login': False, 'requires_detail': False},
                'deepseek', 'key', 'model', progress.append, max_actions=3,
                login_gate=gate)
        self.assertEqual(result['samples'], [{'title': '商品 A'}])
        self.assertEqual(result['profile_dir'], str(profile))
        self.assertEqual(ask.call_count, 2)
        first_prompt = json.loads(ask.call_args_list[0].args[4])
        second_prompt = json.loads(ask.call_args_list[1].args[4])
        self.assertEqual(first_prompt['available_candidate_ids']['lists'], [])
        self.assertEqual(second_prompt['available_candidate_ids']['lists'], ['l1'])

    def test_browser_closed_error_is_sanitized_for_server_state(self):
        sid = uuid.uuid4().hex
        server.SESSIONS[sid] = {'status': 'planned',
                                'plan': {'start_url': 'https://example.org/list', 'pagination': 'none'},
                                'events': [], 'history': [{'request': 'products'}],
                                'messages': [], 'revision': 1}
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{httpd.server_port}'
        try:
            with (patch.object(server, 'resolve_model_config', return_value=('deepseek', 'stub', 'test')),
                  patch.object(server, 'explore', side_effect=BrowserClosedError(BROWSER_CLOSED_MESSAGE))):
                self.assertEqual(post(base, '/api/run', {'session': sid})['status'], 'running')
                deadline = time.monotonic() + 5
                while server.SESSIONS[sid]['status'] not in ('closed', 'error') and time.monotonic() < deadline:
                    time.sleep(0.05)
                current = state(base, sid)
                self.assertEqual(current['status'], 'closed')
                self.assertEqual(current['error'], BROWSER_CLOSED_MESSAGE)
                self.assertNotIn('invalid session id', current['error'].lower())
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)
            server.SESSIONS.pop(sid, None)
            server.LOGIN_GATES.pop(sid, None)

    def test_authenticated_detail_probe_does_not_send_screenshot(self):
        class Driver:
            window_handles = ['original']
            def find_element(self, *_args):
                return SimpleNamespace(click=lambda: None)

        snapshot = {'lists': [{'id': 'l1', 'first_xpath': '/html/body/article[1]'}],
                    'elements': [{'id': 'e1', 'tag': 'a',
                                  'absolute_xpath': '/html/body/article[1]/a[1]',
                                  'xpath': '//article[1]/a[1]'}]}
        observed = {'url': 'https://example.org/detail', 'elements': [],
                    'lists': [], 'next_candidates': []}
        gate = SimpleNamespace(used=True)
        with (patch.object(agent, 'detect_challenge', return_value=''),
              patch.object(agent, 'observe', return_value=observed) as observe,
              patch.object(agent, '_compact', return_value={}),
              patch.object(agent, 'ask_json', return_value={'fields': []}),
              patch.object(agent, '_prepare_extract',
                           return_value=({'fields': []}, [{}])),
              patch.object(agent.time, 'sleep')):
            agent._probe_detail(snapshot, 'l1', 'e1', Driver(), 'request',
                                {'start_url': 'https://example.org'}, 'gpt', 'key',
                                'model', gate)
        observe.assert_called_once_with(ANY, with_screenshot=False)

    def test_profile_location_is_site_specific(self):
        self.assertEqual(auth.profile_path('https://example.org/one'),
                         auth.profile_path('https://example.org/two'))
        self.assertNotEqual(auth.profile_path('https://example.org'),
                            auth.profile_path('https://another.example.org'))

    def test_middleware_login_overlay_is_detected_before_click(self):
        class Driver:
            current_url = 'https://example.org/orders'
            def execute_script(self, script):
                if 'j_middleware_frame_widget' in script:
                    return True
                raise AssertionError('the overlay probe should short-circuit')

        self.assertEqual(auth.detect_challenge(Driver(), 'https://example.org/orders'),
                         '页面出现登录或验证遮罩层')

    def test_intercepted_click_can_be_delegated_to_human_login(self):
        gate = SimpleNamespace(wait_for_login=lambda driver, url, reason: setattr(gate, 'used', True))
        driver = SimpleNamespace(current_url='https://example.org/orders')
        with (patch.object(agent, 'detect_challenge', return_value='login overlay'),
              patch.object(agent, 'ask_json', return_value={'action': 'retry'})):
            action = agent._recover_intercepted_click(
                'deepseek', 'stub', 'model', driver, gate,
                'https://example.org/orders', lambda _message: None,
                'element click intercepted')
        self.assertEqual(action, 'human_login')
        self.assertTrue(gate.used)

    def test_intercepted_click_uses_model_retry_only_without_auth_signal(self):
        driver = SimpleNamespace(current_url='https://example.org/list')
        with (patch.object(agent, 'detect_challenge', return_value=''),
              patch.object(agent, 'ask_json', return_value={'action': 'retry'})):
            action = agent._recover_intercepted_click(
                'deepseek', 'stub', 'model', driver, None,
                'https://example.org/list', lambda _message: None,
                'element click intercepted by animation')
        self.assertEqual(action, 'retry')

    def test_runtime_recovery_redacts_secret_like_error_text(self):
        driver = SimpleNamespace(current_url='https://example.org/list')
        with (patch.object(agent, 'detect_challenge', return_value=''),
              patch.object(agent, 'ask_json', return_value={'action': 'abort'}) as review):
            agent._recover_intercepted_click(
                'deepseek', 'stub', 'model', driver, None,
                'https://example.org/list', lambda _message: None,
                'password=local-only token=abc123 element click intercepted')
        prompt = review.call_args.args[4]
        self.assertNotIn('local-only', prompt)
        self.assertNotIn('abc123', prompt)

    def test_server_waits_for_user_then_resumes_same_run(self):
        sid = uuid.uuid4().hex
        plan = {'start_url': 'http://127.0.0.1:12345/orders', 'pagination': 'none'}
        server.SESSIONS[sid] = {'status': 'planned', 'plan': plan, 'events': [],
                                'history': [{'request': 'orders'}], 'messages': [], 'revision': 1}
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{httpd.server_port}'

        class FakeDriver:
            authenticated = False
            def get(self, _url):
                self.authenticated = True

        def fake_explore(_request, _plan, _provider, _key, _model, _progress,
                         max_actions=30, login_gate=None):
            driver = FakeDriver()
            login_gate.wait_for_login(driver, plan['start_url'], '需要登录')
            self.assertTrue(driver.authenticated)
            return {'task': {}, 'samples': []}

        try:
            with (patch.object(server, 'resolve_model_config', return_value=('deepseek', 'stub', 'test')),
                  patch.object(server, 'explore', side_effect=fake_explore),
                  patch.object(auth, 'detect_challenge', return_value='')):
                self.assertEqual(post(base, '/api/run', {'session': sid})['status'], 'running')
                deadline = time.monotonic() + 5
                while state(base, sid)['status'] != 'awaiting_login' and time.monotonic() < deadline:
                    time.sleep(0.05)
                waiting = state(base, sid)
                self.assertEqual(waiting['status'], 'awaiting_login')
                self.assertNotIn('history', waiting)
                self.assertNotIn('login_gate', waiting)
                post(base, '/api/login-complete', {'session': sid})
                deadline = time.monotonic() + 5
                while state(base, sid)['status'] != 'preview' and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(state(base, sid)['status'], 'preview')
                self.assertNotIn(sid, server.LOGIN_GATES)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)
            server.SESSIONS.pop(sid, None)
            server.LOGIN_GATES.pop(sid, None)


if __name__ == '__main__':
    unittest.main()
