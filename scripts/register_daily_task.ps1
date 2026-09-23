param([switch]$Install)
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'daily.ps1'
$taskName = 'InvestmentLab-DailyUpdate'
$taskArguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $scriptPath + '"'
if (-not $Install) { Write-Output "预览：任务 $taskName，每天 09:30 和 22:30 补数，漏过后尽快执行；当前未注册。使用 -Install 注册。"; exit 0 }
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw '已有同名任务，先检查，不覆盖' }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArguments
$triggerMorning = New-ScheduledTaskTrigger -Daily -At '09:30'
$triggerNight = New-ScheduledTaskTrigger -Daily -At '22:30'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($triggerMorning,$triggerNight) -Settings $settings -Description '本机投资研究数据补齐；按各市场日历和检查点决定范围'
