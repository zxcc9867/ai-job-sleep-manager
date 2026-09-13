"""AI Job Sleep Manager — GUI and metadata-only hook entry point."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from sleep_manager.storage import Store, data_directory

def launch_prefix():
    if getattr(sys,'frozen',False):
        return [str(Path(sys.executable).with_name('AIJobSleepHook.exe'))]
    return [sys.executable.replace('pythonw.exe','python.exe'),str(Path(__file__).resolve())]

def hook_main(provider,root):
    # No stdout: observer hooks must never inject instructions or decisions.
    from sleep_manager.integrations import normalize_hook
    try:
        if sys.stdin is None:
            return 0
        raw=sys.stdin.read(1024*1024+1)
        if len(raw)>1024*1024:
            return 0
        event=normalize_hook(provider,json.loads(raw))
        if event:
            Store(root).append(event)
    except Exception:
        # A broken observer must not block development or reveal hook payloads.
        return 0
    return 0

class Instance:
    def __init__(self,root):
        self.handle=None
        if os.name=='nt':
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p]
            kernel.CreateMutexW.restype=ctypes.c_void_p
            self.kernel=kernel
            name='Local\\AIJobSleepManager-'+hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:20]
            self.handle=kernel.CreateMutexW(None,False,name)
            if not self.handle:
                raise OSError('앱 실행 잠금을 만들 수 없습니다.')
            if ctypes.get_last_error()==183:
                self.close()
                raise RuntimeError('앱이 이미 실행 중입니다. 작업 표시줄의 창을 확인하세요.')
    def close(self):
        if self.handle:
            self.kernel.CloseHandle.argtypes=[ctypes.c_void_p]
            self.kernel.CloseHandle(self.handle)
            self.handle=None

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--hook',choices=['codex','claude'])
    parser.add_argument('--data-dir',type=Path,default=data_directory())
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--diagnose',action='store_true')
    parser.add_argument('--observe-seconds',type=int,default=0)
    parser.add_argument('--smoke-ui',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--screenshot',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.hook:
        return hook_main(args.hook,args.data_dir)
    from sleep_manager.power import WindowsPower
    if args.diagnose:
        with WindowsPower(dry_run=True) as power:
            result=power.diagnostics()
            result['external_blockers']=power.external_blockers()
            result['python_version']=sys.version.split()[0]
            print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    instance=Instance(args.data_dir)
    from sleep_manager.codex_logs import CodexLogs
    from sleep_manager.controller import Controller
    power=WindowsPower(dry_run=args.dry_run or args.smoke_ui)
    store=Store(args.data_dir)
    reader=CodexLogs(Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'sessions')
    controller=Controller(store,power,reader)
    try:
        if args.observe_seconds:
            until=time.monotonic()+max(1,min(args.observe_seconds,60))
            while time.monotonic()<until:
                status=controller.tick()
                print(json.dumps(dict(mode=status.mode,running=status.running,waiting=status.waiting,unknown=status.unknown,error=controller.error),ensure_ascii=False))
                time.sleep(1)
            return 0
        from sleep_manager.ui import Window
        window=Window(controller,args,launch_prefix()+['--data-dir',str(args.data_dir.resolve())])
        window.run()
        return 0
    finally:
        controller.close()
        instance.close()

if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        if '--hook' in sys.argv:
            raise SystemExit(0)
        if sys.stderr:
            print(str(exc),file=sys.stderr)
        if os.name=='nt':
            ctypes.windll.user32.MessageBoxW(None,str(exc),'AI Job Sleep Manager',0x10)
        raise SystemExit(1)


