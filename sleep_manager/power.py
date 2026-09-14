"""Windows power boundary. No persistent power policy or request overrides.

GetLastInputInfo covers the invoking Windows session, including injected input;
it cannot prove physical presence or observe input in other user sessions.
Power requests cannot defeat lid/power-button actions, critical battery policy,
or Modern Standby DC request expiry. Explicit sleep is deliberately fail-closed
when the localized powercfg request inventory cannot be verified.
"""
from __future__ import annotations

import ctypes as C
import os
import subprocess
import threading
from collections.abc import Callable


DWORD = C.c_uint32
BOOL = C.c_int32
BYTE = C.c_ubyte
HANDLE = C.c_void_p


class ProcessEntry(C.Structure):
    _fields_ = [('size', DWORD), ('usage', DWORD), ('pid', DWORD),
                ('heap', C.c_size_t), ('module', DWORD), ('threads', DWORD),
                ('parent_pid', DWORD), ('priority', C.c_int32), ('flags', DWORD),
                ('exe', C.c_wchar * 260)]


class LastInput(C.Structure):
    _fields_ = [('cbSize', DWORD), ('dwTime', DWORD)]


class DetailedReason(C.Structure):
    _fields_ = [('module', HANDLE), ('id', DWORD), ('count', DWORD),
                ('strings', C.POINTER(C.c_wchar_p))]


class ReasonUnion(C.Union):
    _fields_ = [('detailed', DetailedReason), ('simple', C.c_wchar_p)]


class ReasonContext(C.Structure):
    _fields_ = [('version', DWORD), ('flags', DWORD), ('reason', ReasonUnion)]


class Luid(C.Structure):
    _fields_ = [('low', DWORD), ('high', C.c_int32)]


class LuidAttributes(C.Structure):
    _fields_ = [('luid', Luid), ('attributes', DWORD)]


class TokenPrivileges(C.Structure):
    _fields_ = [('count', DWORD), ('privileges', LuidAttributes * 1)]


class PowerStatus(C.Structure):
    _fields_ = [('ac', BYTE), ('battery_flags', BYTE), ('percent', BYTE),
                ('saver', BYTE), ('lifetime', DWORD), ('full_lifetime', DWORD)]


class PowerCapabilities(C.Structure):
    # Windows 10+ SDK SYSTEM_POWER_CAPABILITIES, fixed-width Windows types.
    _fields_ = [(name, BYTE) for name in (
        'power_button', 'sleep_button', 'lid', 's1', 's2', 's3', 's4', 's5',
        'hiber_file', 'full_wake', 'video_dim', 'apm', 'ups', 'thermal',
        'throttle', 'min_throttle', 'max_throttle', 'fast_s4', 'hiberboot',
        'wake_alarm', 'aoac', 'disk_spin', 'hiber_type', 'aoac_connectivity')]
    _fields_ += [('spare', BYTE * 6), ('batteries', BYTE), ('short_term', BYTE),
                 ('battery_scales', DWORD * 6), ('wake_states', DWORD * 5)]


def idle_seconds(now_ticks: int, last_input_ticks: int) -> float:
    """Reconcile DWORD input time with 64-bit uptime; treat anomalous age as input.

    The 32-bit timestamp cannot disambiguate more than one 49.7-day wrap.
    A delta over half its range is conservatively treated as recent input.
    """
    elapsed = (now_ticks - last_input_ticks) & 0xFFFFFFFF
    return elapsed / 1000 if elapsed < 0x80000000 else 0.0


_CATEGORIES = {'DISPLAY', 'SYSTEM', 'AWAYMODE', 'EXECUTION', 'PERFBOOST', 'ACTIVELOCKSCREEN'}
_EMPTY = {'none.', '없음.', 'なし。', 'なし.'}


