param(
    [string]$PythonPath = '',
    [switch]$SkipUnitTests
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$RepositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$RunScript = Join-Path $RepositoryRoot 'scripts\run.ps1'
$VirtualPython = Join-Path $RepositoryRoot '.venv\Scripts\python.exe'
$Python = if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
    (Resolve-Path -LiteralPath $PythonPath).Path
}
elseif (Test-Path -LiteralPath $VirtualPython -PathType Leaf) {
        $VirtualPython
    }
    else {
        $Command = Get-Command 'python.exe' -ErrorAction SilentlyContinue
        if ($null -eq $Command) {
            throw '找不到 Python；请先运行 scripts\install-env.ps1。'
        }
        $Command.Source
    }

$TempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$TestRoot = Join-Path $TempBase ("product-evidence-guard-tests-" + [Guid]::NewGuid().ToString('N'))
$ResolvedTestRoot = [System.IO.Path]::GetFullPath($TestRoot)
if (-not $ResolvedTestRoot.StartsWith(
    $TempBase,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw '临时测试目录不在系统临时目录内，已停止。'
}

$InputDirectory = Join-Path $TestRoot '中文 空格\商品资料'
$OutputDirectory = Join-Path $TestRoot '中文 空格\输出'
$StderrPath = Join-Path $TestRoot 'entry-stderr.log'
$ServerMayBeRunning = $false

function Assert-True {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )
    if (-not $Condition) {
        throw $Message
    }
}

function Invoke-Entry {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][int]$ExpectedExitCode
    )
    if (Test-Path -LiteralPath $StderrPath) {
        Remove-Item -LiteralPath $StderrPath -Force
    }
    $Output = & powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File $RunScript @Arguments 2> $StderrPath
    $ActualExitCode = $LASTEXITCODE
    if ($ActualExitCode -ne $ExpectedExitCode) {
        $ErrorText = if (Test-Path -LiteralPath $StderrPath) {
            Get-Content -LiteralPath $StderrPath -Raw
        }
        else {
            ''
        }
        throw (
            "入口退出码错误：expected=$ExpectedExitCode actual=$ActualExitCode " +
            "stderr=$ErrorText stdout=$($Output -join ' ')"
        )
    }
    $Text = ($Output -join "`n").Trim()
    if (-not $Text) {
        throw '入口没有输出稳定 JSON。'
    }
    try {
        return $Text | ConvertFrom-Json
    }
    catch {
        throw "入口输出不是合法 JSON：$Text"
    }
}

New-Item -ItemType Directory -Force -Path $InputDirectory, $OutputDirectory | Out-Null
foreach ($Sample in Get-ChildItem -LiteralPath (Join-Path $RepositoryRoot 'samples\demo') -Force) {
    Copy-Item -LiteralPath $Sample.FullName -Destination $InputDirectory -Recurse -Force
}
$env:PYTHONPYCACHEPREFIX = Join-Path $TestRoot 'pycache'
$LocationPushed = $false

