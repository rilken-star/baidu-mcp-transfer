import contextlib,io,json,os,sys,tempfile,types,unittest,importlib.util,importlib.abc
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import core,tui,download,worker,parallel_upload
import model_policy as policy
import model_sync

class ModelPolicyTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def test_only_weight_extensions_are_enumerated(self):
  for name in ('a.SAFETENSORS','b.gguf','note.txt','image.PNG','config.json','code.py'):(self.root/name).write_bytes(b'fixture')
  hidden=self.root/'.git';hidden.mkdir();(hidden/'irrelevant.pt').write_bytes(b'fixture')
  _,inv=policy.scan_models(self.root)
  self.assertEqual({r['rel'] for r in inv['files']},{'a.SAFETENSORS','b.gguf'})
  self.assertEqual(inv['excluded_auxiliary_files'],4)
 def test_file_links_require_explicit_permission_and_directory_links_are_never_followed(self):
  source=self.root/'source';source.mkdir();outside=self.root/'outside';outside.mkdir()
  model=outside/'weights.pt';model.write_bytes(b'fixture')
  (source/'link.pt').symlink_to(model);(source/'directory').symlink_to(outside,target_is_directory=True)
  _,before=policy.scan_models(source);self.assertEqual(before['effective_files'],0)
  _,after=policy.scan_models(source,True);self.assertEqual(after['effective_files'],1)
  self.assertEqual(after['files'][0]['realpath'],str(model.resolve()))
  self.assertIn('directory_symlink_not_followed',{x['reason'] for x in after['issues']})
 def test_single_auxiliary_file_is_rejected_without_scanning_siblings(self):
  p=self.root/'note.txt';p.write_text('fixture')
  with self.assertRaises(ValueError):policy.scan_models(p)
 def test_target_resolution_ignores_case_and_rejects_ambiguity(self):
  api=types.SimpleNamespace(listing=lambda p:[{'server_filename':'Models','path':'/Models','isdir':1}])
  self.assertEqual(policy.resolve_target(api,'/models'),'/Models')
  api.listing=lambda p:[{'server_filename':'Models','path':'/Models','isdir':1},{'server_filename':'models','path':'/models','isdir':1}]
  with self.assertRaises(ValueError):policy.resolve_target(api,'/models')
 def test_large_model_uses_eight_mib_only_with_known_membership(self):
  size=14565970976
  self.assertEqual(policy.chunk_mib(2,size),8)
  self.assertLessEqual((size+8*1024**2-1)//(8*1024**2),2048)
  self.assertEqual(policy.chunk_mib(None,size),4)
  self.assertEqual(policy.chunk_mib(True,size),4)
 def test_resume_requires_partial_same_session_and_source_signature(self):
  row={'size':5,'mtime_ns':10};cp={'path':'/source/a.pt','remote_path':'/target/a.pt','size':5,'mtime_ns':10,'chunk_size':4,'uploadid':'fixture-id','hashes':['a','b'],'completed':[0]}
  self.assertTrue(policy.resumable_checkpoint(cp,Path('/source/a.pt'),'/target/a.pt',row,4))
  self.assertFalse(policy.resumable_checkpoint({**cp,'completed':[0,1]},Path('/source/a.pt'),'/target/a.pt',row,4))
  self.assertFalse(policy.resumable_checkpoint({**cp,'mtime_ns':11},Path('/source/a.pt'),'/target/a.pt',row,4))
  self.assertFalse(policy.resumable_checkpoint({**cp,'completed':[True]},Path('/source/a.pt'),'/target/a.pt',row,4))
 def test_default_upload_and_explicit_all_files_dispatch(self):
  for options,expected in (([], 'models'),(['--all-files'],'all')):
   with patch.object(sys,'argv',['baidu-tui','--upload','/source','--to','/target']+options),patch.object(core,'start_model_sync',return_value={}) as model,patch.object(core,'start_upload',return_value={}) as all_files,contextlib.redirect_stdout(io.StringIO()):
    tui.main();self.assertEqual(model.call_count,expected=='models');self.assertEqual(all_files.call_count,expected=='all')
 def test_start_policy_creates_only_model_inventory(self):
  source=self.root/'source';source.mkdir();(source/'a.pt').write_bytes(b'fixture');(source/'note.txt').write_bytes(b'fixture')
  with patch.object(core,'SYNC_ROOT',self.root/'jobs'),patch.object(core,'_avoid_active_collision'),patch.object(core,'resume_model_sync',side_effect=lambda t:t):
   task=core.start_model_sync(source,'/target')
  plan=json.loads((Path(task['state'])/'plan.json').read_text())
  self.assertEqual(plan['policy']['mode'],'models_only');self.assertFalse(plan['policy']['overwrite'])
  self.assertEqual([r['rel'] for r in plan['inventory']['files']],['a.pt'])

class ControllerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.source=self.root/'source';self.source.mkdir();self.job=self.root/'job';self.job.mkdir()
  with (self.source/'weights.safetensors').open('wb') as f:f.truncate(5*1024**2)
  source,inv=policy.scan_models(self.source)
  core.atomic(self.job/'plan.json',{'source':source,'target':'/models','inventory':inv,'follow_file_links':False,'policy':dict(policy.POLICY)})
  self.files={};self.sent=[]
  class FakeApi:
   def __init__(inner,*a):pass
   def listing(inner,path):
    if path=='/':return [{'path':'/Models','server_filename':'Models','isdir':1}]
    return list(self.files.values())
  self.api=FakeApi
  class Loader(importlib.abc.Loader):
   def create_module(inner,spec):return None
   def exec_module(inner,module):
    class SDK:
     def xpanfileprecreate(inner,**kw):raise AssertionError('Unexpected SDK mutation')
     pcssuperfile2=xpanfileprecreate;xpanfilecreate=xpanfileprecreate
    module.access_token='fixture-token';module.CHUNK_SIZE=4*1024**2
    module.fileupload_api=types.SimpleNamespace(FileuploadApi=SDK)
    module.openapi_client=types.SimpleNamespace(Configuration=lambda:types.SimpleNamespace())
  original_spec=importlib.util.spec_from_file_location
  def fake_spec(name,path):
   return importlib.util.spec_from_loader(name,Loader()) if name=='sync_official' else original_spec(name,path)
  self.patches=[patch.object(importlib.util,'spec_from_file_location',side_effect=fake_spec),patch('pathlib.Path.home',return_value=self.root),patch.dict(os.environ,{'BAIDU_TUI_SECRETS_FILE':str(self.root/'no-real-credentials')}),patch.object(download,'token',return_value='fixture-token'),patch.object(worker,'Baidu',self.api),patch.object(download,'Api',return_value=types.SimpleNamespace(request=lambda *a,**kw:{'vip_type':2})),patch.object(parallel_upload,'build',return_value=self.upload)]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.tmp.cleanup()
 def upload(self,path,target,size,*args):
  self.sent.append(target);self.files[target]={'path':target,'server_filename':Path(target).name,'size':size,'isdir':0};return {'status':'success'}
 def test_existing_cloud_model_different_case_is_not_uploaded(self):
  self.files['existing']={'path':'/Models/WEIGHTS.SAFETENSORS','server_filename':'WEIGHTS.SAFETENSORS','isdir':0,'size':5*1024**2}
  self.assertEqual(model_sync.run(self.job),0);self.assertEqual(self.sent,[])
  s=json.loads((self.job/'status.json').read_text());self.assertEqual(s['counts']['skipped_same_name_size'],1)
 def test_new_model_is_uploaded_and_metadata_verified(self):
  self.assertEqual(model_sync.run(self.job),0);self.assertEqual(self.sent,['/Models/weights.safetensors'])
  s=json.loads((self.job/'status.json').read_text());self.assertEqual(s['phase'],'complete')
 def test_unknown_without_checkpoint_is_preserved_not_reuploaded(self):
  core.atomic(self.job/'results.json',{'weights.safetensors':{'state':'unknown','size':5*1024**2}})
  self.assertEqual(model_sync.run(self.job),0);self.assertEqual(self.sent,[])
  s=json.loads((self.job/'status.json').read_text());self.assertEqual(s['counts']['unknown'],1);self.assertEqual(s['phase'],'needs_attention')
 def test_duplicate_target_lock_prevents_second_uploader(self):
  import fcntl,hashlib
  folder=self.root/'.local/state/baidu-sync/.target-locks';folder.mkdir(parents=True)
  with (folder/(hashlib.sha256('/models'.encode()).hexdigest()+'.lock')).open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   self.assertEqual(model_sync.run(self.job),1);self.assertEqual(self.sent,[])

 def test_incomplete_checkpoint_resumes_same_session_without_file_retry_permission(self):
  import hashlib
  plan=json.loads((self.job/'plan.json').read_text());row=plan['inventory']['files'][0]
  target='/Models/weights.safetensors';cp=self.job/'checkpoints'/(hashlib.sha256(target.encode()).hexdigest()+'.json')
  saved={'path':str(self.source/'weights.safetensors'),'remote_path':target,'size':row['size'],'mtime_ns':row['mtime_ns'],'chunk_size':4*1024**2,'uploadid':'fixture-existing-session','hashes':['fixture-hash-one','fixture-hash-two'],'completed':[0]}
  core.atomic(cp,saved);core.atomic(self.job/'results.json',{'weights.safetensors':{'state':'unknown','size':row['size']}})
  def resume_upload(*args):
   current=json.loads(cp.read_text());self.assertEqual(current['uploadid'],'fixture-existing-session');self.assertEqual(current['completed'],[0])
   return self.upload(*args)
  with patch.object(parallel_upload,'build',return_value=resume_upload):self.assertEqual(model_sync.run(self.job),0)
  self.assertEqual(len(self.sent),1);self.assertEqual(len(list((self.job/'resume-audit').glob('*.json'))),1)
 def test_case_colliding_source_models_are_not_uploaded(self):
  (self.source/'weights.safetensors').unlink()
  (self.source/'A.pt').write_bytes(b'fixture-a');(self.source/'a.PT').write_bytes(b'fixture-b')
  if (self.source/'A.pt').samefile(self.source/'a.PT'):self.skipTest('Requires case-sensitive source filesystem; verified on Linux')
  source,inv=policy.scan_models(self.source)
  core.atomic(self.job/'plan.json',{'source':source,'target':'/models','inventory':inv,'follow_file_links':False,'policy':dict(policy.POLICY)})
  self.assertEqual(model_sync.run(self.job),0);self.assertEqual(self.sent,[])
  s=json.loads((self.job/'status.json').read_text());self.assertEqual(s['counts']['case_conflict'],2)

 def test_controller_rejects_auxiliary_entries_even_if_manually_injected(self):
  (self.source/'private.txt').write_text('fixture')
  plan=json.loads((self.job/'plan.json').read_text());row=plan['inventory']['files'][0]
  plan['inventory']['files']=[{**row,'rel':'private.txt'}];core.atomic(self.job/'plan.json',plan)
  self.assertEqual(model_sync.run(self.job),1);self.assertEqual(self.sent,[])

if __name__=='__main__':unittest.main()
