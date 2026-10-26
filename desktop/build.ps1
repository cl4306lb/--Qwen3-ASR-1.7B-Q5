$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$builder = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $builder)) {
    & python -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 or newer is required' }
}
& $builder -m pip install -r (Join-Path $PSScriptRoot 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed' }
& $builder -m pip install --no-deps -e $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Local package installation failed' }
& $builder -m PyInstaller --noconfirm --windowed --onedir --name SpeechDesk `
    --distpath $projectRoot --workpath (Join-Path $PSScriptRoot 'build') `
    --specpath $PSScriptRoot --icon (Join-Path $projectRoot 'CapsWriter-Offline\assets\icon.ico') `
    --collect-data sounddevice --collect-binaries _sounddevice_data `
    (Join-Path $PSScriptRoot 'run_desktop.py')
if ($LASTEXITCODE -ne 0) { throw 'Application build failed' }
