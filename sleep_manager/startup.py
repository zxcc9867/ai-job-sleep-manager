"""Per-user logon launch. Windows does not run this before sign-in."""
import os
from pathlib import Path
import subprocess
import sys

RUN_KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME='AIJobSleepManager'


def launch_command():
    executable=Path(sys.executable)
    if getattr(sys,'frozen',False):
        parts=[str(executable)]
    else:
        pythonw=executable.with_name('pythonw.exe')
        parts=[str(pythonw if pythonw.exists() else executable),
               str(Path(__file__).resolve().parents[1]/'app.py')]
    return subprocess.list2cmdline(parts)


def registered_command():
    if os.name!='nt': return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY,0,winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key,VALUE_NAME)[0]
    except FileNotFoundError:
        return None


def is_enabled():
    return registered_command()==launch_command()


def set_enabled(enabled):
    if os.name!='nt': raise OSError('Windows에서만 사용할 수 있습니다.')
    import winreg
    desired=launch_command()
    previous=registered_command()
    if previous is not None and previous!=desired:
        raise RuntimeError('이름이 같은 다른 자동 실행 항목이 있습니다. 기존 설정을 덮어쓰지 않았습니다.')
    if enabled and previous==desired: return
    if not enabled and previous is None: return
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,RUN_KEY,0,winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key,VALUE_NAME,0,winreg.REG_SZ,desired)
        else:
            winreg.DeleteValue(key,VALUE_NAME)
