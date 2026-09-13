# Two entry points share one bundle: GUI without console; hook with piped stdin.
a = Analysis(['app.py'], pathex=[], binaries=[], datas=[], hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['PIL','pytest','numpy','pandas'], noarchive=False)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AIJobSleepManager', debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False, disable_windowed_traceback=False)
hook = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AIJobSleepHook', debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(gui, hook, a.binaries, a.datas, strip=False, upx=False, name='AIJobSleepManager')

