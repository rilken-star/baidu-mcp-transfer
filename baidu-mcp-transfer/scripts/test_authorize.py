"""Mocked OAuth setup; no real credentials or outbound requests."""
import contextlib,io,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import authorize

class AuthorizeTests(unittest.TestCase):
    def run_flow(self,target,responses):
        output=io.StringIO()
        with patch.object(authorize.sys.stdin,'isatty',return_value=True), \
             patch.object(authorize.resource,'setrlimit'), \
             patch.object(authorize.getpass,'getpass',side_effect=['fixture-app','fixture-secret','fixture-code']), \
             patch.object(authorize,'fetch',side_effect=responses),contextlib.redirect_stdout(output):
            authorize.main(['--secrets-file',str(target),'--check-dir','/authorized'])
        self.assertNotIn('fixture-token',output.getvalue())
        self.assertNotIn('fixture-refresh',output.getvalue())
        self.assertNotIn('fixture-secret',output.getvalue())
    def response(self):
        return [{'access_token':'fixture-token','refresh_token':'fixture-refresh','expires_in':7200},{'errno':0,'list':[]}]
    def test_new_install_creates_protected_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'config/secrets.env'
            self.run_flow(target,self.response())
            self.assertEqual(target.parent.stat().st_mode&0o777,0o700)
            self.assertEqual(target.stat().st_mode&0o777,0o600)
            self.assertIn("BAIDU_REFRESH_TOKEN='fixture-refresh'",target.read_text())
    def test_preserves_other_credentials_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'secrets.env';original=b"OTHER_KEY='fixture-keep'\nBAIDU_ACCESS_TOKEN='fixture-old'\n"
            target.write_bytes(original);target.chmod(0o600)
            self.run_flow(target,self.response())
            self.assertIn("OTHER_KEY='fixture-keep'",target.read_text())
            self.assertEqual(next(Path(tmp).glob('secrets.env.backup-*')).read_bytes(),original)
    def test_failed_exchange_does_not_change_existing_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'secrets.env';original=b"OTHER_KEY='fixture-keep'\n"
            target.write_bytes(original);target.chmod(0o600)
            with self.assertRaises(SystemExit):self.run_flow(target,[{'error':'invalid_grant'}])
            self.assertEqual(target.read_bytes(),original)
            self.assertFalse(list(Path(tmp).glob('secrets.env.backup-*')))
    def test_cli_authorization_dispatch_does_not_open_dashboard(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tui'))
        import tui
        with patch.object(sys,'argv',['baidu-tui','--authorize','--check-dir','/authorized']), \
             patch.object(authorize,'main',return_value=0) as auth,patch.object(tui.curses,'wrapper') as ui:
            self.assertEqual(tui.main(),0)
            auth.assert_called_once_with(['--check-dir','/authorized']);ui.assert_not_called()

if __name__=='__main__':unittest.main()
