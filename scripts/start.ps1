param([int]$Port = 8765, [string]$DataDirectory = '')
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw '请先运行 scripts\install.ps1' }
$env:PYTHONUTF8 = '1'
if ($DataDirectory) { & $pythonExecutable -m investment_lab.cli --data $DataDirectory serve --port $Port }
else { & $pythonExecutable -m investment_lab.cli serve --port $Port }
