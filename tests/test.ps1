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
        'visual-transcription.json',
        'document-visuals.json',
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
    Assert-True ($Facts.run_summary.blocking_conflict_count -eq 2) `
        '确定性冒烟测试应包含两个强冲突。'
    Assert-True (-not [bool]$Facts.run_summary.local_ai.fact_extractor) `
        'deterministic-only 不应加载本地事实提取模型。'
    Assert-True (-not [bool]$Facts.run_summary.local_ai.image_reader) `
        'deterministic-only 不应加载本地图像模型。'
    Assert-True ($Facts.run_summary.model_load_seconds -eq 0) `
        'deterministic-only 的模型加载时间应为零。'

    $SessionId = [string]$Facts.run_summary.session_id
    Assert-True (-not [string]::IsNullOrWhiteSpace($SessionId)) `
        '分析结果缺少 session_id。'
    $ConfirmCandidates = @($Facts.candidates | Where-Object {
        $_.field -eq 'net_weight' -and
        $_.source_file -eq '说明书.txt' -and
        $_.raw_value -eq '320g'
    })
    $RejectCandidates = @($Facts.candidates | Where-Object {
        $_.field -eq 'net_weight' -and
        $_.source_file -eq '包装正面.jpg.ocr.json' -and
        $_.raw_value -eq '300g'
    })
    Assert-True ($ConfirmCandidates.Count -eq 1) `
        '没有唯一找到说明书 320g 确认候选。'
    Assert-True ($RejectCandidates.Count -eq 1) `
        '没有唯一找到包装 sidecar 300g 拒绝候选。'
    $ConfirmCandidate = $ConfirmCandidates[0]
    $RejectCandidate = $RejectCandidates[0]

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

    $ConfirmReason = '已核对当前说明书版本'
    $Confirm = Invoke-Entry -Arguments @(
        'confirm',
        '--output-dir',
        $OutputDirectory,
        '--session-id',
        $SessionId,
        '--candidate-id',
        [string]$ConfirmCandidate.candidate_id,
        '--reason',
        $ConfirmReason
    ) -ExpectedExitCode 0
    Assert-True ([bool]$Confirm.ok) '公开入口 confirm 未成功。'
    Assert-True ($Confirm.result.decision.status -eq 'confirmed') `
        'confirm 响应没有返回 confirmed。'
    Assert-True (
        $Confirm.result.decision.candidate_id -eq $ConfirmCandidate.candidate_id
    ) 'confirm 响应候选 ID 错误。'

    $RejectReason = '包装图属于旧版物料'
    $Reject = Invoke-Entry -Arguments @(
        'reject',
        '--output-dir',
        $OutputDirectory,
        '--session-id',
        $SessionId,
        '--candidate-id',
        [string]$RejectCandidate.candidate_id,
        '--reason',
        $RejectReason
    ) -ExpectedExitCode 0
    Assert-True ([bool]$Reject.ok) '公开入口 reject 未成功。'
    Assert-True ($Reject.result.decision.status -eq 'rejected') `
        'reject 响应没有返回 rejected。'

    $ExportBeforeChange = Invoke-Entry -Arguments @(
        'export',
        '--output-dir',
        $OutputDirectory,
        '--session-id',
        $SessionId
    ) -ExpectedExitCode 0
    Assert-True ([bool]$ExportBeforeChange.ok) '首次 export 未成功。'
    Assert-True ($ExportBeforeChange.result.export.confirmed_count -eq 1) `
        '首次 export 应只包含一个已确认事实。'
    Assert-True ($ExportBeforeChange.result.export.stale_count -eq 0) `
        '源文件变化前不应存在 stale 决定。'

    $DecisionFacts = Get-Content -LiteralPath (
        Join-Path $OutputDirectory 'product-facts.json'
    ) -Raw -Encoding utf8 | ConvertFrom-Json
    $ConfirmedCurrent = @($DecisionFacts.candidates | Where-Object {
        $_.candidate_id -eq $ConfirmCandidate.candidate_id -and
        $_.status -eq 'confirmed'
    })
    $RejectedCurrent = @($DecisionFacts.candidates | Where-Object {
        $_.candidate_id -eq $RejectCandidate.candidate_id -and
        $_.status -eq 'rejected'
    })
    Assert-True ($ConfirmedCurrent.Count -eq 1) `
        'product-facts.json 没有立即显示 confirmed。'
    Assert-True ($RejectedCurrent.Count -eq 1) `
        'product-facts.json 没有立即显示 rejected。'

    $ConfirmedFacts = Get-Content -LiteralPath (
        Join-Path $OutputDirectory 'confirmed-product-facts.json'
    ) -Raw -Encoding utf8 | ConvertFrom-Json
    Assert-True ($ConfirmedFacts.status -eq 'human_confirmed') `
        '正式事实导出状态错误。'
    Assert-True ($ConfirmedFacts.facts.Count -eq 1) `
        '正式事实导出数量错误。'
    Assert-True (
        $ConfirmedFacts.facts[0].candidate_id -eq $ConfirmCandidate.candidate_id
    ) '正式事实导出了错误候选。'
    Assert-True (
        $ConfirmedFacts.facts[0].confirmation_reason -eq $ConfirmReason
    ) '正式事实没有保留确认理由。'

    $DecisionMarkdown = Get-Content -LiteralPath (
        Join-Path $OutputDirectory 'conflicts.md'
    ) -Raw -Encoding utf8
    $DecisionHtml = Get-Content -LiteralPath (
        Join-Path $OutputDirectory 'evidence-report.html'
    ) -Raw -Encoding utf8
    Assert-True ($DecisionMarkdown.Contains('(`confirmed`)')) `
        'Markdown 报告没有显示 confirmed。'
    Assert-True ($DecisionMarkdown.Contains('(`rejected`)')) `
        'Markdown 报告没有显示 rejected。'
    Assert-True ($DecisionHtml.Contains('(confirmed)')) `
        'HTML 报告没有显示 confirmed。'
    Assert-True ($DecisionHtml.Contains('(rejected)')) `
        'HTML 报告没有显示 rejected。'
    Assert-True ($DecisionHtml.Contains('已确认 1')) `
        'HTML 报告确认计数错误。'
    Assert-True ($DecisionHtml.Contains('已拒绝 1')) `
        'HTML 报告拒绝计数错误。'

    $AuditEvents = @(
        Get-Content -LiteralPath (
            Join-Path $OutputDirectory 'confirmation-audit.jsonl'
        ) -Encoding utf8 |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-True (@($AuditEvents | Where-Object {
        $_.action -eq 'confirm' -and
        $_.candidate_id -eq $ConfirmCandidate.candidate_id
    }).Count -eq 1) '审计日志缺少 confirm 事件。'
    Assert-True (@($AuditEvents | Where-Object {
        $_.action -eq 'reject' -and
        $_.candidate_id -eq $RejectCandidate.candidate_id
    }).Count -eq 1) '审计日志缺少 reject 事件。'

    $ManualPath = Join-Path $InputDirectory '说明书.txt'
    $ManualText = Get-Content -LiteralPath $ManualPath -Raw -Encoding utf8
    $ChangedManualText = $ManualText.Replace('产品净重：320g', '产品净重：350g')
    Assert-True ($ChangedManualText -ne $ManualText) `
        '测试夹具没有找到待修改的说明书净重。'
    [System.IO.File]::WriteAllText(
        $ManualPath,
        $ChangedManualText,
        [System.Text.UTF8Encoding]::new($false)
    )

    $ChangedAnalyze = Invoke-Entry -Arguments @(
        'analyze',
        $InputDirectory,
        '--output',
        $OutputDirectory,
        '--deterministic-only'
    ) -ExpectedExitCode 0
    Assert-True ([bool]$ChangedAnalyze.ok) '源文件变化后的重新分析失败。'

    $ChangedFacts = Get-Content -LiteralPath (
        Join-Path $OutputDirectory 'product-facts.json'
    ) -Raw -Encoding utf8 | ConvertFrom-Json
    $ChangedSummary = $ChangedFacts.run_summary
    Assert-True ($ChangedSummary.session_id -eq $SessionId) `
        '同一输入和输出目录的 session_id 不应变化。'
    Assert-True ($ChangedSummary.changed_or_new_files.Count -eq 1) `
        '重新分析应只识别一个变化文件。'
    Assert-True (
        [string]$ChangedSummary.changed_or_new_files[0] -eq '说明书.txt'
    ) '重新分析没有精确识别说明书变化。'
    Assert-True ($ChangedSummary.unchanged_files_reused.Count -eq 2) `
        '重新分析应复用两个未变化文件。'
    Assert-True (@($ChangedFacts.candidates | Where-Object {
        $_.candidate_id -eq $ConfirmCandidate.candidate_id
    }).Count -eq 0) '旧 320g 候选不应继续出现在当前候选中。'
    $NewCandidates = @($ChangedFacts.candidates | Where-Object {
        $_.field -eq 'net_weight' -and
        $_.source_file -eq '说明书.txt' -and
        $_.raw_value -eq '350g' -and
        $_.status -eq 'pending'
    })
    Assert-True ($NewCandidates.Count -eq 1) `
        '新 350g 候选没有保持 pending。'
    Assert-True (@($ChangedFacts.candidates | Where-Object {
        $_.candidate_id -eq $RejectCandidate.candidate_id -and
        $_.status -eq 'rejected'
    }).Count -eq 1) '未变化的包装候选没有保持 rejected。'
    Assert-True ($ChangedSummary.confirmation_status_counts.confirmed -eq 0) `
        '源文件变化后不应保留 confirmed。'
    Assert-True ($ChangedSummary.confirmation_status_counts.rejected -eq 1) `
        '源文件变化后应保留一个 rejected。'
    Assert-True ($ChangedSummary.confirmation_status_counts.stale -eq 1) `
        '源文件变化后应产生一个 stale。'

    $ConfirmationState = Get-Content -LiteralPath (
        Join-Path $OutputDirectory 'confirmation-state.json'
    ) -Raw -Encoding utf8 | ConvertFrom-Json
    $StaleDecision = $ConfirmationState.decisions.PSObject.Properties[
        [string]$ConfirmCandidate.candidate_id
    ].Value
    $PreservedRejection = $ConfirmationState.decisions.PSObject.Properties[
        [string]$RejectCandidate.candidate_id
    ].Value
    Assert-True ($StaleDecision.status -eq 'stale') `
        '旧确认没有在 confirmation-state.json 中变为 stale。'
    Assert-True ($PreservedRejection.status -eq 'rejected') `
        '未变化来源的拒绝决定没有保留。'

    $ChangedAuditEvents = @(
        Get-Content -LiteralPath (
            Join-Path $OutputDirectory 'confirmation-audit.jsonl'
        ) -Encoding utf8 |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json }
    )
    Assert-True (@($ChangedAuditEvents | Where-Object {
        $_.action -eq 'stale' -and
        $_.candidate_id -eq $ConfirmCandidate.candidate_id
    }).Count -eq 1) '审计日志缺少 stale 事件。'

    $ChangedMarkdown = Get-Content -LiteralPath (
        Join-Path $OutputDirectory 'conflicts.md'
    ) -Raw -Encoding utf8
    $ChangedHtml = Get-Content -LiteralPath (
        Join-Path $OutputDirectory 'evidence-report.html'
    ) -Raw -Encoding utf8
    Assert-True ($ChangedMarkdown.Contains('已失效')) `
        'Markdown 报告没有显示已失效状态。'
    Assert-True ($ChangedMarkdown.Contains('源文件或文件哈希变化而失效')) `
        'Markdown 报告没有解释 stale 原因。'
    Assert-True ($ChangedHtml.Contains('已失效')) `
        'HTML 报告没有显示已失效状态。'
    Assert-True ($ChangedHtml.Contains('源文件或文件哈希变化而失效')) `
        'HTML 报告没有解释 stale 原因。'

    $ExportAfterChange = Invoke-Entry -Arguments @(
        'export',
        '--output-dir',
        $OutputDirectory,
        '--session-id',
        $SessionId
    ) -ExpectedExitCode 0
    Assert-True ($ExportAfterChange.result.export.confirmed_count -eq 0) `
        '源文件变化后 export 不应导出旧确认。'
    Assert-True ($ExportAfterChange.result.export.stale_count -eq 1) `
        '源文件变化后 export 应报告一个 stale。'
    $ChangedConfirmedFacts = Get-Content -LiteralPath (
        Join-Path $OutputDirectory 'confirmed-product-facts.json'
    ) -Raw -Encoding utf8 | ConvertFrom-Json
    Assert-True ($ChangedConfirmedFacts.facts.Count -eq 0) `
        '源文件变化后正式事实导出应为空。'

    $StaleCandidateRetry = Invoke-Entry -Arguments @(
        'confirm',
        '--output-dir',
        $OutputDirectory,
        '--session-id',
        $SessionId,
        '--candidate-id',
        [string]$ConfirmCandidate.candidate_id,
        '--reason',
        '不应允许重新确认旧候选'
    ) -ExpectedExitCode 1
    Assert-True (-not [bool]$StaleCandidateRetry.ok) `
        '旧候选重新确认不应成功。'
    Assert-True ($StaleCandidateRetry.error.code -eq 'operation_failed') `
        '旧候选重新确认没有稳定错误码。'

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
        public_business_e2e = 'passed'
        confirm_exit_code = 0
        reject_exit_code = 0
        export_before_change_exit_code = 0
        reanalyze_exit_code = 0
        export_after_change_exit_code = 0
        stale_candidate_exit_code = 1
        json_reports = 'passed'
        markdown_report = 'passed'
        html_report = 'passed'
        audit_jsonl = 'passed'
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
