import unittest
import ctypes as C
import os
from types import SimpleNamespace
from sleep_manager import power as module
from unittest.mock import patch

from sleep_manager.power import WindowsPower, parse_requests, idle_seconds


EMPTY = '\n\n'.join(name + ':\nNone.' for name in (
    'DISPLAY', 'SYSTEM', 'AWAYMODE', 'EXECUTION', 'PERFBOOST', 'ACTIVELOCKSCREEN'))


class FakeNative:
    def __init__(self):
        self.calls = []
        self.allowed = True
        self.output = (0, EMPTY)
        self.fail = None

    def create_request(self):
        self.calls.append('create')
        return 0x100000001

    def set_request(self, handle):
        self.calls.append(('set', handle))
        if self.fail == 'set':
            raise OSError('set failed')

    def clear_request(self, handle):
        self.calls.append(('clear', handle))
        if self.fail == 'clear':
            raise OSError('clear failed')

    def close_handle(self, handle):
        self.calls.append(('close', handle))

    def requests(self):
        return self.output

    def diagnostics(self):
        return {'suspend_allowed': self.allowed, 'modern_standby': False, 'ac_power': True}

    def suspend(self):
        self.calls.append('suspend')
        if self.fail == 'suspend':
            raise OSError('privilege missing')

    def input_ticks(self):
        return (10000, 7000)


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeNative()
        self.power = WindowsPower(_native=self.native)

    def test_request_is_idempotent_and_handle_is_closed(self):
        self.power.set_required(True)
        self.power.set_required(True)
        self.power.set_required(False)
        self.power.close()
        self.power.close()
        self.assertEqual(self.native.calls, ['create', ('set', 0x100000001),
                         ('clear', 0x100000001), ('close', 0x100000001)])

    def test_failed_set_never_claims_protection_and_closes_handle(self):
        self.native.fail = 'set'
        with self.assertRaises(OSError):
            self.power.set_required(True)
        self.assertFalse(self.power.required)
        self.assertIn(('close', 0x100000001), self.native.calls)

    def test_close_cleans_up_even_if_clear_fails(self):
        self.power.set_required(True)
        self.native.fail = 'clear'
        with self.assertRaises(OSError):
            self.power.close()
        self.assertIn(('close', 0x100000001), self.native.calls)
        self.power.close()

    def test_dry_run_never_mutates_native_power(self):
        power = WindowsPower(dry_run=True, _native=self.native)
        power.set_required(True)
        power.set_required(False)
        power.suspend()
        power.close()
        self.assertEqual(power.dry_run_requests, 1)
        self.assertEqual(self.native.calls, [])

    def test_conflicts_and_unknown_requests_prevent_even_dry_sleep(self):
        for output in ((1, 'access denied'), (0, 'unrecognized'),
                       (0, EMPTY.replace('SYSTEM:\nNone.', 'SYSTEM:\n[PROCESS] secret.exe\nworking'))):
            self.native.output = output
            for dry in (False, True):
                with self.assertRaises(RuntimeError):
                    WindowsPower(dry_run=dry, _native=self.native).suspend()
        self.assertNotIn('suspend', self.native.calls)

    def test_active_own_request_and_unsupported_sleep_are_rejected(self):
        self.power.set_required(True)
        with self.assertRaises(RuntimeError):
            self.power.suspend()
        self.power.set_required(False)
        self.native.allowed = False
        with self.assertRaises(RuntimeError):
            self.power.suspend()
        self.assertNotIn('suspend', self.native.calls)

    def test_native_suspend_failure_is_not_reported_as_success(self):
        self.native.fail = 'suspend'
        with self.assertRaises(OSError):
            self.power.suspend()
        self.assertEqual(self.power.dry_run_requests, 0)

    def test_idle_wrap_and_future_injected_timestamp_are_conservative(self):
        self.assertEqual(idle_seconds(0x100000100, 0xFFFFFF00), .512)
        self.assertEqual(idle_seconds(1000, 2000), 0)
        self.assertEqual(self.power.input_idle_seconds(), 3)

    def test_empty_locales_and_private_conflict_messages(self):
        self.assertEqual(parse_requests(EMPTY), [])
        self.assertEqual(parse_requests(EMPTY.replace('None.', '없음.')), [])
        result = parse_requests(EMPTY.replace('DISPLAY:\nNone.', 'DISPLAY:\n[PROCESS] secret-path\nprivate reason'))
        self.assertEqual(result, ['external_power_request:DISPLAY'])
        self.assertEqual(parse_requests('SYSTEM:\nNone.'), ['power_requests_unknown:unrecognized_output'])

    def test_closed_adapter_cannot_be_reactivated(self):
        self.power.close()
        with self.assertRaises(RuntimeError):
            self.power.set_required(True)

    def test_new_input_during_power_inventory_prevents_suspend(self):
        readings = iter([(10000, 7000), (11000, 10500)])
        self.native.input_ticks = lambda: next(readings)
        with self.assertRaisesRegex(RuntimeError, 'input'):
            self.power.suspend()
        self.assertNotIn('suspend', self.native.calls)

    def test_final_callback_runs_after_inventory_and_cancels_new_jobs(self):
        calls = []
        self.native.requests = lambda: (calls.append('inventory') or (0, EMPTY))
        with self.assertRaisesRegex(RuntimeError, 'state changed'):
            self.power.suspend(before_suspend=lambda: (calls.append('final') or False))
        self.assertEqual(calls, ['inventory', 'final'])
        self.assertNotIn('suspend', self.native.calls)


