"""Portable task discovery and terminal fallback; no real data or network access."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import core
import tui

class GeneralTaskView(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [
            patch.object(core, 'SYNC_ROOT', self.root / 'channel'),
            patch.object(core, 'JOBS', self.root / 'jobs'),
            patch.object(core, 'discover_configs', return_value=()),
            patch.object(core, 'discover_mcp_processes', return_value=()),
            patch.object(core, 'identity', return_value='123'),
        ]
        for item in self.patches:
            item.start()
    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()
    def task(self, name='arbitrary-task-name'):
        state = core.SYNC_ROOT / name
        core.atomic(state / 'status.json', {
            'phase':'running', 'pid':42, 'started_at':1,
            'total_files':10, 'total_bytes':1000,
            'counts':{'uploaded':1, 'skipped_same_name_size':2},
            'uploaded_bytes':100, 'skipped_bytes':200,
            'current':{'size':100, 'completed_bytes':50, 'stage':'accepted_part'},
        })
        core.atomic(state / 'launch.json', {'pid':42, 'start_ticks':'123'})
        core.atomic(state / 'plan.json', {
            'source':'/source', 'target':'/Cloud/Models',
            'follow_file_links':name.endswith('links'),
        })
        return next(r for r in core.discover_sync_jobs() if r['state'] == str(state))
    def test_auto_discovery_without_registration(self):
        self.task()
        self.task('different-future-task-links')
        rows = core.jobs()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['kind'] == 'upload' for row in rows))
    def test_skipped_bytes_do_not_inflate_rate(self):
        row = core.snapshot(self.task())
        self.assertTrue(row['running'])
        self.assertEqual(row['accounted'], 350)
        self.assertEqual(row['rate_bytes'], 150)
        self.assertEqual(row['files'], 3)
    def test_reused_pid_is_not_running(self):
        task = self.task()
        with patch.object(core, 'identity', return_value='999'):
            row = core.snapshot(task)
            self.assertFalse(row['running'])
            self.assertEqual(row['phase'], 'interrupted')
    def test_case_insensitive_collision_with_general_task(self):
        self.task()
        with self.assertRaises(core.SafeError):
            core._avoid_active_collision('/cloud/models')
    def test_missing_telemetry_does_not_invent_speed(self):
        row = core.snapshot({'id':'mcp', 'kind':'mcp', 'pid':42, 'start_ticks':'123'})
        self.assertEqual(row['stage'], 'unknown_telemetry')
        self.assertEqual(row['accounted'], 0)
    def test_standard_upload_excludes_existing_files_from_rate(self):
        state = self.root / 'standard'
        cfg = self.root / 'config.json'
        core.atomic(cfg, {'source':'/source', 'target':'/target', 'remote_state':str(state)})
        files = [{'rel':'sent', 'size':100, 'mtime_ns':1}, {'rel':'existing', 'size':600, 'mtime_ns':2}]
        core.atomic(state / 'manifest.json', {'files':files, 'effective_bytes':700})
        core.atomic(state / 'progress.json', {'phase':'complete', 'verified_bytes':700})
        core.atomic(state / 'ledger.json', {
            'sent':{'stage':'verified', 'source':{'size':100, 'mtime_ns':1}, 'started_at':1},
            'existing':{'stage':'verified', 'source':{'size':600, 'mtime_ns':2}},
        })
        row = core.snapshot({'id':'standard', 'kind':'upload', 'config':str(cfg)})
        self.assertEqual(row['accounted'], 700)
        self.assertEqual(row['rate_bytes'], 100)

class TerminalFallback(unittest.TestCase):
    def test_dumb_uses_compatible_terminal(self):
        with patch.dict(os.environ, {'TERM':'dumb'}), patch.object(tui.subprocess, 'run') as run:
            run.return_value.returncode = 0
            tui.prepare_terminal()
            self.assertEqual(os.environ['TERM'], 'xterm-256color')
    def test_unknown_definition_uses_fallback(self):
        fail = type('Result', (), {'returncode':1})()
        ok = type('Result', (), {'returncode':0})()
        with patch.dict(os.environ, {'TERM':'xterm-ghostty'}), patch.object(tui.subprocess, 'run', side_effect=[fail, ok]):
            tui.prepare_terminal()
            self.assertEqual(os.environ['TERM'], 'xterm-256color')
    def test_no_available_definition_reports_actionable_error(self):
        with patch.dict(os.environ, {'TERM':'dumb'}), patch.object(tui.subprocess, 'run') as run:
            run.return_value.returncode = 1
            with self.assertRaises(core.SafeError):
                tui.prepare_terminal()

if __name__ == '__main__':
    unittest.main()
