"""Exercise UI actions with fake power/UAC; never transition the actual PC."""
import tempfile
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sleep_manager.storage import Store
from sleep_manager.controller import Controller
from test_app import Power, Reader


@unittest.skipUnless(__import__('os').name == 'nt', 'Windows Tk UI')
class ClearUIActionsTests(unittest.TestCase):
    def make(self, folder):
        from sleep_manager.ui import Window
        store = Store(Path(folder))
        store.save_settings(dict(enabled=False, action='shutdown', delay=300))
        c = Controller(store, Power(), Reader())
        w = Window(c, SimpleNamespace(smoke_ui=False, data_dir=Path(folder)), [])
        w.root.withdraw()
        w.admin = False
        return c, w

    def test_cancelled_elevation_keeps_original_mode_pause_and_window(self):
        with tempfile.TemporaryDirectory() as folder:
            c, w = self.make(folder)
            try:
                with patch('sleep_manager.ui.request_admin_relaunch', return_value=False) as launch:
                    w.execution_mode.set('실제 전원 제어')
                    w.apply_execution_mode()
                launch.assert_called_once()
                self.assertTrue(c.power.dry_run)
                self.assertTrue(c.store.execution_dry_run())
                self.assertFalse(c.engine.enabled)
                self.assertFalse(w.closed)
                self.assertIn('취소', w.mode_feedback.cget('text'))
            finally:
                w.close()

    def test_accepted_elevation_does_not_apply_live_mode_or_resume_in_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            c, w = self.make(folder)
            try:
                with patch('sleep_manager.ui.request_admin_relaunch', return_value=True) as launch:
                    w.execution_mode.set('실제 전원 제어')
                    w.apply_execution_mode()
                launch.assert_called_once_with(Path(folder), activate=True)
                self.assertTrue(w.closed)
                self.assertTrue(c.store.execution_dry_run())
                self.assertFalse(c.store.settings()['enabled'])
                self.assertEqual(c.power.requests, 0)
            finally:
                w.close()

    def test_admin_mode_selection_applies_without_an_extra_apply_click_or_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            c, w = self.make(folder)
            c.power.process_names = lambda: set()
            c.power.set_dry_run = lambda value: setattr(c.power, 'dry_run', value)
            try:
                w.admin = True
                w.execution_mode.set('실제 전원 제어')
                w.mode_select.event_generate('<<ComboboxSelected>>')
                self.assertFalse(c.power.dry_run)
                self.assertFalse(c.store.execution_dry_run())
                self.assertFalse(c.engine.enabled)
                self.assertEqual(c.power.requests, 0)
            finally:
                w.close()

    def test_refresh_reads_records_without_completing_or_resuming_unknown_work(self):
        with tempfile.TemporaryDirectory() as folder:
            c, w = self.make(folder)
            try:
                c._accept(dict(provider='codex', session_id='s', kind='unknown'))
                w.recheck_connections()
                self.assertEqual(c.engine.jobs['codex:s'].state, 'unknown')
                self.assertFalse(c.engine.enabled)
                self.assertIn('작업', w.notice.cget('text'))
                self.assertEqual(c.power.requests, 0)
            finally:
                w.close()

    def test_diagnostics_opens_read_only_summary_and_preserves_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            c, w = self.make(folder)
            before = c.store.settings()
            c.diagnostics = lambda: dict(version='test', pid=42, run_id='isolated',
                database_path=str(c.store.path), settings=before,
                shutdown_permission=dict(available=True), last_error='', power_action={})
            try:
                with patch('sleep_manager.ui.messagebox.showinfo'), patch('sleep_manager.ui.messagebox.showerror'):
                    w.diagnose()
                self.assertTrue(w.diagnostics_window.winfo_exists())
                self.assertEqual(str(c.store.path),json.loads(w.diagnostic_details.get('1.0', 'end'))['database_path'])
                self.assertEqual(c.store.settings(), before)
                self.assertEqual(c.power.requests, 0)
            finally:
                w.close()


class ContrastTests(unittest.TestCase):
    def test_primary_body_and_secondary_text_are_readable_on_their_surfaces(self):
        from sleep_manager import ui
        def luminance(color):
            values = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)]
            linear = [x/12.92 if x <= .04045 else ((x+.055)/1.055)**2.4 for x in values]
            return sum(x*y for x, y in zip(linear, (.2126, .7152, .0722)))
        for foreground, background in ((ui.TEXT, ui.CARD), (ui.MUTED, ui.CARD),
                                      (ui.TEXT, ui.BG), (ui.MUTED, ui.BG), ('#ffffff', ui.TEAL)):
            light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
            self.assertGreaterEqual((light+.05)/(dark+.05), 4.5)
