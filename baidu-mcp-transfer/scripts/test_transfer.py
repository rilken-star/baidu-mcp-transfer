#!/usr/bin/env python3
"""Offline behavioral tests. Never uses a real token, server or Baidu account."""
import argparse
import json
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from common import atomic_json, validate
import worker


def configuration(root):
    source = root / 'source'
    source.mkdir()
    return {'version': 1, 'source': str(source), 'target': '/backup/tree',
            'remote_code': str(root / 'code'), 'remote_state': str(root / 'state'),
            'exclude_roots': ['input', 'output', 'user'], 'exclude_globs': ['models/*wan2.2*'],
            'min_bytes': 0, 'overwrite': False, 'follow_file_links': False,
            'host': 'server.example', 'user': 'user', 'port': 22, 'control_path': None}


class FakeApi:
    def __init__(self):
        self.rows = []
        self.created = 0

    def ensure_parent(self, *args):
        self.created += 1

    def proof(self, path, size):
        matches = [r for r in self.rows if r.get('path') == path]
        return {'verified': len(matches) == 1 and worker.strict_match(matches[0], path, size),
                'matches': matches, 'path': path, 'expected_bytes': size}


class FakeMcp:
    def __init__(self, api, fail=False):
        self.api, self.fail, self.calls = api, fail, 0

    def upload(self, path, parent):
        self.calls += 1
        if self.fail:
            raise worker.Attention('Simulated disconnect')
        self.api.rows = [{'path': parent + '/' + Path(path).name,
                          'server_filename': Path(path).name, 'isdir': 0, 'size': Path(path).stat().st_size}]


