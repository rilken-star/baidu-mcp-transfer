import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import core
import download

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import parallel_upload

class PortableTests(unittest.TestCase):
    def test_in_memory_credential_not_written_and_removed_from_child_env(self):
        with patch.dict(os.environ, {'BAIDU_NETDISK_ACCESS_TOKEN':'test-only-token'}, clear=True):
            self.assertEqual(download.token(), 'test-only-token')
            self.assertNotIn('BAIDU_NETDISK_ACCESS_TOKEN', os.environ)

    def test_upload_config_is_derived_from_install_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'input';src.write_bytes(b'test');pkg=root/'custom package';pkg.mkdir()
            with patch.object(core,'PACK',pkg), patch.object(core,'JOBS',root/'jobs'), \
                 patch.object(core,'CODE_ROOT',root/'none'), patch.object(core,'STATE_ROOT',root/'none2'), \
                 patch.object(core,'PROC_ROOT',root/'none3'), patch.object(core,'SYNC_ROOT',root/'none4'), patch.object(core,'_spawn'):
                task=core.start_upload(str(src),'/backup')
                cfg=core.read(task['config'])
                self.assertEqual(cfg['remote_code'],str(pkg))
                self.assertFalse(cfg['overwrite'])
                self.assertNotIn('access_token',cfg)
                self.assertTrue(Path(cfg['source']).is_dir())

    def test_multipart_resume_uses_checkpoint_without_reuploading_confirmed_parts(self):
        class Config:
            def __init__(self): self.connection_pool_maxsize=None;self.retries=None
        class Context:
            def __init__(self,*a):pass
            def __enter__(self):return self
            def __exit__(self,*a):return False
        state={'fail':True,'sent':[],'precreate_calls':0}
        class API:
            def __init__(self,*a):pass
            def xpanfileprecreate(self,**kw):
                state['precreate_calls']+=1;return {'errno':0,'uploadid':'synthetic-id'}
            def pcssuperfile2(self,**kw):
                part=int(kw['partseq']);state['sent'].append(part)
                if state['fail'] and part==2:raise ConnectionError('synthetic')
                return {'md5':hashlib.md5(kw['file'].read()).hexdigest()}
            def xpanfilecreate(self,**kw):return {'errno':0}
        official=types.SimpleNamespace(CHUNK_SIZE=4,
            openapi_client=types.SimpleNamespace(Configuration=Config,ApiClient=Context),
            fileupload_api=types.SimpleNamespace(FileuploadApi=API))
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'input';src.write_bytes(b'abcdefghijklmnop')
            checkpoint=Path(tmp)/'checkpoints'
            fn=parallel_upload.build(official,lambda *a,**kw:None,workers=1,checkpoint_dir=str(checkpoint))
            with patch.object(parallel_upload.time,'sleep'):
                first=fn(str(src),'/backup/test',16,'synthetic-token')
            self.assertEqual(first['status'],'error')
            record=json.loads(next(checkpoint.iterdir()).read_text())
            self.assertEqual(record['completed'],[0,1])
            self.assertNotIn('synthetic-token',json.dumps(record))
            state.update(fail=False,sent=[])
            result=fn(str(src),'/backup/test',16,'synthetic-token')
            self.assertEqual(result['status'],'success')
            self.assertEqual(state['sent'],[2,3])
            self.assertEqual(state['precreate_calls'],1)
            self.assertFalse(list(checkpoint.iterdir()))


class ConnectionReuseTests(unittest.TestCase):
    def check_upload(self,fail=False):
        import hashlib
        counts={'opened':0,'closed':0,'parts':0,'published':0}
        class Config:pass
        class Client:
            def __init__(self,*args):pass
            def __enter__(self):counts['opened']+=1;return self
            def __exit__(self,*args):counts['closed']+=1
        class API:
            def __init__(self,*args):pass
            def xpanfileprecreate(self,**kw):return {'errno':0,'uploadid':'fixture-session'}
            def pcssuperfile2(self,**kw):
                counts['parts']+=1
                if fail:raise ConnectionError('fixture-network-failure')
                return {'md5':hashlib.md5(kw['file'].read()).hexdigest()}
            def xpanfilecreate(self,**kw):counts['published']+=1;return {'errno':0}
        official=types.SimpleNamespace(CHUNK_SIZE=4,openapi_client=types.SimpleNamespace(Configuration=Config,ApiClient=Client),fileupload_api=types.SimpleNamespace(FileuploadApi=API))
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'fixture.bin';p.write_bytes(b'abcdefghijklmnop')
            upload=parallel_upload.build(official,lambda *a,**kw:None,workers=1,checkpoint_dir=str(Path(tmp)/'state'))
            with patch.object(parallel_upload.time,'sleep'):result=upload(str(p),'/target/file',16,'fixture-token')
        return counts,result
    def test_part_connection_reused_across_file(self):
        counts,result=self.check_upload()
        self.assertEqual(result['status'],'success');self.assertEqual(counts['parts'],4)
        self.assertEqual(counts['opened'],2);self.assertEqual(counts['closed'],2)
    def test_part_connection_closed_on_error_without_publishing(self):
        counts,result=self.check_upload(True)
        self.assertEqual(result['status'],'error');self.assertEqual(counts['published'],0)
        self.assertEqual(counts['opened'],2);self.assertEqual(counts['closed'],2)

if __name__=='__main__':unittest.main()