def parse_requests(output: str) -> list[str]:
    """Accept only a complete known request inventory; redact caller details.

    Windows' headings are normally English even with Korean localized empty
    labels. Unknown locales/formats return unknown instead of assuming empty.
    """
    sections: dict[str, list[str]] = {}
    current = None
    for raw in output.splitlines():
        line = raw.strip().lstrip('\ufeff')
        if not line:
            continue
        if line.endswith(':') and line[:-1] in _CATEGORIES:
            current = line[:-1]
            if current in sections:
                return ['power_requests_unknown:unrecognized_output']
            sections[current] = []
        elif current is None:
            return ['power_requests_unknown:unrecognized_output']
        else:
            sections[current].append(line)
    if set(sections) != _CATEGORIES:
        return ['power_requests_unknown:unrecognized_output']
    blockers = []
    for category, lines in sections.items():
        if len(lines) == 1 and lines[0].casefold() in _EMPTY:
            continue
        if lines and lines[0].startswith('['):
            blockers.append('external_power_request:' + category)
        else:
            blockers.append('power_requests_unknown:' + category)
    return blockers


class _NativePower:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('Windows power APIs are only available on Windows')
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.user = C.WinDLL('user32', use_last_error=True)
        self.powr = C.WinDLL('powrprof', use_last_error=True)
        self.advapi = C.WinDLL('advapi32', use_last_error=True)
        signatures = [
            (self.kernel, 'CreateToolhelp32Snapshot', [DWORD, DWORD], HANDLE),
            (self.kernel, 'Process32FirstW', [HANDLE, C.POINTER(ProcessEntry)], BOOL),
            (self.kernel, 'Process32NextW', [HANDLE, C.POINTER(ProcessEntry)], BOOL),
            (self.kernel, 'PowerCreateRequest', [C.POINTER(ReasonContext)], HANDLE),
            (self.kernel, 'PowerSetRequest', [HANDLE, C.c_int], BOOL),
            (self.kernel, 'PowerClearRequest', [HANDLE, C.c_int], BOOL),
            (self.kernel, 'CloseHandle', [HANDLE], BOOL),
            (self.kernel, 'GetCurrentProcess', [], HANDLE),
            (self.kernel, 'GetTickCount64', [], C.c_uint64),
            (self.kernel, 'GetSystemPowerStatus', [C.POINTER(PowerStatus)], BOOL),
            (self.user, 'GetLastInputInfo', [C.POINTER(LastInput)], BOOL),
            (self.user, 'ExitWindowsEx', [DWORD, DWORD], BOOL),
            (self.powr, 'IsPwrSuspendAllowed', [], BYTE),
            (self.powr, 'GetPwrCapabilities', [C.POINTER(PowerCapabilities)], BYTE),
            (self.powr, 'SetSuspendState', [BYTE, BYTE, BYTE], BYTE),
            (self.advapi, 'OpenProcessToken', [HANDLE, DWORD, C.POINTER(HANDLE)], BOOL),
            (self.advapi, 'LookupPrivilegeValueW', [C.c_wchar_p, C.c_wchar_p, C.POINTER(Luid)], BOOL),
            (self.advapi, 'AdjustTokenPrivileges', [HANDLE, BOOL, C.POINTER(TokenPrivileges),
                DWORD, C.POINTER(TokenPrivileges), C.POINTER(DWORD)], BOOL),
        ]
        for library, name, args, result in signatures:
            function = getattr(library, name)
            function.argtypes, function.restype = args, result

    @staticmethod
    def _check(result, name):
        if not result:
            code = C.get_last_error()
            raise OSError(code, name + ' failed (Windows error ' + str(code) + ')')

    def create_request(self):
        context = ReasonContext(0, 1, ReasonUnion(simple='AI Job Sleep Manager: active job or countdown'))
        handle = self.kernel.PowerCreateRequest(C.byref(context))
        if handle is None or handle == C.c_void_p(-1).value:
            self._check(False, 'PowerCreateRequest')
        return handle

    def set_request(self, handle):
        self._check(self.kernel.PowerSetRequest(handle, 1), 'PowerSetRequest')

    def clear_request(self, handle):
        self._check(self.kernel.PowerClearRequest(handle, 1), 'PowerClearRequest')

    def close_handle(self, handle):
        self._check(self.kernel.CloseHandle(handle), 'CloseHandle')

    def input_ticks(self):
        info = LastInput(C.sizeof(LastInput), 0)
        self._check(self.user.GetLastInputInfo(C.byref(info)), 'GetLastInputInfo')
        return self.kernel.GetTickCount64(), info.dwTime

    def process_names(self) -> set[str]:
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot is None or snapshot == C.c_void_p(-1).value:
            self._check(False, 'CreateToolhelp32Snapshot')
        try:
            entry = ProcessEntry()
            entry.size = C.sizeof(entry)
            names = set()
            result = self.kernel.Process32FirstW(snapshot, C.byref(entry))
            while result:
                names.add(entry.exe.casefold())
                result = self.kernel.Process32NextW(snapshot, C.byref(entry))
            if C.get_last_error() != 18:  # ERROR_NO_MORE_FILES is normal completion.
                self._check(False, 'Process32NextW')
            return names
        finally:
            self.close_handle(snapshot)

    def requests(self):
        # Absolute executable and no shell; never log raw output (may name jobs).
        executable = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'powercfg.exe')
        result = subprocess.run([executable, '/requests'], capture_output=True,
                                timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        raw = result.stdout
        if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            output = raw.decode('utf-16', errors='replace')
        elif b'\x00' in raw[:100]:
            output = raw.decode('utf-16-le', errors='replace')
        else:
            output = raw.decode('oem', errors='replace')
        return result.returncode, output

    def diagnostics(self):
        status = PowerStatus()
        caps = PowerCapabilities()
        self._check(self.kernel.GetSystemPowerStatus(C.byref(status)), 'GetSystemPowerStatus')
        self._check(self.powr.GetPwrCapabilities(C.byref(caps)), 'GetPwrCapabilities')
        return {'suspend_allowed': bool(self.powr.IsPwrSuspendAllowed()),
                'modern_standby': bool(caps.aoac),
                'traditional_sleep': bool(caps.s1 or caps.s2 or caps.s3),
                'ac_power': {0: False, 1: True}.get(status.ac),
                'battery_present': bool(caps.batteries)}

    def suspend(self):
        self._with_shutdown_privilege(
            lambda: self._check(self.powr.SetSuspendState(False, False, False), 'SetSuspendState'))

    def shutdown(self):
        # EWX_POWEROFF only: no FORCE/FORCEIFHUNG, no shell or delayed /f implication.
        # Success means Windows accepted an asynchronous request, not shutdown completion.
        self._with_shutdown_privilege(
            lambda: self._check(self.user.ExitWindowsEx(0x08, 0x80040000), 'ExitWindowsEx'))

    def _with_shutdown_privilege(self, operation):
        # Temporarily enable only SeShutdownPrivilege, then restore its exact
        # prior state on success, error, and return from sleep. No UAC elevation.
        token = HANDLE()
        self._check(self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(),
                    0x20 | 0x08, C.byref(token)), 'OpenProcessToken')
        previous = TokenPrivileges()
        changed = False
        try:
            luid = Luid()
            self._check(self.advapi.LookupPrivilegeValueW(None, 'SeShutdownPrivilege', C.byref(luid)),
                        'LookupPrivilegeValueW')
            wanted = TokenPrivileges(1, (LuidAttributes * 1)(LuidAttributes(luid, 2)))
            length = DWORD()
            C.set_last_error(0)
            adjusted = self.advapi.AdjustTokenPrivileges(token, False, C.byref(wanted),
                        C.sizeof(previous), C.byref(previous), C.byref(length))
            error = C.get_last_error()
            self._check(adjusted, 'AdjustTokenPrivileges')
            changed = True
            if error:
                raise OSError(error, 'SeShutdownPrivilege could not be enabled')
            operation()
        finally:
            try:
                if changed:
                    C.set_last_error(0)
                    self._check(self.advapi.AdjustTokenPrivileges(token, False,
                                C.byref(previous), 0, None, None), 'RestoreTokenPrivileges')
                    if C.get_last_error():
                        raise OSError(C.get_last_error(), 'RestoreTokenPrivileges failed')
            finally:
                self.close_handle(token)