class TransferTests(unittest.TestCase):
    def setUp(self):
        worker.STOP = False
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = configuration(self.root)
        self.path = Path(self.config['source']) / 'a.bin'
        self.path.write_bytes(b'offline fixture')
        self.item = {'rel': 'a.bin', 'size': self.path.stat().st_size,
                     'mtime_ns': self.path.stat().st_mtime_ns}

    def tearDown(self):
        worker.STOP = False
        self.temporary.cleanup()

    def test_scope_roots_hidden_files_size_and_symlinks(self):
        root = self.path.parent
        for name in ['input/.hidden', 'output/deep/f.bin', 'user/settings.json', 'users/keep',
                     'custom_nodes/a/user/keep', '.keep', 'models/WAN2.2-model.bin']:
            f = root / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b'fixture')
        (root / 'broken').symlink_to(root / 'absent')
        (root / 'dir_link').symlink_to(root / 'users', target_is_directory=True)
        result = worker.scan(self.config)
        rels = {r['rel'] for r in result['files']}
        self.assertEqual(rels, {'a.bin', 'users/keep', 'custom_nodes/a/user/keep', '.keep'})
        self.assertEqual(len(result['issues']), 2)
        self.config['min_bytes'] = 10
        self.assertEqual([r['rel'] for r in worker.scan(self.config)['files']], ['a.bin'])

    def test_exact_metadata_types_paths_and_duplicates(self):
        path = '/backup/tree/a.bin'
        row = {'path': path, 'server_filename': 'a.bin', 'isdir': 0, 'size': 15}
        self.assertTrue(worker.strict_match(row, path, 15))
        for key, value in [('path', '/other/a.bin'), ('server_filename', 'b.bin'),
                           ('isdir', False), ('size', 15.0), ('size', '15'), ('isdir', 1)]:
            self.assertFalse(worker.strict_match(dict(row, **{key: value}), path, 15))
        api = FakeApi()
        api.rows = [row, row]
        self.assertFalse(api.proof(path, 15)['verified'])

    def test_target_size_verified_then_resume_without_upload(self):
        api = FakeApi()
        mcp = FakeMcp(api)
        ledger = {}
        self.assertEqual(worker.transfer_one(self.config, self.item, ledger, api, mcp, {}), 'verified')
        self.assertEqual(mcp.calls, 1)
        self.assertEqual(worker.transfer_one(self.config, self.item, ledger, api, mcp, {}), 'verified')
        self.assertEqual(mcp.calls, 1)
        self.assertEqual(json.loads((self.root / 'state/ledger.json').read_text())['a.bin']['stage'], 'verified')

    def test_unknown_is_durable_and_never_resubmitted(self):
        api = FakeApi()
        mcp = FakeMcp(api, fail=True)
        ledger = {}
        with self.assertRaises(worker.Attention):
            worker.transfer_one(self.config, self.item, ledger, api, mcp, {})
        self.assertEqual(ledger['a.bin']['stage'], 'unknown')
        self.assertEqual(worker.transfer_one(self.config, self.item, ledger, api, mcp, {}), 'unknown')
        self.assertEqual(mcp.calls, 1)

    def test_verify_has_no_mutations(self):
        api = FakeApi()
        mcp = FakeMcp(api)
        self.assertEqual(worker.transfer_one(self.config, self.item, {}, api, mcp, {}, True), 'not_verified')
        self.assertEqual((api.created, mcp.calls), (0, 0))

    def test_source_change_and_conflict_do_not_upload(self):
        api = FakeApi()
        mcp = FakeMcp(api)
        ledger = {'a.bin': {'stage': 'verified', 'source': {'size': 999, 'mtime_ns': 1}}}
        self.assertEqual(worker.transfer_one(self.config, self.item, ledger, api, mcp, {}), 'source_changed')
        api.rows = [{'path': '/backup/tree/a.bin', 'server_filename': 'a.bin', 'size': 999, 'isdir': 0}]
        self.assertEqual(worker.transfer_one(self.config, self.item, {}, api, mcp, {}), 'conflict')
        self.assertEqual(mcp.calls, 0)

    def test_pagination_does_not_verify_first_page_only(self):
        class Pages(worker.Baidu):
            def request(self, method, values, post=False):
                if values['start'] == 0:
                    return {'errno': 0, 'list': [{'path': '/x/%d' % i} for i in range(1000)]}
                return {'errno': 0, 'list': [{'path': '/x/last'}]}
        self.assertEqual(len(Pages('not-a-credential').listing('/x')), 1001)

    def test_config_rejects_traversal_runtime_in_source_and_secret_keys(self):
        for override in ({'source': '/a/../b'}, {'target': '/'}, {'exclude_roots': ['../user']},
                         {'remote_code': str(self.path.parent / 'runtime')}, {'access_token': 'fixture'}):
            with self.assertRaises(ValueError):
                validate(dict(self.config, **override))

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux source process identity test')
    def test_stop_only_owned_process_identity(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            atomic_json(self.root / 'state/owner.json', {'pid': process.pid, 'start_ticks': worker.process_identity(process.pid)})
            self.assertTrue(worker.stop(self.config)['stopped'])
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def real_mcp(python):
    # Real official stdio handshake and guarded tool rejection; no outbound request.
    with tempfile.TemporaryDirectory() as temporary:
        config = configuration(Path(temporary))
        path = Path(temporary) / 'config.json'
        atomic_json(path, config)
        program = '''
import json,sys,fcntl,importlib.util,hashlib
from pathlib import Path
import worker
c=json.loads(Path(sys.argv[1]).read_text()); c['_config_path']=sys.argv[1]
lockpath=Path(c['remote_state']).parent/'lock'
handle=lockpath.open('a'); fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
m=worker.Mcp(c,'offline-not-a-real-token',handle.fileno()); handle.close()
probe=lockpath.open('a')
try:
    fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
    raise AssertionError('SDK child lost exclusive transfer lock')
except BlockingIOError:
    pass
r=m.call('tools/list',{},30)
assert any(t['name']=='upload_file' for t in r['tools'])
r=m.call('tools/call',{'name':'upload_file','arguments':
 {'local_file_path':'/outside/not-authorized','remote_path':'/outside'}},30)
assert 'Outside configured' in json.dumps(r)
m.close(); assert m.process.poll() is not None
fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB); probe.close()
print('Real MCP handshake/guard/child lock/exit: PASS')
# Invoke the pristine official uploader using a fake SDK transport, no network.
vendor=Path(worker.__file__).resolve().parents[1]/'vendor'; sys.path.insert(0,str(vendor))
spec=importlib.util.spec_from_file_location('sdk_test',vendor/'fileupload_tool.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
parts=[]; final=[]
class Context:
    def __enter__(self): return self
    def __exit__(self,*a): pass
class FakeSDK:
    def xpanfileprecreate(self,**kw): return {'errno':0,'uploadid':'fixture-upload-id'}
    def pcssuperfile2(self,**kw):
        body=kw['file'].read(); parts.append((kw['partseq'],len(body)))
        return {'md5':hashlib.md5(body).hexdigest()}
    def xpanfilecreate(self,**kw): final.append(kw); return {'errno':0,'fs_id':42}
mod.openapi_client.ApiClient=lambda *a,**k:Context()
mod.fileupload_api.FileuploadApi=lambda *a,**k:FakeSDK()
fixture=Path(c['source'])/'multipart-fixture.bin'; fixture.write_bytes(b'x'*(4*1024*1024+7))
result=mod.upload_file(str(fixture),'/backup/tree')
assert result['status']=='success' and parts==[('0',4194304),('1',7)]
assert final[0]['path']=='/backup/tree/multipart-fixture.bin' and final[0]['rtype']==3
assert final[0]['size']==4194311 and len(json.loads(final[0]['block_list']))==2
print('Official multipart algorithm with fake SDK transport: PASS')
'''
        env = os.environ.copy()
        env['PYTHONPATH'] = str(Path(__file__).resolve().parent)
        subprocess.run([python, '-c', program, str(path)], env=env, check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mcp-python')
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TransferTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    if args.mcp_python:
        real_mcp(args.mcp_python)
