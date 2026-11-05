# Two entry points share one bundle: GUI without console; hook with piped stdin.
a = Analysis(['app.py'], pathex=[], binaries=[], datas=[('sleep_manager/assets/app-icon.png','sleep_manager/assets'),('sleep_manager/assets/app-icon-header.png','sleep_manager/assets'),('sleep_manager/assets/app-icon.ico','sleep_manager/assets')], hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['PIL','pytest','numpy','pandas'], noarchive=False)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AIJobSleepManager', icon='sleep_manager/assets/app-icon.ico', debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False, disable_windowed_traceback=False)
hook = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AIJobSleepHook', icon='sleep_manager/assets/app-icon.ico', debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(gui, hook, a.binaries, a.datas, strip=False, upx=False, name='AIJobSleepManager')

