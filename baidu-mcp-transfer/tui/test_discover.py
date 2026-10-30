import unittest,tempfile,json,os
from pathlib import Path
from unittest.mock import patch
import core
class Discover(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.r=Path(self.tmp.name)
  self.code=self.r/'code';self.state=self.r/'states';self.jobs=self.r/'jobs';self.proc=self.r/'proc'
  for p in (self.code,self.state,self.jobs,self.proc):p.mkdir()
  self.patches=[patch.object(core,'CODE_ROOT',self.code),patch.object(core,'STATE_ROOT',self.state),patch.object(core,'JOBS',self.jobs),patch.object(core,'PROC_ROOT',self.proc)]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.tmp.cleanup()
 def cfg(self,name,kind):
  folder=self.code/('baidu-'+name);folder.mkdir();state=self.state/('baidu-'+name);state.mkdir()
  data={'remote_state':str(state),'source':'/source','target':'/target-'+name}
  if kind=='upload':data['remote_code']=str(folder)
  else:data['kind']='download'
  core.atomic(folder/'config.json',data);core.atomic(state/'progress.json',{'phase':'complete'})
  return folder/'config.json',state
 def test_finds_upload_and_download(self):
  self.cfg('one','upload');self.cfg('two','download')
  rows=core.jobs();self.assertEqual(sorted(r['kind'] for r in rows),['download','upload'])
 def test_registered_task_deduplicates_auto(self):
  cfg,state=self.cfg('one','upload')
  core.atomic(self.jobs/'registered'/'task.json',{'id':'registered','kind':'upload','config':str(cfg)})
  rows=core.jobs();self.assertEqual([r['id'] for r in rows],['registered'])
 def test_uninstrumented_mcp_is_visible_but_no_rate(self):
  folder=self.proc/'42';folder.mkdir();(folder/'cmdline').write_bytes(b'/usr/bin/python3\0/root/project/code/baidu-mcp/mcp_server.py\0')
  with patch.object(core,'identity',return_value='123'):
   rows=core.jobs();self.assertEqual(len(rows),1);v=core.snapshot(rows[0])
   self.assertEqual(v['kind'],'mcp');self.assertEqual(v['stage'],'unknown_telemetry');self.assertEqual(v['accounted'],0)
   with self.assertRaises(core.SafeError):core.stop_task(rows[0])
 def test_registered_child_not_listed_twice(self):
  cfg,state=self.cfg('one','upload');core.atomic(state/'child.json',{'pid':42,'start_ticks':'123'})
  folder=self.proc/'42';folder.mkdir();(folder/'cmdline').write_bytes(b'/usr/bin/python3\0/root/project/code/baidu-mcp/mcp_server.py\0')
  with patch.object(core,'identity',return_value='123'):
   rows=core.jobs();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['kind'],'upload')
 def test_rate_reset(self):
  rate=core.Rates();rate.update('a',100,1);self.assertEqual(rate.update('a',200,3),50)
  rate.reset('a');self.assertEqual(rate.update('a',400,4),0)
if __name__=='__main__':unittest.main()
