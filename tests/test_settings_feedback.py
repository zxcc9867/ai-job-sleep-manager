import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sleep_manager.controller import Controller
from sleep_manager.storage import Store
from test_app import Power,Reader

class CombinedSettingsTests(unittest.TestCase):
    def test_delay_and_action_commit_together(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); c=Controller(store,Power(),Reader())
            with patch.object(store,'save_settings',wraps=store.save_settings) as save:
                c.set_power_settings(300,'shutdown')
                self.assertEqual(save.call_count,1)
            self.assertEqual((c.engine.delay,c.engine.action),(300,'shutdown'))
            self.assertEqual((store.settings()['delay'],store.settings()['action']),(300,'shutdown'))

    def test_failed_write_preserves_applied_values_and_timer(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); c=Controller(store,Power(),Reader());c.save();c.engine.baseline=10
            before=store.settings()
            with patch.object(store,'save_settings',side_effect=sqlite3.OperationalError('disk full')):
                with self.assertRaises(sqlite3.OperationalError):c.set_power_settings(300,'shutdown')
            self.assertEqual((c.engine.delay,c.engine.action,c.engine.baseline),(600,'sleep',10))
            self.assertEqual(store.settings(),before)

    def test_invalid_action_does_not_partially_save_delay(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d));c=Controller(store,Power(),Reader());c.save()
            before=store.settings()
            with self.assertRaises(ValueError):c.set_power_settings(300,'invalid')
            self.assertEqual(store.settings(),before)
            self.assertEqual(c.engine.delay,600)

@unittest.skipUnless(__import__('os').name=='nt','Windows Tk UI')
class SettingsFeedbackUITests(unittest.TestCase):
    def make(self,d):
        from sleep_manager.ui import Window
        c=Controller(Store(Path(d)),Power(),Reader())
        w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=Path(d)),[]);w.root.update_idletasks();w.root.withdraw()
        return c,w

    def test_dirty_saved_and_execution_mode_are_distinct_and_visible_when_collapsed(self):
        with tempfile.TemporaryDirectory() as d:
            c,w=self.make(d)
            try:
                w.minutes.set('5');w.action.set('정상 종료')
                self.assertIn('미저장',w.save_feedback.cget('text'))
                self.assertIn('10분',w.applied_summary.cget('text'))
                w.apply_delay()
                self.assertIn('저장 완료',w.save_feedback.cget('text'))
                self.assertIn('5분',w.applied_summary.cget('text'))
                self.assertIn('정상 종료',w.applied_summary.cget('text'))
                self.assertIn('실제 종료 안 함',w.execution_status.cget('text'))
                w.toggle_settings();w.toggle_settings()
                self.assertIn('5분',w.applied_summary.cget('text'))
                self.assertTrue(c.power.dry_run)
                w.minutes.set('7')
                self.assertIn('미저장',w.save_feedback.cget('text'))
                self.assertIn('5분',w.applied_summary.cget('text'))
            finally:w.close()

    def test_storage_failure_is_visible_near_save_button_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            c,w=self.make(d)
            try:
                w.minutes.set('5')
                with patch.object(c.store,'save_settings',side_effect=sqlite3.OperationalError('disk full')),patch('sleep_manager.ui.messagebox.showerror'):
                    w.apply_delay()
                self.assertIn('저장 실패',w.save_feedback.cget('text'))
                self.assertNotIn('저장 완료',w.save_feedback.cget('text'))
                self.assertIn('10분',w.applied_summary.cget('text'))
            finally:w.close()

    def test_live_unknown_waiting_and_paused_do_not_claim_shutdown_is_armed(self):
        with tempfile.TemporaryDirectory() as d:
            c,w=self.make(d)
            try:
                c.power.dry_run=False;c.engine.set_action('shutdown',0)
                c._accept(dict(provider='claude',session_id='s',kind='unknown'))
                w.update_settings_feedback()
                self.assertIn('확인 필요',w.execution_status.cget('text'))
                c._accept(dict(provider='claude',session_id='s',kind='waiting'))
                w.update_settings_feedback()
                self.assertIn('답변',w.execution_status.cget('text'))
                c.engine.set_enabled(False,0);w.update_settings_feedback()
                self.assertIn('일시 중지',w.execution_status.cget('text'))
            finally:w.close()
