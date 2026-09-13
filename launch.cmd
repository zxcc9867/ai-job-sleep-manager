@echo off
cd /d "%~dp0"
if exist "dist\AIJobSleepManager\AIJobSleepManager.exe" (
    start "" "dist\AIJobSleepManager\AIJobSleepManager.exe" %*
) else (
    start "" pythonw.exe "%~dp0app.py" %*
)

