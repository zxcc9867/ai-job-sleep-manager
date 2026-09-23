"""Read-only session connection evidence. Never establishes job completion."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time


def windows_process(pid):
    if os.name!='nt':return None
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return None
    try:
        code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259:return None
        creation,exit_time,kernel_time,user_time=(wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle,*[ctypes.byref(x) for x in (creation,exit_time,kernel_time,user_time)]):return None
        size=wintypes.DWORD(32768);name=ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle,0,name,ctypes.byref(size)):return None
        return Path(name.value).name.lower(),str((creation.dwHighDateTime<<32)|creation.dwLowDateTime)
    finally:kernel.CloseHandle(handle)


class ClaudePresence:
    def __init__(self,root,probe=windows_process,clock=time.monotonic):
        self.root=Path(root).resolve();self.probe=probe;self.clock=clock
        self.next_read=0;self.cache={};self.last_error=''

    def lookup(self,force=False):
        if not force and self.clock()<self.next_read:return self.cache
        self.next_read=self.clock()+5;result={};self.last_error=''
        try:
            for index,path in enumerate(self.root.glob('*.json')):
                if index>=512:break
                try:
                    if not path.resolve().is_relative_to(self.root) or path.stat().st_size>65536:continue
                    with path.open('rb') as f:raw=f.read(65537)
                    if len(raw)>65536:continue
                    data=json.loads(raw)
                    if not isinstance(data,dict):continue
                    pid=data.get('pid');sid=data.get('sessionId');start=data.get('procStart')
                    if type(pid)!=int or not 0<pid<2**32 or path.stem!=str(pid):continue
                    if not isinstance(sid,str) or not 0<len(sid)<=256:continue
                    if not isinstance(start,str) or not start.isdigit():continue
                    actual=self.probe(pid)
                    if actual and actual[0] in ('claude.exe','node.exe') and actual[1]==start:
                        result[sid]=f'PID {pid} · 프로세스 시작 시각 일치'
                except (OSError,ValueError,RecursionError):
                    self.last_error='일부 세션의 연결을 확인하지 못했습니다.'
        except OSError:self.last_error='Claude 세션 목록을 읽을 수 없습니다.'
        self.cache=result
        return result