@unittest.skipUnless(os.name == 'nt', 'Windows ctypes error slot')
class NativeBoundaryTests(unittest.TestCase):
    def make_native(self, enable_error=0):
        native = module._NativePower.__new__(module._NativePower)
        calls = []

        def open_token(process, access, pointer):
            C.cast(pointer, C.POINTER(module.HANDLE)).contents.value = 0x100000001
            return 1

        def adjust(token, disable, wanted, size, previous, length):
            if previous is not None:
                old = C.cast(previous, C.POINTER(module.TokenPrivileges)).contents
                old.count = 1
                old.privileges[0].attributes = 0
                calls.append('enable')
                C.set_last_error(enable_error)
            else:
                old = C.cast(wanted, C.POINTER(module.TokenPrivileges)).contents
                calls.append(('restore', old.privileges[0].attributes))
            return 1

        def fail_suspend(*args):
            calls.append(('suspend', args))
            C.set_last_error(5)
            return 0

        native.kernel = SimpleNamespace(GetCurrentProcess=lambda: -1)
        native.advapi = SimpleNamespace(OpenProcessToken=open_token,
            LookupPrivilegeValueW=lambda *args: 1, AdjustTokenPrivileges=adjust)
        native.powr = SimpleNamespace(SetSuspendState=fail_suspend)
        native.close_handle = lambda token: calls.append(('close', getattr(token, 'value', token)))
        return native, calls

    def test_privilege_restored_and_token_closed_on_suspend_failure(self):
        native, calls = self.make_native()
        with self.assertRaisesRegex(OSError, 'SetSuspendState'):
            native.suspend()
        self.assertEqual(calls, ['enable', ('suspend', (False, False, False)),
                                ('restore', 0), ('close', 0x100000001)])

    def test_unassigned_privilege_never_reaches_suspend(self):
        native, calls = self.make_native(enable_error=1300)
        with self.assertRaisesRegex(OSError, 'could not be enabled'):
            native.suspend()
        self.assertEqual(calls, ['enable', ('restore', 0), ('close', 0x100000001)])

    def test_process_inventory_returns_names_only_and_closes_snapshot(self):
        native, calls = self.make_native()
        items = iter(['CODEX.EXE', 'claude.exe'])

        def next_process(snapshot, pointer):
            try:
                name = next(items)
            except StopIteration:
                C.set_last_error(18)
                return 0
            entry = C.cast(pointer, C.POINTER(module.ProcessEntry)).contents
            entry.exe = name
            return 1

        native.kernel.CreateToolhelp32Snapshot = lambda *args: 0x100000002
        native.kernel.Process32FirstW = next_process
        native.kernel.Process32NextW = next_process
        self.assertEqual(native.process_names(), {'codex.exe', 'claude.exe'})
        self.assertEqual(calls, [('close', 0x100000002)])


if __name__ == '__main__':
    unittest.main()