try {
    Push-Location -LiteralPath $RepositoryRoot
    $LocationPushed = $true
    if (-not $SkipUnitTests) {
        & $Python -B -m unittest discover -s (Join-Path $RepositoryRoot 'tests') -v
        Assert-True ($LASTEXITCODE -eq 0) 'Python 单元测试失败。'
    }

    & $Python -B -m compileall -q `
        (Join-Path $RepositoryRoot 'product_evidence_guard') `
        (Join-Path $RepositoryRoot 'scripts')
    Assert-True ($LASTEXITCODE -eq 0) 'compileall 失败。'

    $HelpOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File $RunScript --help
    Assert-True ($LASTEXITCODE -eq 0) 'run.ps1 --help 失败。'
    Assert-True (($HelpOutput -join "`n").Contains('--continue')) `
        '帮助信息没有 --continue。'

    $Analyze = Invoke-Entry -Arguments @(
        'analyze',
        $InputDirectory,
        '--output',
        $OutputDirectory,
        '--deterministic-only'
    ) -ExpectedExitCode 0
    $ServerMayBeRunning = $true
    Assert-True ([bool]$Analyze.ok) '中文空格路径分析未成功。'
    Assert-True ($Analyze.operation -eq 'analyze') '分析响应 operation 错误。'

    foreach ($Name in @(
        'product-facts.json',
        'conflicts.md',
        'evidence-report.html',
        'run-summary.json',
        'analysis-state.json',
        'confirmed-product-facts.json',
        'confirmation-audit.jsonl'
    )) {
        Assert-True (Test-Path -LiteralPath (Join-Path $OutputDirectory $Name) -PathType Leaf) `
            "缺少分析输出：$Name"
    }
    $Facts = Get-Content -LiteralPath (Join-Path $OutputDirectory 'product-facts.json') `
        -Raw -Encoding utf8 | ConvertFrom-Json
    Assert-True ($Facts.status -eq 'pending_human_confirmation') `
        '模型候选被错误地自动确认为正式事实。'
    Assert-True ($Facts.candidates.Count -gt 0) '确定性冒烟测试没有候选。'

    $SecondAnalyze = Invoke-Entry -Arguments @(
        'analyze',
        $InputDirectory,
        '--output',
        $OutputDirectory,
        '--deterministic-only'
    ) -ExpectedExitCode 0
    Assert-True ([bool]$SecondAnalyze.ok) '第二次增量分析失败。'
    $Summary = Get-Content -LiteralPath (Join-Path $OutputDirectory 'run-summary.json') `
        -Raw -Encoding utf8 | ConvertFrom-Json
    Assert-True ($Summary.unchanged_files_reused.Count -gt 0) `
        '第二次分析没有复用未修改文件。'

    $Status = Invoke-Entry -Arguments @('status') -ExpectedExitCode 0
    Assert-True ([bool]$Status.ok) '服务 status 失败。'
    Assert-True ($Status.status -eq 'running') '服务没有保持 running。'

    $Missing = Join-Path $TestRoot '不存在 中文 空格'
    $Invalid = Invoke-Entry -Arguments @(
        'analyze',
        $Missing,
        '--deterministic-only'
    ) -ExpectedExitCode 1
    Assert-True (-not [bool]$Invalid.ok) '错误路径不应成功。'
    Assert-True ($Invalid.error.code -eq 'invalid_arguments') `
        '错误路径没有稳定参数错误码。'

    $Shutdown = Invoke-Entry -Arguments @('shutdown') -ExpectedExitCode 0
    Assert-True ($Shutdown.status -eq 'shutdown') 'shutdown 未成功。'
    $ServerMayBeRunning = $false

    $Result = [ordered]@{
        schema_version = 1
        status = 'passed'
        utf8_space_path = '中文 空格'
        unit_tests = if ($SkipUnitTests) { 'skipped_by_flag' } else { 'passed' }
        python = $Python
        compileall = 'passed'
        deterministic_smoke = 'passed'
        incremental_reuse = 'passed'
        invalid_path_exit_code = 1
        named_pipe_status = 'passed'
        shutdown = 'passed'
    }
    [Console]::Out.WriteLine(($Result | ConvertTo-Json -Compress))
    exit 0
}
catch {
    $Failure = [ordered]@{
        schema_version = 1
        status = 'failed'
        error = $_.Exception.Message
    }
    [Console]::Out.WriteLine(($Failure | ConvertTo-Json -Compress))
    exit 1
}
finally {
    if ($LocationPushed) {
        Pop-Location
    }
    if ($ServerMayBeRunning) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass `
            -File $RunScript shutdown *> $null
    }
    if (Test-Path -LiteralPath $ResolvedTestRoot -PathType Container) {
        $ResolvedBeforeDelete = [System.IO.Path]::GetFullPath(
            (Resolve-Path -LiteralPath $ResolvedTestRoot).Path
        )
        if (-not $ResolvedBeforeDelete.StartsWith(
            $TempBase,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw '拒绝删除不在系统临时目录内的测试目录。'
        }
        Remove-Item -LiteralPath $ResolvedBeforeDelete -Recurse -Force
    }
}
