"""Original task storage, saved-graph conflicts and advanced-node preservation."""
import copy
import json
import io
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import _bootstrap
import editor
import server
from native_bridge import NativeClient, NativeSavePending, fingerprint
from test_editor import example_task, example_session


class NativeBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (_bootstrap.ROOT / 'browser_tmp').mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix='native-storage-', dir=_bootstrap.ROOT / 'browser_tmp')
        cls.addClassCleanup(cls.temporary.cleanup)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        source = _bootstrap.ROOT.parent / 'EasySpider' / 'ElectronJS'
        cls.log = (Path(cls.temporary.name) / 'fixture.log').open('w', encoding='utf-8')
        cls.addClassCleanup(cls.log.close)
        cls.process = subprocess.Popen(['node', str(Path(__file__).with_name('native_storage_fixture.js')),
            str(source), cls.temporary.name, str(port)], stdin=subprocess.PIPE, stdout=cls.log, stderr=cls.log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        cls.addClassCleanup(cls.stop_process)
        cls.client = NativeClient(f'http://127.0.0.1:{port}')
        for _ in range(100):
            if (Path(cls.temporary.name) / 'config.json').exists():
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2): break
                except OSError: pass
            if cls.process.poll() is not None:
                cls.log.flush(); raise RuntimeError((Path(cls.temporary.name) / 'fixture.log').read_text(encoding='utf-8'))
            time.sleep(.05)
        else:
            raise RuntimeError('Native storage fixture startup timeout')

    @classmethod
    def stop_process(cls):
        if cls.process.poll() is None:
            try:
                cls.process.communicate(b'exit', timeout=10)
            except subprocess.TimeoutExpired:
                cls.process.kill(); cls.process.wait(timeout=5)

    def setUp(self):
        self.sid = 'native-bridge-test'
        self.state = server.SESSIONS[self.sid] = example_session()
        self.factory = patch('server.native_client', return_value=self.client)
        self.factory.start()
        self.connection = patch('server.NativeDesigner.connect', return_value=False)
        self.connection.start()

    def tearDown(self):
        self.connection.stop(); self.factory.stop()
        server.SESSIONS.pop(self.sid, None)
        attached = server.NATIVE_DESIGNERS.pop(self.sid, None)
        if attached: attached.disconnect()

    def call(self, action, **data):
        return server.handle_editor(action, {'session':self.sid, 'version':self.state['edit_version'], **data}, self.state)

    def open(self):
        self.call('native/open')
        return self.state['imported_id']

    def propose(self, operations):
        with patch('server.propose_changes', return_value={'summary':'局部修改', 'questions':[], 'operations':operations}):
            return self.call('propose', message='修改采集', api_key='test')['pending_edit']['proposal_id']

    def test_native_open_creates_once_and_reopening_uses_same_id(self):
        task_id = self.open()
        self.assertFalse(self.state['native']['active'])
        self.assertIn('启动', self.state['native']['message'])
        self.assertEqual(self.open(), task_id)
        self.assertEqual(self.client.read(task_id)['id'], task_id)

    def test_uncertain_initial_save_retains_id_without_duplicate_creation(self):
        task_id, _ = self.client.write(self.state['result']['task'])
        with patch.object(self.client, 'write', side_effect=NativeSavePending(task_id)):
            self.call('native/open')
        self.assertEqual(self.state['imported_id'], task_id)
        with patch.object(self.client, 'write') as create:
            self.call('native/open')
            create.assert_not_called()

    def test_read_retries_partial_json_during_original_async_save(self):
        task = example_task(); task['id'] = 12
        with patch('native_bridge.urllib.request.urlopen', side_effect=[io.BytesIO(b''),
                io.BytesIO(b'{"id":'), io.BytesIO(json.dumps(task).encode('utf-8'))]), \
             patch('native_bridge.time.sleep'):
            self.assertEqual(self.client.read(12), task)

    def test_proposal_reads_latest_manual_changes_then_updates_same_task(self):
        task_id = self.open()
        manual = self.client.read(task_id)
        manual['graph'][2]['parameters']['value'] = '网球拍'
        self.client.write(manual, task_id)
        proposal = self.propose([{'op':'set_page_limit', 'page_limit':5}])
        self.assertEqual(self.state['result']['task']['graph'][2]['parameters']['value'], '网球拍')
        self.assertEqual(editor.pagination_info(self.client.read(task_id))['page_limit'], 3)
        self.call('apply', proposal_id=proposal)
        saved = self.client.read(task_id)
        self.assertEqual(saved['id'], task_id)
        self.assertEqual(saved['graph'][2]['parameters']['value'], '网球拍')
        self.assertEqual(editor.pagination_info(saved)['page_limit'], 5)

    def test_reopening_after_ai_and_manual_edits_keeps_one_task_and_latest_graph(self):
        task_id = self.open()
        proposal = self.propose([{'op':'set_page_limit', 'page_limit':7}])
        self.call('apply', proposal_id=proposal)
        manual = self.client.read(task_id)
        manual['graph'][2]['parameters']['value'] = '原版手工更新'
        self.client.write(manual, task_id)
        with patch.object(self.client, 'write', wraps=self.client.write) as save:
            self.call('native/open')
            save.assert_not_called()
        self.assertEqual(self.state['imported_id'], task_id)
        self.assertEqual(self.state['result']['task']['graph'][2]['parameters']['value'], '原版手工更新')
        self.assertEqual(editor.pagination_info(self.client.read(task_id))['page_limit'], 7)

    def test_manual_save_after_proposal_blocks_overwrite_and_undo_conflict(self):
        task_id = self.open()
        proposal = self.propose([{'op':'set_page_limit', 'page_limit':5}])
        manual = self.client.read(task_id); manual['name'] = '手工新名称'
        self.client.write(manual, task_id)
        with self.assertRaisesRegex(ValueError, '流程已变化'):
            self.call('apply', proposal_id=proposal)
        self.assertEqual(self.client.read(task_id)['name'], '手工新名称')
        with self.assertRaisesRegex(ValueError, '流程已变化'):
            self.call('undo')

    def test_undo_writes_back_same_task_and_preserves_manual_baseline(self):
        task_id = self.open()
        proposal = self.propose([{'op':'set_page_limit', 'page_limit':5}])
        self.call('apply', proposal_id=proposal)
        self.call('undo')
        self.assertEqual(editor.pagination_info(self.client.read(task_id))['page_limit'], 3)

    def test_original_advanced_node_and_field_metadata_survive_ai_edit(self):
        task_id = self.open()
        manual = self.client.read(task_id)
        script = copy.deepcopy(manual['graph'][4])
        script.update({'index':len(manual['graph']), 'id':len(manual['graph']), 'option':5,
                       'title':'用户自定义操作', 'parentId':0, 'sequence':[]})
        script['parameters'] = {'code':'return 123;', 'codeMode':0, 'wait':1, 'customNativeValue':'keep'}
        manual['graph'].append(script); manual['graph'][0]['sequence'].append(script['index'])
        manual['outputParameters'].reverse()
        manual['outputParameters'][0].update({'type':'custom-native-type', 'recordASField':0})
        manual['outputParameters'].append({'name':script['title'], 'type':'int', 'recordASField':1, 'id':4})
        manual['links'] = 'https://example.com/manual-links'
        manual['inputParameters'][0]['desc'] = '用户自定义网址说明'
        self.client.write(manual, task_id)
        proposal = self.propose([{'op':'set_page_limit', 'page_limit':5}])
        self.call('apply', proposal_id=proposal)
        saved = self.client.read(task_id)
        preserved = next(node for node in saved['graph'] if node['option']==5)
        self.assertEqual(preserved['parameters'], script['parameters'])
        self.assertEqual([field['name'] for field in saved['outputParameters']], [field['name'] for field in manual['outputParameters']])
        self.assertEqual(saved['outputParameters'][0]['type'], 'custom-native-type')
        self.assertEqual(saved['outputParameters'][0]['recordASField'], 0)
        self.assertEqual(saved['outputParameters'][-1]['name'], script['title'])
        self.assertEqual(saved['links'], manual['links'])
        self.assertEqual(saved['inputParameters'][0]['desc'], '用户自定义网址说明')

    def test_existing_custom_field_reader_survives_default_edit(self):
        task = editor.normalize_native_task(example_task(), allow_native=True)
        field = task['graph'][6]['parameters']['params'][0]
        field.update({'JS':'return customReader();', 'contentType':6, 'desc':'用户说明', 'paraType':'int'})
        changed = editor.apply_changes(task, [{'op':'upsert_field','node_id':6,
            'old_name':field['name'], 'name':field['name'], 'default':'无结果'}], ai=True, allow_native=True)
        after = changed['graph'][6]['parameters']['params'][0]
        self.assertEqual(after, {**field, 'default':'无结果'})

    def test_native_graph_rejects_cycles_and_ai_script_injection(self):
        malformed = example_task(); malformed['graph'][5]['sequence'].append(5)
        with self.assertRaises(ValueError): editor.normalize_native_task(malformed, allow_native=True)
        native = editor.normalize_native_task(example_task(), allow_native=True)
        with self.assertRaisesRegex(ValueError, 'XPath 或脚本'):
            editor.apply_changes(native, [{'op':'update_node','node_id':2,'parameters':{'beforeJS':'return 1'}}],
                                 ai=True, allow_native=True)


if __name__=='__main__':
    unittest.main()
