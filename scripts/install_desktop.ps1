param([int]$Port = 8765, [string]$DataDirectory = '')
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\pythonw.exe'
$launcher = Join-Path $PSScriptRoot 'desktop_app.py'
$icon = Join-Path $PSScriptRoot 'assets\investment-lab.ico'
foreach ($required in @($pythonExecutable, $launcher, $icon)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "缺少文件：$required" }
}
if ($Port -lt 1 -or $Port -gt 65535) { throw '端口必须在1至65535之间' }
if (-not $DataDirectory) { $DataDirectory = Join-Path (Split-Path -Parent $projectDirectory) 'investment_lab_data' }
$DataDirectory = [IO.Path]::GetFullPath($DataDirectory)
$desktopDirectory = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopDirectory '投资研究室.lnk'
$shell = New-Object -ComObject WScript.Shell
$arguments = '"{0}" --port {1} --data "{2}"' -f $launcher, $Port, $DataDirectory
if (Test-Path -LiteralPath $shortcutPath) {
    $existing = $shell.CreateShortcut($shortcutPath)
    if ($existing.TargetPath -ne $pythonExecutable -or $existing.Arguments -ne $arguments) {
        throw "桌面已存在同名入口，未覆盖：$shortcutPath"
    }
}
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonExecutable
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $projectDirectory
$shortcut.Description = '自动启动本机投资研究室并打开独立应用窗口'
$shortcut.IconLocation = "$icon,0"
$shortcut.WindowStyle = 1
$shortcut.Save()
[pscustomobject]@{Shortcut=$shortcutPath; Target=$shortcut.TargetPath; Arguments=$shortcut.Arguments; Icon=$shortcut.IconLocation} | ConvertTo-Json
