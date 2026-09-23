param([string]$DataDirectory = '')
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
if ($DataDirectory) { & $pythonExecutable -m investment_lab.cli --data $DataDirectory update }
else { & $pythonExecutable -m investment_lab.cli update }
$updateExitCode = $LASTEXITCODE
if ($DataDirectory) { & $pythonExecutable -m investment_lab.cli --data $DataDirectory daily-backup }
else { & $pythonExecutable -m investment_lab.cli daily-backup }
if ($updateExitCode -ne 0) { exit $updateExitCode }
exit $LASTEXITCODE
