import ctypes as C
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from sleep_manager.engine import Engine
from sleep_manager.controller import Controller
from sleep_manager.storage import Store
from sleep_manager.power import WindowsPower
import test_app
import test_power


class ShutdownPolicyTests(unittest.TestCase):
    def test_default_is_sleep_and_waiting_still_allows_sleep(self):
        e=Engine(delay=60)
        self.assertEqual(e.action,'sleep')
        e.ingest(dict(provider='codex',session_id='s',kind='waiting'),0)
        e.tick(0,1000)
        self.assertTrue(e.tick(60,1000).sleep_due)

    def test_shutdown_waits_for_human_waiting_and_unknown(self):
        for kind in ('waiting','unknown'):
            e=Engine(delay=60,action='shutdown')
            e.ingest(dict(provider='codex',session_id='s',kind=kind),0)
            self.assertFalse(e.tick(5000,5000).sleep_due)
            self.assertIsNone(e.tick(5000,5000).remaining)

    def test_shutdown_complete_requires_full_delay_and_resets_on_input(self):
        e=Engine(delay=60,action='shutdown')
        e.ingest(dict(provider='codex',session_id='s',kind='complete'),0)
        self.assertEqual(e.tick(0,1000).remaining,60)
        self.assertEqual(e.tick(59,0).remaining,60)
        self.assertFalse(e.tick(118,59).sleep_due)
        self.assertTrue(e.tick(119,60).sleep_due)

    def test_action_change_resets_countdown_and_rejects_invalid_action(self):
        e=Engine(delay=60)
        e.ingest(dict(provider='codex',session_id='s',kind='complete'),0)
        e.tick(0,1000)
        e.set_action('shutdown',59)
        self.assertEqual(e.tick(59,1000).remaining,60)
        with self.assertRaises(ValueError): e.set_action('force',60)
        self.assertEqual(e.action,'shutdown')

    def test_running_child_blocks_shutdown(self):
        e=Engine(delay=60,action='shutdown')
        for kind in ('start','child_start','complete'):
            e.ingest(dict(provider='claude',session_id='s',kind=kind,task_id='child'),0)
        self.assertEqual(e.tick(1000,1000).mode,'running')
        self.assertFalse(e.tick(1000,1000).sleep_due)


class ShutdownPower(test_app.Power):
    def __init__(self,store):
        super().__init__()
        self.store=store
        self.shutdowns=0
        self.inject=None
        self.fail=False

    def shutdown(self,before_shutdown=None):
        if self.inject: self.inject()
        if before_shutdown and not before_shutdown(): raise RuntimeError('cancelled')
        assert self.store.settings()['enabled'] is False, 'Must persist disarm before native request'
        self.shutdowns+=1
        if self.fail: raise OSError('OS rejected request')


class ShutdownControllerTests(unittest.TestCase):
    def setup_controller(self,folder):
        store=Store(Path(folder)); store.save_settings(dict(delay=60,action='shutdown'))
        power=ShutdownPower(store); clock=[0]
        c=Controller(store,power,test_app.Reader(),clock=lambda:clock[0])
        store.append(dict(provider='claude',session_id='s',kind='complete',metadata_complete=True))
        c.tick()
        return store,power,clock,c

    def advance(self,clock,c,until=150):
        for t in range(1,until): clock[0]=t; c.tick()

    def test_shutdown_is_one_shot_disarmed_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            store,p,clock,c=self.setup_controller(d)
            self.advance(clock,c)
            self.assertEqual(p.shutdowns,1)
            self.assertEqual(p.requests,0)
            self.assertFalse(c.engine.enabled)
            self.assertFalse(p.required)
            restored=Controller(store,ShutdownPower(store),test_app.Reader())
            self.assertEqual(restored.engine.action,'shutdown')
            self.assertFalse(restored.engine.enabled)

    def test_shutdown_failure_does_not_repeat_or_claim_success(self):
        with tempfile.TemporaryDirectory() as d:
            store,p,clock,c=self.setup_controller(d); p.fail=True
            self.advance(clock,c)
            self.assertEqual(p.shutdowns,1)
            self.assertFalse(c.engine.enabled)
            self.assertIn('OS rejected',c.error)

    def test_new_work_during_shutdown_preflight_cancels_without_disarming(self):
        with tempfile.TemporaryDirectory() as d:
            store,p,clock,c=self.setup_controller(d)
            p.inject=lambda:store.append(dict(provider='codex',session_id='new',kind='start'))
            self.advance(clock,c,63)
            self.assertEqual(p.shutdowns,0)
            self.assertTrue(c.engine.enabled)
            self.assertTrue(p.required)

    def test_choice_is_saved_and_legacy_settings_default_to_sleep(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); c=Controller(store,test_app.Power(),test_app.Reader())
            self.assertEqual(c.engine.action,'sleep')
            c.set_action('shutdown')
            self.assertEqual(store.settings()['action'],'shutdown')


