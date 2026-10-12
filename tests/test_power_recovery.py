import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sleep_manager.controller import Controller
from sleep_manager.storage import Store
from test_app import Power, Reader
from test_mode_toggle import Native
from sleep_manager.power import WindowsPower


class PowerRecoveryTests(unittest.TestCase):
    def test_reboot_interrupts_preboot_work_without_arming_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            store.save_settings(dict(delay=60, action='shutdown'))
            first = Controller(store, Power(), Reader(), boot_epoch=1000)
            store.append(dict(provider='claude', session_id='night', kind='start', timestamp=1500))
            self.assertEqual(first.tick().mode, 'running')
            first.close()

            restored = Controller(store, Power(), Reader(), boot_epoch=2000)
            try:
                self.assertEqual(restored.engine.jobs['claude:night'].state, 'interrupted')
                state = restored.tick()
                self.assertEqual(state.mode, 'interrupted')
                self.assertFalse(state.sleep_due)
                self.assertFalse(state.required)
            finally:
                restored.close()

    def test_same_boot_and_post_boot_event_are_not_mislabeled_interrupted(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            first = Controller(store, Power(), Reader(), boot_epoch=1000)
            first.close()
            store.append(dict(provider='claude', session_id='old', kind='start', timestamp=1500))
            same = Controller(store, Power(), Reader(), boot_epoch=1000)
            self.assertEqual(same.engine.jobs['claude:old'].state, 'unknown')
            same.close()

            store.append(dict(provider='claude', session_id='new', kind='start', timestamp=2100))
            next_boot = Controller(store, Power(), Reader(), boot_epoch=2000)
            try:
                self.assertEqual(next_boot.engine.jobs['claude:old'].state, 'interrupted')
                self.assertEqual(next_boot.engine.jobs['claude:new'].state, 'unknown')
            finally:
                next_boot.close()

    def test_user_confirmation_is_durable_and_does_not_finish_other_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            first = Controller(store, Power(), Reader(), boot_epoch=1000)
            store.append(dict(provider='claude', session_id='old', kind='start', timestamp=1500))
            store.append(dict(provider='claude', session_id='other', kind='start', timestamp=1501))
            first.tick()
            first.close()

            current = Controller(store, Power(), Reader(), boot_epoch=2000)
            try:
                current.confirm_job_finished('claude:old')
                self.assertEqual(current.engine.jobs['claude:old'].state, 'complete')
                self.assertEqual(current.engine.jobs['claude:other'].state, 'interrupted')
                self.assertEqual(current.tick().mode, 'interrupted')
                with self.assertRaises(ValueError):
                    current.confirm_job_finished('claude:old')
            finally:
                current.close()
            restarted = Controller(store, Power(), Reader(), boot_epoch=2000)
            try:
                self.assertNotIn('claude:old', restarted.engine.jobs)
                self.assertEqual(restarted.engine.jobs['claude:other'].state, 'interrupted')
            finally:
                restarted.close()

    def test_hook_arriving_during_reboot_recovery_is_not_skipped(self):
        class RacingStore(Store):
            injected = False
            def append(self, event, event_id=None):
                if event['kind'] == 'interrupted' and not self.injected:
                    self.injected = True
                    super().append(dict(provider='codex', session_id='fresh',
                                        kind='start', timestamp=2100))
                return super().append(event, event_id)

        with tempfile.TemporaryDirectory() as folder:
            store = RacingStore(Path(folder))
            first = Controller(store, Power(), Reader(), boot_epoch=1000)
            store.append(dict(provider='claude', session_id='old', kind='start', timestamp=1500))
            first.tick()
            first.close()
            recovered = Controller(store, Power(), Reader(), boot_epoch=2000)
            try:
                self.assertEqual(recovered.tick().mode, 'running')
                self.assertEqual(recovered.engine.jobs['codex:fresh'].state, 'running')
            finally:
                recovered.close()

    def test_failed_live_mode_save_does_not_leave_unstored_live_control(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            power = WindowsPower(dry_run=True, _native=Native())
            controller = Controller(store, power, Reader())
            try:
                with patch.object(store, 'save_execution_dry_run', side_effect=OSError('disk full')):
                    with self.assertRaises(OSError):
                        controller.set_execution_mode(False)
                self.assertTrue(power.dry_run)
                self.assertTrue(store.execution_dry_run())
            finally:
                controller.close()

    def test_reboot_and_manual_resolution_leave_a_power_diagnostic_trail(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            first = Controller(store, Power(), Reader(), boot_epoch=1000)
            store.append(dict(provider='claude', session_id='old', kind='start', timestamp=1500))
            first.tick()
            first.close()
            recovered = Controller(store, Power(), Reader(), boot_epoch=2000)
            try:
                recovered.tick()
                recovered.confirm_job_finished('claude:old')
                events = store.power_events()
                self.assertIn('reboot_interrupted', [event['code'] for event in events])
                self.assertIn('user_confirmed', [event['code'] for event in events])
                self.assertTrue(any('1' in event['detail'] for event in events
                                    if event['code'] == 'reboot_interrupted'))
            finally:
                recovered.close()

    def test_interrupted_job_is_named_and_explained_in_task_list(self):
        from sleep_manager.engine import Job
        from sleep_manager.job_view import job_row
        row = job_row(Job('claude', 's', state='interrupted'), {}, 'shutdown')
        self.assertEqual(row['category'], 'interrupted')
        self.assertTrue(row['pending'])
        self.assertIn('재부팅', row['state'])
        self.assertIn('전원 전환', row['reason'])

    def test_live_mode_is_saved_but_explicit_dry_run_overrides_startup(self):
        from app import resolve_dry_run
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            self.assertTrue(resolve_dry_run(store))
            power = WindowsPower(dry_run=True, _native=Native())
            controller = Controller(store, power, Reader())
            try:
                controller.set_execution_mode(False)
            finally:
                controller.close()
            self.assertFalse(resolve_dry_run(store))
            self.assertTrue(resolve_dry_run(store, force_observe=True))


@unittest.skipUnless(__import__('os').name == 'nt', 'Windows Tk UI')
class PowerRecoveryUITests(unittest.TestCase):
    def test_reboot_blocker_and_manual_confirmation_are_visible(self):
        from sleep_manager.ui import Window
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            first = Controller(store, Power(), Reader(), boot_epoch=1000)
            store.append(dict(provider='claude', session_id='old', kind='start', timestamp=1500))
            first.tick()
            first.close()

            controller = Controller(store, Power(), Reader(), boot_epoch=2000)
            window = Window(controller, SimpleNamespace(smoke_ui=False, data_dir=Path(folder),
                                                        dry_run=False), [])
            window.root.withdraw()
            try:
                window.render_jobs()
                window.update_settings_feedback()
                self.assertIn('재부팅', window.history_notice.cget('text'))
                self.assertIn('재부팅 전 종료 미확인', window.execution_status.cget('text'))
                window.show_history.set(True)
                window.render_jobs()
                window.table.selection_set('claude:old')
                window.show_job_details()
                self.assertIn('재부팅', window.details.get('1.0', 'end'))
                with patch('sleep_manager.ui.messagebox.askyesno', return_value=True):
                    window.confirm_selected_finished()
                self.assertEqual(controller.engine.jobs['claude:old'].state, 'complete')
            finally:
                window.close()


if __name__ == '__main__':
    unittest.main()
