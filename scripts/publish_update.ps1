[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Message,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string[]]$Paths,
    [string[]]$Tests = @(),
    [string]$Remote = 'origin'
)

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'

function Invoke-Git {
    param([string[]]$GitArguments)
    $output = & git -C $projectDirectory @GitArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git 命令失败：git $($GitArguments[0])。若已创建本地提交，脚本不会回滚它。"
    }
    return $output
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw '找不到 Git。' }
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) { throw "找不到项目 Python：$pythonExecutable" }

$branch = (Invoke-Git @('branch', '--show-current') | Select-Object -First 1).Trim()
if (-not $branch) { throw '当前处于 detached HEAD；请切换到要发布的分支。' }
$remoteNames = @(Invoke-Git @('remote'))
$hasRemote = $Remote -in $remoteNames
$remoteUrl = if ($hasRemote) { (Invoke-Git @('remote', 'get-url', $Remote) | Select-Object -First 1).Trim() } else { '' }
$validRemote = $hasRemote -and $remoteUrl -match '(?i)^(https://github\.com/[^/\s]+/[^/\s]+(?:\.git)?|git@github\.com:[^/\s]+/[^/\s]+(?:\.git)?)$'
if ($validRemote) {
    $publicMarkerPath = Join-Path $projectDirectory '.public-history-baseline'
    if (-not (Test-Path -LiteralPath $publicMarkerPath -PathType Leaf)) {
        throw '当前分支未标记为经审查的公开基线；拒绝推送。'
    }
    $rootCommits = @(Invoke-Git @('rev-list', '--max-parents=0', 'HEAD') | Where-Object { $_ })
    if ($rootCommits.Count -ne 1) { throw '公开分支必须从单一、独立的干净根提交开始。' }
    $rootMarkerRef = '{0}:.public-history-baseline' -f $rootCommits[0]
    $committedMarker = (Invoke-Git @('show', $rootMarkerRef) | Out-String).Trim()
    if ($committedMarker -ne 'investment-lab-public-baseline-v1') {
        throw '公开基线标记不在根提交或内容不匹配；拒绝推送。'
    }
    $privateHistoryPathspecs = @(
        'TASK_STATE.md', 'docs/changes/', ':(glob)docs/*-verification.json',
        ':(glob)docs/*-delivery.json', 'docs/data_coverage.json', 'docs/ACCEPTANCE.md',
        'docs/OPEN_RESOURCE_COVERAGE.md', 'docs/STRATEGY_ASSISTANT.md',
        'docs/SPEC.md', 'config/universe.yaml'
    )
    $privateHistoryPaths = @(Invoke-Git (@('log', '--format=', '--name-only', 'HEAD', '--') + $privateHistoryPathspecs) |
        Where-Object { $_ } | Sort-Object -Unique)
    if ($privateHistoryPaths.Count -gt 0) {
        throw '当前分支历史含本机状态、研究证据或个人交接记录；为防止泄露，拒绝连接 GitHub。请先保留本地历史并准备隐私清理后的初始公开提交。'
    }
}

$staged = @(Invoke-Git @('diff', '--cached', '--name-only'))
if ($staged.Count -gt 0) { throw '暂存区已有内容。请先检查并提交它们，再运行发布脚本。' }

