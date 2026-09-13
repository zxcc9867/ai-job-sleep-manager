$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$buildPython = Join-Path $PSScriptRoot '.build-env\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    python -m venv .build-env
    if ($LASTEXITCODE -ne 0) { throw 'Build environment creation failed' }
}
& $buildPython -m pip install --disable-pip-version-check -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed' }
& $buildPython -m PyInstaller --noconfirm AIJobSleepManager.spec
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
Write-Output (Join-Path $PSScriptRoot 'dist\AIJobSleepManager\AIJobSleepManager.exe')

