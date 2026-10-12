"""Power transitions are mocked; these tests never turn the computer off."""
import ctypes as C
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sleep_manager.controller import Controller
from sleep_manager.power import WindowsPower
from sleep_manager.storage import Store
import test_app
import test_power


class ShutdownReliabilityTests(unittest.TestCase):
    def make_controller(self, folder):
        store = Store(Path(folder))
        store.save_settings(dict(delay=60, action='shutdown', enabled=True))
        native = test_power.FakeNative()
        native.check_shutdown_privilege = lambda: None
        native.shutdown = lambda: native.calls.append('shutdown')
        power = WindowsPower(_native=native)
        clock = [0]
        native.input_ticks = lambda: (int((clock[0]+1000)*1000), 0)
        controller = Controller(store, power, test_app.Reader(), clock=lambda: clock[0])
        store.append(dict(provider='claude', session_id='s', kind='complete', metadata_complete=True))
        controller.tick()
        return store, native, power, clock, controller

    def advance(self, clock, controller, until):
        for t in range(int(clock[0])+1, until+1):
            clock[0] = t
            controller.tick()

    def test_external_requests_and_query_failure_do_not_block_shutdown(self):
        for output in [(1, 'permission denied'), (0, 'unknown format'),
                       (0, test_power.EMPTY.replace('SYSTEM:\nNone.', 'SYSTEM:\n[DRIVER] audio\nactive'))]:
            native = test_power.FakeNative()
            native.shutdown = lambda: native.calls.append('shutdown')
            native.check_shutdown_privilege = lambda: None
            native.requests = lambda: (_ for _ in ()).throw(AssertionError('Shutdown must not query powercfg'))
            native.output = output
            WindowsPower(_native=native).shutdown()
            self.assertEqual(native.calls, ['shutdown'])

    def test_privilege_failure_is_before_submission_and_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            store, native, power, clock, c = self.make_controller(folder)
            native.check_shutdown_privilege = lambda: (_ for _ in ()).throw(PermissionError(1300, 'not assigned'))
            self.advance(clock, c, 150)
            self.assertNotIn('shutdown', native.calls)
            self.assertFalse(c.engine.enabled)
            self.assertEqual(c.power_action.reason, 'shutdown_privilege')
            restored = Controller(store, WindowsPower(_native=native), test_app.Reader())
            self.assertFalse(restored.engine.enabled)
            self.assertEqual(restored.power_action.reason, 'shutdown_privilege')
            self.assertTrue(restored.error)
            restored.close()
            c.close()

    def test_transient_preflight_retry_waits_30_seconds_then_succeeds(self):
        with tempfile.TemporaryDirectory() as folder:
            store, native, power, clock, c = self.make_controller(folder)
            original = c.reader.poll
            calls = [0]
            def poll():
                if clock[0] >= 60 and calls[0] == 0:
                    calls[0] += 1
                    raise TimeoutError('reader timeout')
                return original()
            c.reader.poll = poll
            self.advance(clock, c, 60)
            self.assertTrue(c.engine.enabled)
            self.assertEqual(c.power_action.phase, 'deferred')
            self.assertEqual(c.power_action.retry_at, 90)
            self.advance(clock, c, 89)
            self.assertNotIn('shutdown', native.calls)
            self.advance(clock, c, 150)
            self.assertEqual(native.calls.count('shutdown'), 1)
            self.assertEqual(c.power_action.phase, 'requested')
            c.close()

    def test_retry_limit_stops_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store, native, power, clock, c = self.make_controller(folder)
            c.reader.poll = lambda: (_ for _ in ()).throw(TimeoutError('timeout'))
            self.advance(clock, c, 400)
            self.assertFalse(c.engine.enabled)
            self.assertEqual(c.power_action.reason, 'retry_limit')
            self.assertEqual(c.power_action.attempts, 10)
            self.assertNotIn('shutdown', native.calls)
            restored = Controller(store, WindowsPower(_native=native), test_app.Reader())
            self.assertEqual(restored.power_action.reason, 'retry_limit')
            self.assertFalse(restored.engine.enabled)
            restored.close()
            c.close()

    def test_new_job_during_retry_cancels_previous_retry_cycle(self):
        with tempfile.TemporaryDirectory() as folder:
            store, native, power, clock, c = self.make_controller(folder)
            c.reader.poll = lambda: (_ for _ in ()).throw(TimeoutError('timeout'))
            self.advance(clock, c, 60)
            c.reader.poll = lambda: []
            store.append(dict(provider='codex', session_id='new', kind='start'))
            self.advance(clock, c, 95)
            self.assertTrue(c.engine.enabled)
            self.assertEqual(c.status.mode, 'running')
            self.assertEqual(c.power_action.attempts, 0)
            self.assertNotIn('shutdown', native.calls)
            c.close()

    def test_native_error_after_submission_never_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            store, native, power, clock, c = self.make_controller(folder)
            def shutdown():
                native.calls.append('shutdown')
                raise OSError('restore privilege failed after Windows accepted')
            native.shutdown = shutdown
            self.advance(clock, c, 400)
            self.assertEqual(native.calls.count('shutdown'), 1)
            self.assertFalse(c.engine.enabled)
            self.assertTrue(c.power_action.request_started)
            self.assertEqual(c.power_action.phase, 'uncertain')
            c.set_power_settings(60, 'shutdown')
            self.assertEqual(c.power_action.phase, 'uncertain')
            c.close()

    def test_atomic_intent_is_visible_to_a_second_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            store, native, power, clock, c = self.make_controller(folder)
            def shutdown():
                other = Store(Path(folder))
                self.assertFalse(other.settings()['enabled'])
                self.assertEqual(other.power_action()['phase'], 'submitting')
                native.calls.append('shutdown')
            native.shutdown = shutdown
            self.advance(clock, c, 100)
            self.assertEqual(native.calls.count('shutdown'), 1)
            diag = c.diagnostics()
            self.assertEqual(diag['database_path'], str(store.path.resolve()))
            self.assertTrue(diag['run_id'])
            self.assertEqual(diag['power_action']['phase'], 'requested')
            c.close()

    def test_sqlite_lock_is_typed_transient_and_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            store, native, power, clock, c = self.make_controller(folder)
            held = sqlite3.connect(store.path, timeout=0)
            held.execute('BEGIN IMMEDIATE')
            try:
                with patch.object(store, 'connect', wraps=store.connect):
                    # Avoid the normal five-second busy wait in this integration test.
                    original = store.connect
                    def short_connect():
                        db = original()
                        db.execute('PRAGMA busy_timeout=0')
                        return db
                    store.connect = short_connect
                    self.advance(clock, c, 60)
                self.assertEqual(c.power_action.phase, 'deferred')
                self.assertTrue(c.engine.enabled)
            finally:
                held.rollback()
                held.close()
            self.advance(clock, c, 160)
            self.assertEqual(native.calls.count('shutdown'), 1)
            c.close()

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows token mock')
    def test_permission_probe_restores_privilege_without_shutdown(self):
        native, calls = test_power.NativeBoundaryTests().make_native()
        native.check_shutdown_privilege()
        self.assertEqual(calls, ['enable', ('restore', 0), ('close', 0x100000001)])

    def test_final_event_timeout_restores_persisted_management_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            store,native,power,clock,c=self.make_controller(folder)
            polls=[0]
            def poll():
                if clock[0]>=60:
                    polls[0]+=1
                    if polls[0]%2==0: raise TimeoutError('final drain timeout')
                return []
            c.reader.poll=poll
            self.advance(clock,c,60)
            self.assertEqual(c.power_action.phase,'deferred')
            self.assertTrue(store.settings()['enabled'])
            self.advance(clock,c,400)
            self.assertFalse(c.engine.enabled)
            self.assertEqual(c.power_action.attempts,10)
            self.assertNotIn('shutdown',native.calls)
            c.close()

    def test_user_input_during_retry_starts_the_full_delay_again(self):
        with tempfile.TemporaryDirectory() as folder:
            store,native,power,clock,c=self.make_controller(folder)
            c.reader.poll=lambda: (_ for _ in ()).throw(TimeoutError('timeout'))
            self.advance(clock,c,60)
            c.reader.poll=lambda:[]
            native.input_ticks=lambda:(int((clock[0]+1000)*1000),1061000)
            self.advance(clock,c,61)
            self.assertEqual(c.power_action.phase,'idle')
            self.assertEqual(c.status.remaining,60)
            self.advance(clock,c,120)
            self.assertNotIn('shutdown',native.calls)
            self.advance(clock,c,122)
            self.assertEqual(native.calls.count('shutdown'),1)
            c.close()

    def test_result_save_failure_after_native_call_does_not_allow_restart_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            store,native,power,clock,c=self.make_controller(folder)
            original=store.save_power_action
            def save(payload,settings=None):
                if payload['phase']=='requested': raise OSError('disk full')
                return original(payload,settings)
            store.save_power_action=save
            self.advance(clock,c,180)
            self.assertEqual(native.calls.count('shutdown'),1)
            self.assertFalse(c.engine.enabled)
            self.assertEqual(store.power_action()['phase'],'submitting')
            store.save_power_action=original
            restored=Controller(store,WindowsPower(_native=native),test_app.Reader())
            self.assertFalse(restored.engine.enabled)
            self.assertEqual(restored.power_action.phase,'submitting')
            restored.close(); c.close()

    def test_unknown_preflight_fault_is_not_retried_or_hidden_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store,native,power,clock,c=self.make_controller(folder)
            calls=[0]
            def poll():
                calls[0]+=1
                raise ValueError('invalid lifecycle state')
            c.reader.poll=poll
            self.advance(clock,c,100)
            self.assertFalse(c.engine.enabled)
            self.assertEqual(c.power_action.phase,'failed')
            self.assertEqual(c.power_action.reason,'unknown_error')
            self.assertNotIn('shutdown',native.calls)
            restored=Controller(store,WindowsPower(_native=native),test_app.Reader())
            self.assertEqual(restored.power_action.phase,'failed')
            self.assertIn('invalid lifecycle',restored.error)
            restored.close(); c.close()

    def test_temporary_reader_fault_keeps_known_running_job_count(self):
        with tempfile.TemporaryDirectory() as folder:
            store,native,power,clock,c=self.make_controller(folder)
            store.append(dict(provider='codex',session_id='running',kind='start'))
            c.reader.poll=lambda: (_ for _ in ()).throw(TimeoutError('timeout'))
            clock[0]=1
            status=c.tick()
            self.assertEqual(status.running,1)
            self.assertTrue(power.required)
            self.assertNotIn('shutdown',native.calls)
            c.close()