$pathsToPublish = @($Paths | ForEach-Object {
    $path = $_.Replace('\', '/')
    if ([IO.Path]::IsPathRooted($_) -or $path -match '(^|/)\.\.(/|$)' -or $path -in @('', '.', './') -or $path -match '[*?]') {
        throw "只接受仓库内明确的文件路径：$_"
    }
    if ($path -match '(?i)^TASK_STATE\.md$|^docs/changes/|^docs/[^/]*(verification|delivery)\.json$|^docs/(data_coverage\.json|ACCEPTANCE\.md|OPEN_RESOURCE_COVERAGE\.md|STRATEGY_ASSISTANT\.md|SPEC\.md)$|^config/universe\.yaml$') {
        throw "拒绝发布本机任务状态、研究记录或私有验证材料：$_"
    }
    if ($path -match '(?i)^(investment_lab_data|investment_lab_backups|data|local_config|user_strategies|runs|logs|secrets?)(/|$)' -or
        $path -match '(?i)(^|/)(investment_lab_data|investment_lab_backups|local_config|user_strategies|runs|logs|secrets?)(/|$)' -or
        $path -match '(?i)(^|/)\.env($|\.)|(^|/)config/providers\.json$|\.(sqlite(?:\d+)?|db|log)(\.|$)') {
        throw "拒绝发布运行数据、私人配置或日志路径：$_"
    }
    $path
})
$testsToRun = @($Tests | ForEach-Object {
    if ([IO.Path]::IsPathRooted($_) -or $_ -match '(^|[/\\])\.\.([/\\]|$)' -or $_.StartsWith('-')) {
        throw "测试路径必须是仓库内的 pytest 文件或节点：$_"
    }
    $testFile = ($_ -split '::', 2)[0]
    if (-not (Test-Path -LiteralPath (Join-Path $projectDirectory $testFile) -PathType Leaf)) {
        throw "测试文件不存在：$_"
    }
    $_
})

$codePaths = @($pathsToPublish | Where-Object { $_ -match '(?i)\.(py|js)$' })
if ($codePaths.Count -gt 0 -and $testsToRun.Count -eq 0) {
    throw '代码变更必须指定受影响的少量测试；不要以全量测试代替针对性测试。'
}
$powershellPaths = @($pathsToPublish | Where-Object { $_ -match '(?i)\.ps1$' })
foreach ($path in $powershellPaths) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $projectDirectory $path), [ref]$tokens, [ref]$parseErrors) | Out-Null
    if ($parseErrors.Count -gt 0) { throw "PowerShell 语法检查失败：$path ($($parseErrors[0].Message))" }
}

Push-Location $projectDirectory
try {
    if ($pathsToPublish | Where-Object { $_ -match '(?i)\.py$' }) {
        & $pythonExecutable -m compileall -q src scripts/desktop_app.py
        if ($LASTEXITCODE -ne 0) { throw 'Python 语法检查失败。' }
    }
    if ($codePaths.Count -gt 0 -and $testsToRun.Count -gt 0) {
        & $pythonExecutable -m pytest -q @testsToRun
        if ($LASTEXITCODE -ne 0) { throw '指定的针对性 pytest 失败。' }
    }
    $jsPaths = @($pathsToPublish | Where-Object { $_ -match '(?i)\.js$' })
    if ($jsPaths.Count -gt 0) {
        if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'JavaScript 有改动但找不到 Node.js，无法执行语法检查。' }
        foreach ($path in $jsPaths) {
            & node --check (Join-Path $projectDirectory $path)
            if ($LASTEXITCODE -ne 0) { throw "JavaScript 语法检查失败：$path" }
        }
    }

    Invoke-Git (@('diff', '--check', '--') + $pathsToPublish) | Out-Null

    $shortcutInstaller = Join-Path $PSScriptRoot 'install_desktop.ps1'
    if ($PSCmdlet.ShouldProcess('桌面\投资研究室.lnk', '刷新快捷方式')) {
        & $shortcutInstaller
        if ($LASTEXITCODE -ne 0) { throw '更新桌面快捷方式失败，未提交或推送。' }
    } else {
        return
    }

    if (-not $PSCmdlet.ShouldProcess("Git 暂存区：$($pathsToPublish -join ', ')", '暂存指定文件')) { return }
    Invoke-Git (@('add', '-A', '--') + $pathsToPublish) | Out-Null
    $staged = @(Invoke-Git @('diff', '--cached', '--name-only'))
    if ($staged.Count -eq 0) { throw '指定路径没有可提交的改动。' }
    Invoke-Git @('diff', '--cached', '--check') | Out-Null

    if (-not $PSCmdlet.ShouldProcess("$Remote/$branch", "提交：$Message")) { return }
    Invoke-Git @('commit', '-m', $Message) | Out-Null
    $commit = (Invoke-Git @('rev-parse', '--short', 'HEAD') | Select-Object -First 1).Trim()

    if (-not $hasRemote) {
        throw "本地提交 $commit 已保留，但没有名为 $Remote 的远程，因此未上传。配置 GitHub 远程后再普通推送。"
    }
    if (-not $validRemote) {
        throw "本地提交 $commit 已保留，但远程不是不含凭据的 GitHub HTTPS/SSH URL，因此未上传。"
    }
    if (-not $PSCmdlet.ShouldProcess("$Remote/$branch", '推送 GitHub')) { return }
    try {
        Invoke-Git @('push', '--set-upstream', $Remote, $branch) | Out-Null
    } catch {
        throw "本地提交 $commit 已保留，但没有成功上传。修复远程或认证后重试普通 git push；没有执行强制推送。原因：$($_.Exception.Message)"
    }
    Write-Output "已刷新桌面快捷方式，提交 $commit 已推送到 $Remote/$branch。"
} finally {
    Pop-Location
}