class ShutdownBoundaryTests(unittest.TestCase):
    def native(self):
        native=test_power.FakeNative()
        native.shutdown=lambda:native.calls.append('shutdown')
        return native

    def test_shutdown_does_not_require_supported_sleep(self):
        native=self.native(); native.allowed=False
        WindowsPower(_native=native).shutdown(before_shutdown=lambda:True)
        self.assertEqual(native.calls,['shutdown'])

    def test_dry_run_never_calls_native_shutdown(self):
        native=self.native(); power=WindowsPower(dry_run=True,_native=native)
        power.shutdown()
        self.assertEqual(native.calls,[])
        self.assertEqual(power.dry_run_requests,1)

    def test_external_blocker_cancellation_and_new_input_prevent_shutdown(self):
        for mode in ('blocker','job','input','closed','active'):
            native=self.native(); power=WindowsPower(_native=native)
            if mode=='blocker': native.output=(1,'unavailable')
            if mode=='input':
                readings=iter([(10000,7000),(11000,10500)])
                native.input_ticks=lambda:next(readings)
            if mode=='closed': power.close()
            if mode=='active': power.set_required(True)
            with self.assertRaises(RuntimeError):
                power.shutdown(before_shutdown=lambda:mode!='job')
            self.assertNotIn('shutdown',native.calls)

    @unittest.skipUnless(os.name=='nt','Windows ctypes error slot')
    def test_native_shutdown_uses_only_poweroff_and_restores_privilege(self):
        native,calls=test_power.NativeBoundaryTests().make_native()
        native.user=SimpleNamespace(ExitWindowsEx=lambda *args:(calls.append(('shutdown',args)) or 1))
        native.shutdown()
        self.assertEqual(calls,['enable',('shutdown',(8,0x80040000)),('restore',0),('close',0x100000001)])

    @unittest.skipUnless(os.name=='nt','Windows ctypes error slot')
    def test_native_shutdown_failure_and_unassigned_privilege_are_safe(self):
        for error in (0,1300):
            native,calls=test_power.NativeBoundaryTests().make_native(enable_error=error)
            native.user=SimpleNamespace(ExitWindowsEx=lambda *args:(calls.append('shutdown') or 0))
            with self.assertRaises(OSError): native.shutdown()
            self.assertIn(('restore',0),calls)
            self.assertIn(('close',0x100000001),calls)
            if error: self.assertNotIn('shutdown',calls)

class ShutdownRaceTests(unittest.TestCase):
    def run_race(self,mode):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); store.save_settings(dict(delay=60,action='shutdown'))
            clock=[0]; last_input=[0]; saved=store.save_settings
            native=test_power.FakeNative()
            native.input_ticks=lambda:(int((clock[0]+1000)*1000),last_input[0])
            native.shutdown=lambda:native.calls.append('shutdown')
            power=WindowsPower(dry_run=True,_native=native)
            c=Controller(store,power,test_app.Reader(),clock=lambda:clock[0])
            injected=[False]
            def save(settings):
                saved(settings)
                if not settings['enabled'] and not injected[0]:
                    injected[0]=True
                    if mode=='job': store.append(dict(provider='codex',session_id='new',kind='start'))
                    elif mode=='save_input': last_input[0]=int((clock[0]+1000)*1000)
            store.save_settings=save
            if mode=='final_input':
                original_shutdown=power.shutdown
                def shutdown(before_shutdown=None):
                    def callback():
                        ready=before_shutdown()
                        last_input[0]=int((clock[0]+1000)*1000)
                        return ready
                    return original_shutdown(before_shutdown=callback)
                power.shutdown=shutdown
            store.append(dict(provider='claude',session_id='s',kind='complete',metadata_complete=True))
            c.tick()
            for t in range(1,62): clock[0]=t; c.tick()
            self.assertEqual(power.dry_run_requests,0)
            self.assertTrue(c.engine.enabled)
            self.assertTrue(store.settings()['enabled'])
            store.append(dict(provider='codex',session_id='following',kind='start'))
            c.tick()
            self.assertTrue(power.required)

    def test_new_job_during_disarm_save_cancels_shutdown(self): self.run_race('job')
    def test_user_input_during_disarm_save_resets_countdown(self): self.run_race('save_input')
    def test_input_after_final_callback_restores_management(self): self.run_race('final_input')

class ShutdownPersistenceFailureTests(unittest.TestCase):
    def test_failed_disarm_save_never_reaches_shutdown(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)); store.save_settings(dict(delay=60,action='shutdown'))
            p=ShutdownPower(store); clock=[0]
            c=Controller(store,p,test_app.Reader(),clock=lambda:clock[0])
            store.append(dict(provider='claude',session_id='s',kind='complete',metadata_complete=True))
            c.tick()
            def fail(settings): raise OSError('settings unavailable')
            store.save_settings=fail
            for t in range(1,70): clock[0]=t; c.tick()
            self.assertEqual(p.shutdowns,0)
            self.assertFalse(c.engine.enabled)
            self.assertFalse(p.required)
