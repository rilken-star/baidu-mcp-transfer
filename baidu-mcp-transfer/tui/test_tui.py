import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import core
import download
import tui

class Response(io.BytesIO):
    def __init__(self,data,status=200,**headers):
        super().__init__(data);self.status=status;self.headers=headers

class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.final=self.root/'final';self.partial=self.root/'part'
        self.row={'size':10,'fs_id':123}
        self.api=type('Api',(),{'dlink':lambda s,f:'https://d.pcs.baidu.com/file/test'})()
        download.STOP=False
    def tearDown(self):self.tmp.cleanup()
    def call(self,response):
        fake=type('Opener',(),{'open':lambda s,req,timeout:response})()
        with patch('urllib.request.build_opener',return_value=fake):
            download.transfer(self.api,self.row,self.final,self.partial,{},lambda:None)
    def test_resume_publishes_complete_file(self):
        self.partial.write_bytes(b'0123')
        self.call(Response(b'456789',206,**{'Content-Range':'bytes 4-9/10','Content-Length':'6'}))
        self.assertEqual(self.final.read_bytes(),b'0123456789');self.assertFalse(self.partial.exists())
    def test_ignored_range_does_not_corrupt_partial(self):
        self.partial.write_bytes(b'0123')
        with self.assertRaises(core.SafeError):self.call(Response(b'0123456789',200,**{'Content-Length':'10'}))
        self.assertEqual(self.partial.read_bytes(),b'0123');self.assertFalse(self.final.exists())
    def test_short_error_response_never_published(self):
        with self.assertRaises(core.SafeError):self.call(Response(b'error',200,**{'Content-Length':'5'}))
        self.assertFalse(self.final.exists());self.assertFalse(self.partial.exists())
    def test_existing_destination_never_overwritten(self):
        self.final.write_bytes(b'keep')
        with self.assertRaises(core.SafeError):self.call(Response(b'0123456789',200,**{'Content-Length':'10'}))
        self.assertEqual(self.final.read_bytes(),b'keep')
    def test_outside_symlink_rejected(self):
        (self.root/'escape').symlink_to('/tmp')
        with self.assertRaises(core.SafeError):download.safe_child(self.root,'escape/outside')
    def test_non_baidu_redirect_rejected(self):
        with self.assertRaises(core.SafeError):download.trusted_url('https://example.org/file?access_token=hidden')
    def test_remote_listing_cannot_escape_selected_scope(self):
        class FakeApi:
            def listing(s,path):
                if path=='/':return [{'path':'/chosen','isdir':1}]
                return [{'path':'/outside/file','isdir':0,'fs_id':1,'size':3}]
        with self.assertRaises(core.SafeError):download.enumerate_files(FakeApi(),'/chosen')

class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.state=self.root/'state';self.state.mkdir()
        self.config=self.root/'config.json'
        core.atomic(self.config,{'remote_state':str(self.state),'source':'/source','target':'/target'})
        core.atomic(self.state/'manifest.json',{'effective_bytes':1000,'files':[{'rel':'new','size':900}]})
        core.atomic(self.state/'progress.json',{'current':'new','checked_at':100,'verified_bytes':100,'effective_bytes':1000,'phase':'processing'})
        core.atomic(self.state/'child.json',{'pid':123})
        self.task={'id':'test','kind':'upload','config':str(self.config)}
    def tearDown(self):self.tmp.cleanup()
    def test_previous_file_part_is_not_counted(self):
        core.atomic(self.state/'parts.json',{'owner_pid':123,'checked_at':99,'stage':'accepted_part','part':7})
        self.assertEqual(core.snapshot(self.task)['accounted'],100)
    def test_unacknowledged_sending_part_is_not_counted(self):
        core.atomic(self.state/'parts.json',{'owner_pid':123,'checked_at':101,'stage':'sending_part','part':0})
        self.assertEqual(core.snapshot(self.task)['accounted'],100)
    def test_recycled_child_part_is_not_counted(self):
        core.atomic(self.state/'parts.json',{'owner_pid':124,'checked_at':101,'stage':'accepted_part','part':7})
        self.assertEqual(core.snapshot(self.task)['accounted'],100)
    def test_verified_current_not_double_counted(self):
        core.atomic(self.state/'progress.json',{'current':'new','checked_at':100,'verified_bytes':1000})
        core.atomic(self.state/'ledger.json',{'new':{'stage':'verified'}})
        core.atomic(self.state/'parts.json',{'owner_pid':123,'checked_at':101,'stage':'finished','sdk_success':True})
        self.assertEqual(core.snapshot(self.task)['accounted'],1000)
    def test_sending_later_part_counts_only_preceding_acknowledged_bytes(self):
        core.atomic(self.state/'parts.json',{'owner_pid':123,'checked_at':101,'stage':'sending_part','part':1})
        with patch.object(core,'CHUNK',100):self.assertEqual(core.snapshot(self.task)['accounted'],200)
    def test_completed_upload_has_completed_stage(self):
        core.atomic(self.state/'progress.json',{'phase':'complete'})
        self.assertEqual(core.snapshot(self.task)['stage'],'complete')
    def test_manifest_supplies_count_before_first_file_completes(self):
        core.atomic(self.state/'manifest.json',{'effective_files':2,'files':[]})
        self.assertEqual(core.snapshot(self.task)['total_files'],2)
    def test_failed_launch_is_visible_instead_of_queued_forever(self):
        (self.state/'progress.json').unlink()
        core.atomic(self.root/'launcher.json',{'pid':-1,'start_ticks':'0'})
        self.assertEqual(core.snapshot(self.task)['phase'],'needs_attention')
        self.assertTrue(core.snapshot(self.task)['message'])
    def test_dead_processing_task_displayed_as_interrupted(self):
        self.assertEqual(core.snapshot(self.task)['phase'],'interrupted')
    def test_counter_reset_does_not_generate_negative_rate(self):
        r=core.Rates();r.update('a',100,0);self.assertEqual(r.update('a',200,2),50)
        self.assertEqual(r.update('a',0,3),0)
    def test_unicode_clip_respects_terminal_columns(self):
        self.assertLessEqual(tui.width(tui.clip('中文abcdef',5)),5)
    def test_control_codes_not_rendered(self):
        self.assertNotIn('\x1b',core.clean('hello\x1b[31m'))

if __name__=='__main__':unittest.main()
