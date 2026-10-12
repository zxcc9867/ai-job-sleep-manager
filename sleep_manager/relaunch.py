"""User-initiated Windows elevation; never change power or management settings."""
import base64
import ctypes
import os
from pathlib import Path
import sys


def is_administrator():
    return bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name == 'nt' else False


def _shell_execute(*args):
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                    ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    shell.ShellExecuteW.restype = ctypes.c_void_p
    return shell.ShellExecuteW(*args)


def request_admin_relaunch(data_dir, *, activate=False):
    executable = sys.executable
    argv = [] if getattr(sys, 'frozen', False) else [str(Path(__file__).resolve().parents[1]/'app.py')]
    argv += ['--data-dir', str(Path(data_dir).resolve())]
    if activate:
        argv.append('--activate-power-control')
    # Literal PowerShell arguments preserve quotes/spaces without interpolation.
    quoted = ' '.join("'"+value.replace("'", "''")+"'" for value in [executable, *argv])
    script = ("$ErrorActionPreference='Stop';"
              "$handoffDeadline=[DateTime]::UtcNow.AddSeconds(20);"
              f"while (Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue) {{"
              "if ([DateTime]::UtcNow -gt $handoffDeadline) {throw 'App handoff timed out'};"
              "Start-Sleep -Milliseconds 200}; "
              f"& {quoted}")
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    result = _shell_execute(None, 'runas', 'powershell.exe',
                            '-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand '+encoded,
                            None, 0)
    return bool(result and result > 32)
