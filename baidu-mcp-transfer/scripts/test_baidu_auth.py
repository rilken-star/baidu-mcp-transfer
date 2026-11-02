import tempfile,unittest,time,os,concurrent.futures,types
from pathlib import Path
from unittest.mock import patch
import baidu_auth as auth

class RefreshTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'secrets.env'
  self.path.write_text("OTHER_KEY='fixture-keep'\nBAIDU_ACCESS_TOKEN='fixture-old'\nBAIDU_APP_KEY='fixture-app'\nBAIDU_SECRET_KEY='fixture-secret'\nBAIDU_REFRESH_TOKEN='fixture-refresh'\nBAIDU_TOKEN_EXPIRES_AT='1'\n");self.path.chmod(0o600)
  self.env=patch.dict(os.environ,{'BAIDU_TUI_SECRETS_FILE':str(self.path)});self.env.start()
 def tearDown(self):self.env.stop();self.tmp.cleanup()
 def result(self):return {'access_token':'fixture-new','refresh_token':'fixture-rotated','expires_in':7200}
 def test_refresh_rotates_and_preserves_other_credentials(self):
  original=self.path.read_bytes()
  with patch.object(auth,'_exchange',return_value=self.result()) as exchange:
   self.assertEqual(auth.Session('fixture-old').get(),'fixture-new');self.assertEqual(exchange.call_count,1)
  _,v=auth._read(self.path);self.assertEqual(v['BAIDU_REFRESH_TOKEN'],'fixture-rotated')
  self.assertIn("OTHER_KEY='fixture-keep'",self.path.read_text())
  self.assertEqual(self.path.stat().st_mode&0o777,0o600)
  self.assertEqual(next(self.root.glob('secrets.env.backup-refresh-*')).read_bytes(),original)
 def test_concurrent_readers_refresh_only_once(self):
  with patch.object(auth,'_exchange',return_value=self.result()) as exchange:
   session=auth.Session('fixture-old')
   with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:tokens=list(pool.map(lambda _:session.get(),range(8)))
   self.assertEqual(tokens,['fixture-new']*8);self.assertEqual(exchange.call_count,1)
 def test_failed_or_uncertain_refresh_is_not_retried(self):
  original=self.path.read_bytes()
  with patch.object(auth,'_exchange',side_effect=TimeoutError('fixture sensitive exception')) as exchange:
   for _ in range(2):
    with self.assertRaises(auth.AuthError) as error:auth.Session('fixture-old').get()
    self.assertNotIn('sensitive',str(error.exception))
   self.assertEqual(exchange.call_count,1)
  self.assertEqual(self.path.read_bytes(),original)
 def test_valid_token_does_not_refresh(self):
  self.path.write_text(self.path.read_text().replace("EXPIRES_AT='1'","EXPIRES_AT='"+str(int(time.time())+7200)+"'"))
  with patch.object(auth,'_exchange') as exchange:
   self.assertEqual(auth.Session('fixture-old').get(),'fixture-old');exchange.assert_not_called()
 def test_sdk_uses_new_token_for_each_part(self):
  calls=[]
  class SDK:
   def xpanfileprecreate(self,**kw):calls.append(kw['access_token'])
   pcssuperfile2=xpanfileprecreate;xpanfilecreate=xpanfileprecreate
  official=types.SimpleNamespace(access_token='fixture-old',fileupload_api=types.SimpleNamespace(FileuploadApi=SDK))
  with patch.object(auth,'_exchange',return_value=self.result()):
   auth.install_sdk_refresh(official);api=SDK()
   api.pcssuperfile2(access_token='fixture-old');api.xpanfilecreate(access_token='fixture-old')
  self.assertEqual(calls,['fixture-new','fixture-new'])

if __name__=='__main__':unittest.main()