class PowerActionCancelled(RuntimeError):
    """Preflight cancelled before any native power transition was attempted."""


class WindowsPower:
    """Process-owned request handle, safe to use across the app's threads.

    Caller clears its own request before asking for external blockers/suspend.
    The native sleep call rechecks blockers, but no API makes that inventory and
    SetSuspendState atomic. The controller must also recheck jobs and input.
    """
    def __init__(self, dry_run: bool = False, *, _native=None):
        self.dry_run = dry_run
        self.dry_run_requests = 0
        self.required = False
        self._handle = None
        self._closed = False
        self._lock = threading.RLock()
        self._native = _native if _native is not None else _NativePower()

    def set_required(self, required: bool) -> None:
        with self._lock:
            if self._closed:
                if required:
                    raise RuntimeError('Power adapter is closed')
                return
            if required == self.required:
                return
            if not self.dry_run:
                if required:
                    if self._handle is None:
                        self._handle = self._native.create_request()
                    try:
                        self._native.set_request(self._handle)
                    except Exception:
                        self._native.close_handle(self._handle)
                        self._handle = None
                        raise
                else:
                    self._native.clear_request(self._handle)
            self.required = required

    def process_names(self) -> set[str]:
        """Read executable basenames only; failure raises instead of empty success."""
        return self._native.process_names()

    def input_idle_seconds(self) -> float:
        return idle_seconds(*self._native.input_ticks())

    def external_blockers(self) -> list[str]:
        with self._lock:
            if self.required and not self.dry_run:
                return ['power_requests_unknown:own_request_active']
            try:
                code, output = self._native.requests()
                if code:
                    return ['power_requests_unknown:query_failed_admin_may_be_required']
                return parse_requests(output)
            except (OSError, subprocess.SubprocessError, UnicodeError):
                return ['power_requests_unknown:query_unavailable']

    def diagnostics(self) -> dict:
        result = self._native.diagnostics()
        result.update(dry_run=self.dry_run, request_active=self.required,
                      input_scope='current_session_including_injected_input')
        result['protection_limited'] = bool(result.get('modern_standby') and result.get('ac_power') is not True)
        return result

    def suspend(self, before_suspend: Callable[[], bool] | None = None) -> None:
        with self._lock:
            if self._closed or self.required:
                raise RuntimeError('Clear the active request before attempting sleep')
            initial_input = self._native.input_ticks()[1]
            if not self.diagnostics().get('suspend_allowed'):
                raise RuntimeError('Windows reports sleep unsupported or disallowed')
            blockers = self.external_blockers()
            if blockers:
                raise RuntimeError('Sleep deferred: ' + ', '.join(blockers))
            if before_suspend is not None and not before_suspend():
                raise RuntimeError('Sleep deferred: job or management state changed')
            if self._native.input_ticks()[1] != initial_input:
                raise RuntimeError('Sleep deferred: new user input during preflight')
            if self.required or self._closed:
                raise RuntimeError('Sleep deferred: management state changed')
            if self.dry_run:
                self.dry_run_requests += 1
            else:
                self._native.suspend()

    def shutdown(self, before_shutdown: Callable[[], bool] | None = None) -> None:
        with self._lock:
            if self._closed or self.required:
                raise RuntimeError('Clear the active request before attempting shutdown')
            initial_input = self._native.input_ticks()[1]
            blockers = self.external_blockers()
            if blockers:
                raise RuntimeError('Shutdown deferred: ' + ', '.join(blockers))
            if before_shutdown is not None and not before_shutdown():
                raise PowerActionCancelled('Shutdown deferred: job or management state changed')
            if self._native.input_ticks()[1] != initial_input:
                raise PowerActionCancelled('Shutdown deferred: new user input during preflight')
            if self.required or self._closed:
                raise PowerActionCancelled('Shutdown deferred: management state changed')
            if self.dry_run:
                self.dry_run_requests += 1
            else:
                self._native.shutdown()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self.set_required(False)
            finally:
                try:
                    if self._handle is not None:
                        self._native.close_handle(self._handle)
                finally:
                    self._handle = None
                    self.required = False
                    self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
