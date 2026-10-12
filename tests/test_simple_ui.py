"""Exercise the simplified dashboard using only temporary data and fake power."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from sleep_manager.controller import Controller
from sleep_manager.power_state import PowerActionState
from sleep_manager.storage import Store
from test_app import Power, Reader


@unittest.skipUnless(os.name == 'nt', 'Windows Tk UI')
class SimpleDashboardTests(unittest.TestCase):
    def make_window(self, folder):
        from sleep_manager.ui import Window
        store = Store(Path(folder))
        store.save_settings(dict(enabled=False, action='shutdown', delay=300))
        store.save_power_action(PowerActionState('failed', 'legacy_shutdown_failure',
            'Shutdown deferred: external_power_request:SYSTEM').payload())
        power = Power()
        power.dry_run = False
        power.process_names = lambda: set()
        controller = Controller(store, power, Reader())
        window = Window(controller, SimpleNamespace(smoke_ui=False, data_dir=Path(folder)), [])
        window.root.withdraw()
        window.catalog = SimpleNamespace(lookup=lambda jobs: {})
        window.claude_details = SimpleNamespace(lookup=lambda *args: {})
        window.claude_presence = SimpleNamespace(lookup=lambda **args: {}, last_error='')
        return controller, window

    def test_off_state_and_completion_controls_are_visible_without_extra_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            c, w = self.make_window(folder)
            try:
                w.refresh()
                self.assertFalse(w.settings_open)
                self.assertFalse(w.settings_panel.winfo_manager())
                self.assertEqual(w.spin.master, w.completion_controls)
                self.assertEqual(w.completion_controls.winfo_manager(), 'pack')
                self.assertEqual(w.pause.master, w.overview_actions)
                self.assertIn('꺼져', w.status_label.cget('text'))
                self.assertIn('켜기', w.description.cget('text'))
                self.assertIn('5분', w.applied_summary.cget('text'))
                self.assertIn('이전', w.description.cget('text'))
                self.assertFalse(c.engine.enabled)
                self.assertEqual(c.power.requests, 0)
            finally:
                w.close()

    def test_saving_and_opening_options_do_not_resume_management(self):
        with tempfile.TemporaryDirectory() as folder:
            c, w = self.make_window(folder)
            try:
                w.minutes.set('7')
                w.apply_delay()
                w.toggle_list_options()
                self.assertTrue(w.list_options_open)
                self.assertTrue(w.history_bar.winfo_manager())
                w.toggle_list_options()
                self.assertFalse(w.history_bar.winfo_manager())
                self.assertEqual(c.engine.delay, 420)
                self.assertFalse(c.store.settings()['enabled'])
                self.assertFalse(c.engine.enabled)
                self.assertIn('꺼져', w.description.cget('text'))
                self.assertEqual(c.power.requests, 0)
            finally:
                w.close()

    def test_compact_detail_keeps_instruction_activity_and_plan_then_expands_records(self):
        with tempfile.TemporaryDirectory() as folder:
            c, w = self.make_window(folder)
            c._accept(dict(provider='codex', session_id='sample-session', kind='start'))
            w.catalog = SimpleNamespace(lookup=lambda jobs: {'codex:sample-session': dict(
                title='로그인 오류 수정', latest_request='로그인 오류를 수정해줘',
                commentary='세션 만료 처리를 수정하고 테스트를 실행하고 있습니다.',
                commentary_at='2026-10-12 12:00', activity='터미널 명령 실행 중',
                activity_history=[dict(text='테스트 명령 실행', at='2026-10-12')],
                plan=[dict(step='회귀 테스트', status='in_progress')])})
            try:
                w.render_jobs()
                compact = w.details.get('1.0', 'end')
                self.assertIn('로그인 오류를 수정해줘', compact)
                self.assertIn('테스트를 실행', compact)
                self.assertIn('회귀 테스트', compact)
                self.assertNotIn('세션 ID:', compact)
                self.assertNotIn('마지막 상태 이벤트:', compact)
                selected = w.table.selection()
                w.show_records.set(True)
                w.show_job_details()
                full = w.details.get('1.0', 'end')
                self.assertIn('세션 ID: sample-session', full)
                self.assertIn('마지막 상태 이벤트:', full)
                self.assertEqual(w.table.selection(), selected)
                self.assertFalse(c.engine.enabled)
                self.assertEqual(c.power.requests, 0)
            finally:
                w.close()

    def test_main_button_explicitly_starts_and_stops_management_without_a_power_call(self):
        with tempfile.TemporaryDirectory() as folder:
            c, w = self.make_window(folder)
            try:
                w.pause.invoke()
                self.assertTrue(c.engine.enabled)
                self.assertTrue(c.store.settings()['enabled'])
                self.assertEqual(c.power_action.phase, 'idle')
                self.assertEqual(c.error, '')
                self.assertIn('켜져', w.status_label.cget('text'))
                self.assertNotIn('이전 종료 실패', w.description.cget('text'))
                self.assertEqual(w.pause.cget('text'), '자동 관리 끄기')
                w.pause.invoke()
                self.assertFalse(c.engine.enabled)
                self.assertFalse(c.store.settings()['enabled'])
                self.assertIn('꺼져', w.status_label.cget('text'))
                self.assertEqual(c.power.requests, 0)
            finally:
                w.close()

    def test_policy_summary_distinguishes_sleep_input_waits_from_shutdown_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            c, w = self.make_window(folder)
            try:
                c.set_power_settings(300, 'sleep')
                w.update_settings_feedback()
                self.assertIn('입력 대기', w.applied_summary.cget('text'))
                c.set_power_settings(300, 'shutdown')
                w.update_settings_feedback()
                self.assertIn('모든 작업 완료', w.applied_summary.cget('text'))
                self.assertNotIn('입력 대기', w.applied_summary.cget('text'))
                self.assertFalse(c.engine.enabled)
                self.assertEqual(c.power.requests, 0)
            finally:
                w.close()
