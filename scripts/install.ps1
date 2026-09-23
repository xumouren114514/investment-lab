$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$venvDirectory = Join-Path $projectDirectory '.venv'
if (-not (Test-Path -LiteralPath $venvDirectory)) { py -3.12 -m venv $venvDirectory }
$pythonExecutable = Join-Path $venvDirectory 'Scripts\python.exe'
& $pythonExecutable -m pip install -r (Join-Path $projectDirectory 'requirements.lock')
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
& $pythonExecutable -m pip install --no-deps -e $projectDirectory
if ($LASTEXITCODE -ne 0) { throw '项目安装失败' }
$env:PYTHONUTF8 = '1'
& $pythonExecutable -m investment_lab.cli init --demo
