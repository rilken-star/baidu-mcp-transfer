import io,json,os,ssl,types,unittest,urllib.error
from unittest.mock import patch
from urllib3.exceptions import NewConnectionError,MaxRetryError,ReadTimeoutError
import safe_retry as retry

class SafeRetryTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{'BAIDU_TUI_SECRETS_FILE':'/tmp/baidu-test-credentials-do-not-exist'});self.env.start()
  self.sleep=patch.object(retry.time,'sleep');self.sleep_mock=self.sleep.start()
 def tearDown(self):self.sleep.stop();self.env.stop()
 def sdk(self,pre=None,part=None,create=None,probe=None):
  class SDK:
   def xpanfileprecreate(self,**kw):return pre(**kw) if pre else {'errno':0,'uploadid':'fixture-id'}
   def pcssuperfile2(self,**kw):return part(**kw) if part else {'md5':'fixture-md5'}
   def xpanfilecreate(self,**kw):return create(**kw) if create else {'errno':0}
  official=types.SimpleNamespace(access_token='fixture-token',fileupload_api=types.SimpleNamespace(FileuploadApi=SDK))
  retry.install_sdk_retry(official,probe=probe);return SDK()
 def connect_error(self):
  return MaxRetryError(None,'https://fixture.invalid/?access_token=fixture-secret',NewConnectionError(None,'fixture-private-detail'))
 def test_read_timeout_then_success_backoff(self):
  with patch('builtins.print'):
   fn=unittest.mock.Mock(side_effect=[TimeoutError('fixture-secret'),TimeoutError('fixture-secret'),{'ok':True}])
   self.assertEqual(retry.retry_call(fn),{'ok':True})
  self.assertEqual(fn.call_count,3);self.assertEqual([x.args[0] for x in self.sleep_mock.call_args_list],[1,2])
 def test_bounded_exhaustion_and_no_sensitive_diagnostic(self):
  fn=unittest.mock.Mock(side_effect=self.connect_error())
  with self.assertRaises(retry.RequestFailed) as e:retry.retry_call(fn)
  self.assertEqual(fn.call_count,5);self.assertEqual(e.exception.diagnostic['attempt'],5)
  self.assertNotIn('fixture-secret',str(e.exception)+json.dumps(e.exception.diagnostic))
  self.assertNotIn('fixture.invalid',json.dumps(e.exception.diagnostic))
 def test_http_unauthorized_is_not_retried(self):
  fn=unittest.mock.Mock(side_effect=urllib.error.HTTPError('https://fixture.invalid/secret',401,'fixture-secret',{},None))
  with self.assertRaises(retry.RequestFailed):retry.retry_call(fn)
  self.assertEqual(fn.call_count,1)
 def test_certificate_failure_is_not_retried(self):
  fn=unittest.mock.Mock(side_effect=ssl.SSLCertVerificationError('fixture-secret'))
  with self.assertRaises(retry.RequestFailed):retry.retry_call(fn)
  self.assertEqual(fn.call_count,1)
 def test_auth_refresh_failure_never_retries(self):
  from baidu_auth import AuthError
  fn=unittest.mock.Mock(side_effect=AuthError('fixture-private'))
  with self.assertRaises(retry.RequestFailed):retry.retry_call(fn)
  self.assertEqual(fn.call_count,1)
 def test_precreate_retries_only_before_send_failures(self):
  pre=unittest.mock.Mock(side_effect=[self.connect_error(),{'errno':0,'uploadid':'fixture-id'}]);sdk=self.sdk(pre=pre)
  self.assertEqual(sdk.xpanfileprecreate(path='/target/file',size=3)['uploadid'],'fixture-id');self.assertEqual(pre.call_count,2)
 def test_ambiguous_precreate_is_not_resubmitted(self):
  pre=unittest.mock.Mock(side_effect=ReadTimeoutError(None,'/secret','fixture-secret'));sdk=self.sdk(pre=pre)
  with self.assertRaises(retry.RequestFailed):sdk.xpanfileprecreate(path='/target/file',size=3)
  self.assertEqual(pre.call_count,1)
 def test_part_retries_identical_body_at_same_sequence(self):
  bodies=[];seq=[]
  def part(**kw):
   bodies.append(kw['file'].read());seq.append(kw['partseq'])
   if len(bodies)==1:raise TimeoutError('fixture-secret')
   return {'md5':'fixture-md5'}
  sdk=self.sdk(part=part);sdk.pcssuperfile2(path='/target/file',uploadid='fixture-id',partseq='0',file=io.BytesIO(b'fixture-body'))
  self.assertEqual(bodies,[b'fixture-body']*2);self.assertEqual(seq,['0','0'])
 def test_finalization_lost_response_is_verified_without_second_write(self):
  create=unittest.mock.Mock(side_effect=TimeoutError('fixture-secret'));probe=unittest.mock.Mock(return_value={'errno':0,'fs_id':1});sdk=self.sdk(create=create,probe=probe)
  self.assertEqual(sdk.xpanfilecreate(path='/target/file',size=3)['errno'],0);self.assertEqual(create.call_count,1);probe.assert_called_once_with('/target/file',3)
 def test_finalization_unknown_result_stops_without_resubmission(self):
  create=unittest.mock.Mock(side_effect=self.connect_error());sdk=self.sdk(create=create,probe=lambda *a:None)
  with self.assertRaises(retry.UnknownOutcome) as e:sdk.xpanfilecreate(path='/target/file',size=3)
  self.assertEqual(create.call_count,1);self.assertEqual(e.exception.diagnostic['outcome'],'unknown_not_resubmitted')
 def test_cancellation_is_not_retried_or_wrapped(self):
  fn=unittest.mock.Mock(side_effect=InterruptedError())
  with self.assertRaises(InterruptedError):retry.retry_call(fn)
  self.assertEqual(fn.call_count,1)

 def test_metadata_get_integration_retries(self):
  import worker
  response=io.BytesIO(b'{"errno":0,"list":[]}')
  with patch.object(worker.urllib.request,'urlopen',side_effect=[TimeoutError('fixture-secret'),response]) as call:
   self.assertEqual(worker.Baidu('fixture-token').request('list',{'dir':'/authorized'})['errno'],0)
   self.assertEqual(call.call_count,2)
 def test_metadata_mutation_integration_is_never_blindly_retried(self):
  import worker
  with patch.object(worker.urllib.request,'urlopen',side_effect=TimeoutError('fixture-secret')) as call:
   with self.assertRaises(worker.Attention):worker.Baidu('fixture-token').request('create',{'path':'/authorized/new'},post=True)
   self.assertEqual(call.call_count,1)
 def test_metadata_auth_rejection_is_not_retried(self):
  import worker
  with patch.object(worker.urllib.request,'urlopen',return_value=io.BytesIO(b'{"errno":-6}')) as call:
   with self.assertRaises(worker.Attention):worker.Baidu('fixture-token').request('list',{'dir':'/authorized'})
   self.assertEqual(call.call_count,1)

if __name__=='__main__':unittest.main()
