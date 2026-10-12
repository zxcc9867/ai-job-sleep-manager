import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sleep_manager.power import WindowsPower
from sleep_manager.controller import Controller
from sleep_manager.storage import Store
from test_power import FakeNative
from test_app import Reader

class Native(FakeNative):
    def process_names(self): return {'codex.exe'}
    def shutdown(self): self.calls.append('shutdown')

class ModeAdapterTests(unittest.TestCase):
    def test_simulated_request_becomes_real_then_is_released(self):
        n=Native();p=WindowsPower(dry_run=True,_native=n)
        try:
            p.set_required(True);self.assertEqual(n.calls,[])
            p.set_dry_run(False)
            self.assertFalse(p.dry_run)
            self.assertIn(('set',0x100000001),n.calls)
            p.set_dry_run(True)
            self.assertTrue(p.dry_run)
            self.assertIn(('clear',0x100000001),n.calls)
            self.assertNotIn('shutdown',n.calls);self.assertNotIn('suspend',n.calls)
        finally:p.close()

    def test_failed_real_request_keeps_observation_mode(self):
        n=Native();p=WindowsPower(dry_run=True,_native=n);p.set_required(True);n.fail='set'
        try:
            with self.assertRaises(OSError):p.set_dry_run(False)
            self.assertTrue(p.dry_run)
            self.assertTrue(p.required)
        finally:n.fail=None;p.close()

    def test_failed_release_does_not_claim_observation_mode(self):
        n=Native();p=WindowsPower(_native=n);p.set_required(True);n.fail='clear'
        try:
            with self.assertRaises(OSError):p.set_dry_run(True)
            self.assertFalse(p.dry_run)
            self.assertTrue(p.required)
        finally:n.fail=None;p.close()

class ModeControllerTests(unittest.TestCase):
    def test_live_switch_resets_elapsed_timer_without_power_transition(self):
        with tempfile.TemporaryDirectory() as d:
            n=Native();p=WindowsPower(dry_run=True,_native=n);clock=[1000]
            c=Controller(Store(Path(d)),p,Reader(),clock=lambda:clock[0])
            c._accept(dict(provider='codex',session_id='s',kind='complete',metadata_complete=True))
            c.engine.baseline=0
            try:
                c.set_execution_mode(False)
                self.assertFalse(p.dry_run)
                self.assertEqual(c.status.remaining,600)
                self.assertNotIn('suspend',n.calls);self.assertNotIn('shutdown',n.calls)
            finally:c.close()

    def test_paused_and_unknown_states_remain_gates_after_switch(self):
        with tempfile.TemporaryDirectory() as d:
            p=WindowsPower(dry_run=True,_native=Native());c=Controller(Store(Path(d)),p,Reader())
            try:
                c._accept(dict(provider='codex',session_id='real-session',kind='unknown'))
                c.set_execution_mode(False)
                self.assertEqual(c.status.mode,'unknown')
                c.engine.set_enabled(False,0)
                c.set_execution_mode(True);c.set_execution_mode(False)
                self.assertFalse(c.engine.enabled)
                self.assertEqual(c.status.mode,'paused')
            finally:c.close()

    def test_live_recheck_failure_leaves_observation_mode(self):
        with tempfile.TemporaryDirectory() as d:
            n=Native();p=WindowsPower(dry_run=True,_native=n);c=Controller(Store(Path(d)),p,Reader())
            try:
                with patch.object(n,'process_names',side_effect=OSError('unavailable')):
                    with self.assertRaises(OSError):c.set_execution_mode(False)
                self.assertTrue(p.dry_run)
            finally:c.close()

@unittest.skipUnless(__import__('os').name=='nt','Windows Tk UI')
class ModeUITests(unittest.TestCase):
    def test_admin_mode_selection_updates_badge_and_keeps_draft(self):
        from sleep_manager.ui import Window
        with tempfile.TemporaryDirectory() as d:
            p=WindowsPower(dry_run=True,_native=Native());c=Controller(Store(Path(d)),p,Reader())
            w=Window(c,SimpleNamespace(smoke_ui=False,data_dir=Path(d)),[]);w.root.withdraw()
            try:
                w.admin=True
                w.minutes.set('7')
                w.execution_mode.set('실제 전원 제어')
                self.assertTrue(p.dry_run)
                w.mode_select.event_generate('<<ComboboxSelected>>')
                self.assertFalse(p.dry_run)
                self.assertIn('실제',w.mode_badge.cget('text'))
                self.assertEqual(w.minutes.get(),'7')
                self.assertEqual(c.engine.delay,600)
                self.assertIn('실제 자동 관리',w.execution_status.cget('text'))
                w.execution_mode.set('관찰 모드');w.apply_execution_mode()
                self.assertTrue(p.dry_run)
                self.assertIn('관찰',w.mode_badge.cget('text'))
                self.assertNotIn('관찰 모드라',w.save_message)
            finally:w.close()
