import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from sleep_manager.controller import Controller
from sleep_manager.storage import Store
from test_app import Power,Reader
from sleep_manager.power_state import PowerActionState


@unittest.skipUnless(__import__('os').name=='nt','Windows Tk UI')
class DashboardUITests(unittest.TestCase):
    def test_persisted_failure_is_visible_without_opening_diagnostics(self):
        from sleep_manager.ui import Window
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); store.save_settings(dict(enabled=False,action='shutdown',delay=300))
            store.save_power_action(PowerActionState('failed','shutdown_privilege').payload())
            power=Power(); power.dry_run=False; power.process_names=lambda:set()
            c=Controller(store,power,Reader())
            w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=Path(d)),[]); w.root.withdraw()
            try:
                w.refresh()
                self.assertIn('종료 권한',w.description.cget('text'))
                self.assertIn('자동 관리 중지',w.applied_summary.cget('text'))
                self.assertIn('종료 권한',w.execution_status.cget('text'))
                self.assertEqual(w.pause.cget('text'),'자동 관리 켜기')
            finally: w.close()

    def test_permission_probe_does_not_confuse_non_admin_with_missing_right(self):
        from sleep_manager.ui import Window
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); store.save_settings(dict(action='shutdown',delay=300))
            power=Power(); power.dry_run=False; power.shutdown_permission=lambda:dict(available=True,error_code=0)
            power.process_names=lambda:set()
            c=Controller(store,power,Reader())
            w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=Path(d)),[]); w.root.withdraw()
            try:
                w.admin=False; w.refresh()
                self.assertNotIn('관리자 권한',w.description.cget('text'))
                self.assertFalse(w.elevate_button.winfo_manager())
            finally: w.close()

    def test_settings_are_collapsible_and_edits_are_not_applied_implicitly(self):
        from sleep_manager.ui import Window
        with tempfile.TemporaryDirectory() as d:
            c=Controller(Store(Path(d)),Power(),Reader())
            w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=Path(d)),[]); w.root.withdraw()
            try:
                self.assertFalse(w.settings_open)
                w.toggle_settings()
                self.assertTrue(w.settings_open)
                w.minutes.set('15'); w.action.set('정상 종료')
                w.toggle_settings()
                self.assertEqual(c.engine.delay,600)
                self.assertEqual(c.engine.action,'sleep')
                w.toggle_settings(); w.apply_delay()
                self.assertEqual(c.engine.delay,900)
                self.assertEqual(c.engine.action,'shutdown')
            finally: w.close()

    def test_details_remain_available_next_to_selected_job_and_empty_state_clears_badge(self):
        from sleep_manager.ui import Window
        with tempfile.TemporaryDirectory() as d:
            c=Controller(Store(Path(d)),Power(),Reader())
            c._accept(dict(provider='codex',session_id='s',kind='start'))
            w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=Path(d)),[]); w.root.withdraw()
            w.catalog=SimpleNamespace(lookup=lambda jobs:{'codex:s':dict(title='테스트 작업',latest_request='진행해줘')})
            try:
                w.render_jobs(); w.root.update_idletasks()
                self.assertIn('진행해줘',w.details.get('1.0','end'))
                self.assertEqual(w.detail_badge.cget('text'),'작업 중')
                c._accept(dict(provider='codex',session_id='s',kind='complete'))
                w.render_jobs()
                self.assertEqual(w.detail_badge.cget('text'),'선택 없음')
                w.show_complete.set(True); w.render_jobs()
                self.assertEqual(w.detail_badge.cget('text'),'완료')
            finally: w.close()
