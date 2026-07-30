param(
    [Parameter(Mandatory = $true)]
    [string]$InputDirectory,
    [Parameter(Mandatory = $true)]
    [string]$ImagePath,
    [string]$ModelPath = '',
    [ValidateSet('CPU', 'GPU', 'NPU')]
    [string]$Device = 'CPU',
    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$RepositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$RunScript = Join-Path $RepositoryRoot 'scripts\run.ps1'
$InfoPath = Join-Path $RepositoryRoot 'info.json'
$ResultPath = $null

function Stop-WithFailure {
    param([Parameter(Mandatory = $true)][string]$Message)

    $Failure = [ordered]@{
        schema_version = 1
        status = 'failed'
        verified_real_model = $false
        error = $Message
    }
    $Json = $Failure | ConvertTo-Json -Depth 6
    if ($null -ne $ResultPath) {
        $Json | Set-Content -LiteralPath $ResultPath -Encoding utf8
    }
    [Console]::Out.WriteLine(($Failure | ConvertTo-Json -Compress -Depth 6))
    exit 1
}

try {
    $InputRoot = (Resolve-Path -LiteralPath $InputDirectory).Path
    $RealImage = (Resolve-Path -LiteralPath $ImagePath).Path
    if (-not (Test-Path -LiteralPath $InputRoot -PathType Container)) {
        Stop-WithFailure '真实模型测试输入目录不存在。'
    }
    if (-not (Test-Path -LiteralPath $RealImage -PathType Leaf)) {
        Stop-WithFailure '真实商品图片不存在。'
    }
    $InputPrefix = $InputRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $RealImage.StartsWith(
        $InputPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        Stop-WithFailure '真实商品图片必须位于 InputDirectory 内。'
    }
    $ImageExtension = [System.IO.Path]::GetExtension($RealImage).ToLowerInvariant()
    if ($ImageExtension -notin @('.png', '.jpg', '.jpeg', '.webp', '.bmp')) {
        Stop-WithFailure 'ImagePath 必须是真实图片文件。'
    }
    if ((Get-Item -LiteralPath $RealImage).Length -lt 1024) {
        Stop-WithFailure '真实商品图片小于 1 KiB，拒绝把空壳文件当作真机样本。'
    }
    if (Test-Path -LiteralPath "$RealImage.ocr.json" -PathType Leaf) {
        Stop-WithFailure '真实模型测试图片旁存在 OCR sidecar，无法证明候选来自真实模型。'
    }

    $Info = Get-Content -LiteralPath $InfoPath -Raw -Encoding utf8 | ConvertFrom-Json
    $ConfiguredModel = $Info.models | Select-Object -First 1
    if ($null -eq $ConfiguredModel) {
        Stop-WithFailure 'info.json 没有配置官方模型。'
    }
    if ([string]::IsNullOrWhiteSpace($ModelPath)) {
        $ModelPath = Join-Path $RepositoryRoot $ConfiguredModel.local_dir
    }
    $ResolvedModel = (Resolve-Path -LiteralPath $ModelPath).Path
    if (-not (Test-Path -LiteralPath $ResolvedModel -PathType Container)) {
        Stop-WithFailure '真实模型目录不存在。'
    }
    $LowerModelPath = $ResolvedModel.ToLowerInvariant()
    if ($LowerModelPath.Contains('mock') -or
        $LowerModelPath.Contains('fake-model') -or
        $LowerModelPath.Contains('dummy-model')) {
        Stop-WithFailure '模型目录名称含 mock/fake/dummy，不能作为真实模型验证。'
    }

    $ModelFiles = Get-ChildItem -LiteralPath $ResolvedModel -Recurse -File
    $ModelBytes = ($ModelFiles | Measure-Object -Property Length -Sum).Sum
    if ($null -eq $ModelBytes -or $ModelBytes -lt 1GB) {
        Stop-WithFailure '模型总大小不足 1GB，拒绝把测试桩当作 Qwen3-VL 8B。'
    }
    foreach ($Required in $ConfiguredModel.required_files) {
        $RequiredPath = Join-Path $ResolvedModel ([string]$Required)
        if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
            Stop-WithFailure "模型缺少 required file：$Required"
        }
        if ((Get-Item -LiteralPath $RequiredPath).Length -le 0) {
            Stop-WithFailure "模型 required file 为空：$Required"
        }
    }
    foreach ($LargeCore in @(
        'openvino_language_model.bin',
        'openvino_vision_embeddings_model.bin'
    )) {
        $CorePath = Join-Path $ResolvedModel $LargeCore
        if ((Get-Item -LiteralPath $CorePath).Length -lt 1MB) {
            Stop-WithFailure "模型核心权重异常小：$LargeCore"
        }
    }
    $ConfigText = Get-Content -LiteralPath (Join-Path $ResolvedModel 'config.json') `
        -Raw -Encoding utf8
    $LowerConfig = $ConfigText.ToLowerInvariant()
    if (-not ($LowerConfig.Contains('qwen3') -and $LowerConfig.Contains('vl'))) {
        Stop-WithFailure 'config.json 无法证明模型是 Qwen3-VL。'
    }
    if ($LowerConfig.Contains('"mock"') -or $LowerConfig.Contains('"fake"')) {
        Stop-WithFailure 'config.json 表明这是 mock/fake 模型。'
    }

    if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
        $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $OutputDirectory = Join-Path $RepositoryRoot "benchmark-output\real-model-$Stamp"
    }
    $ResolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
    New-Item -ItemType Directory -Force -Path $ResolvedOutput | Out-Null
    $ResultPath = Join-Path $ResolvedOutput 'real-model-test-result.json'
    $StderrPath = Join-Path $ResolvedOutput 'run-stderr.log'

    $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $AnalyzeOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File $RunScript analyze $InputRoot `
        --output $ResolvedOutput `
        --model $ResolvedModel `
        --device $Device 2> $StderrPath
    $AnalyzeExitCode = $LASTEXITCODE
    $Stopwatch.Stop()
    if ($AnalyzeExitCode -ne 0) {
        Stop-WithFailure (
            "真实模型 analyze 失败，exit=$AnalyzeExitCode；" +
            "请保留 $StderrPath 和服务日志。"
        )
    }
    try {
        $AnalyzeResponse = ($AnalyzeOutput -join "`n") | ConvertFrom-Json
    }
    catch {
        Stop-WithFailure '真实模型 analyze stdout 不是稳定 JSON。'
    }
    if (-not [bool]$AnalyzeResponse.ok -or $AnalyzeResponse.status -ne 'running') {
        Stop-WithFailure '真实模型 analyze 没有返回 ok/running。'
    }

    $SummaryPath = Join-Path $ResolvedOutput 'run-summary.json'
    $FactsPath = Join-Path $ResolvedOutput 'product-facts.json'
    $VisualPath = Join-Path $ResolvedOutput 'visual-transcription.json'
    foreach ($RequiredOutput in @($SummaryPath, $FactsPath, $VisualPath)) {
        if (-not (Test-Path -LiteralPath $RequiredOutput -PathType Leaf)) {
            Stop-WithFailure "真实模型分析缺少输出：$RequiredOutput"
        }
    }
    $Summary = Get-Content -LiteralPath $SummaryPath -Raw -Encoding utf8 |
        ConvertFrom-Json
    if (-not [bool]$Summary.local_ai.image_reader) {
        Stop-WithFailure 'run-summary 没有证明 image_reader 已启用。'
    }
    if ([string]$Summary.local_ai.device -ne $Device) {
        Stop-WithFailure 'run-summary 设备与请求设备不一致。'
    }
    $Facts = Get-Content -LiteralPath $FactsPath -Raw -Encoding utf8 |
        ConvertFrom-Json
    if ($Facts.status -ne 'pending_human_confirmation') {
        Stop-WithFailure '真实模型候选被错误地自动晋升为正式事实。'
    }

    $RelativeImage = $RealImage.Substring($InputPrefix.Length).Replace('\', '/')
    $ImageHash = (Get-FileHash -LiteralPath $RealImage -Algorithm SHA256).Hash.ToLowerInvariant()
    $ImageCandidates = @(
        $Facts.candidates | Where-Object {
            ([string]$_.source_file).Replace('\', '/') -eq $RelativeImage -and
            ([string]$_.file_hash).ToLowerInvariant() -eq $ImageHash -and
            ([string]$_.source_kind).StartsWith('image_') -and
            -not ([string]$_.source_kind).ToLowerInvariant().Contains('ocr') -and
            -not ([string]$_.source_kind).ToLowerInvariant().Contains('mock') -and
            -not ([string]$_.extraction_method).ToLowerInvariant().Contains('mock') -and
            -not [string]::IsNullOrWhiteSpace([string]$_.raw_text) -and
            -not [string]::IsNullOrWhiteSpace([string]$_.raw_value)
        }
    )
    if ($ImageCandidates.Count -lt 1) {
        Stop-WithFailure '真实图片没有产生任何可验证的非 OCR、非 mock 图片候选。'
    }

    $VisualRaw = Get-Content -LiteralPath $VisualPath -Raw -Encoding utf8
    try {
        $null = $VisualRaw | ConvertFrom-Json
    }
    catch {
        Stop-WithFailure 'visual-transcription.json 不是合法 JSON。'
    }
    if ($VisualRaw.Length -lt 20 -or
        $VisualRaw.ToLowerInvariant().Contains('"mock"') -or
        $VisualRaw.ToLowerInvariant().Contains('mock_backend')) {
        Stop-WithFailure 'visual-transcription.json 为空或含 mock 标记。'
    }
    if ($VisualRaw -notmatch '"raw_text"\s*:\s*"[^"]+"') {
        Stop-WithFailure 'visual-transcription.json 没有非空视觉原文。'
    }
    if ($VisualRaw -notmatch '"raw_transcription_output"\s*:\s*"[^"]+') {
        Stop-WithFailure 'visual-transcription.json 没有保留非空模型原始输出。'
    }

    # Contract: run.ps1 status must succeed after the real inference request.
    $StatusOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File $RunScript status
    $StatusExitCode = $LASTEXITCODE
    if ($StatusExitCode -ne 0) {
        Stop-WithFailure "运行后 run.ps1 status 失败，exit=$StatusExitCode。"
    }
    try {
        $StatusResponse = ($StatusOutput -join "`n") | ConvertFrom-Json
    }
    catch {
        Stop-WithFailure '运行后 status 不是合法 JSON。'
    }
    if (-not [bool]$StatusResponse.ok -or
        $StatusResponse.status -ne 'running' -or
        [int]$StatusResponse.result.server.pid -le 0) {
        Stop-WithFailure '运行后服务不是可验证的 running 状态。'
    }

    $Passed = [ordered]@{
        schema_version = 1
        status = 'passed'
        verified_real_model = $true
        model_id = [string]$ConfiguredModel.id
        model_bytes = [long]$ModelBytes
        requested_device = $Device
        image_relative_path = $RelativeImage
        image_sha256 = $ImageHash
        image_candidate_count = $ImageCandidates.Count
        analyze_elapsed_seconds = [Math]::Round($Stopwatch.Elapsed.TotalSeconds, 3)
        server_status_after_run = $StatusResponse.status
        server_pid = [int]$StatusResponse.result.server.pid
        visual_transcription = $VisualPath
        product_facts = $FactsPath
        warning = '该结果只证明本次指定图片和设备；不得外推为业务准确率。'
    }
    $Passed | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $ResultPath -Encoding utf8
    [Console]::Out.WriteLine(($Passed | ConvertTo-Json -Compress -Depth 8))
    exit 0
}
catch {
    Stop-WithFailure ("真实模型测试异常：" + $_.Exception.Message)
}
