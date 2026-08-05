param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

trap {
    $failureMessage = $_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($failureMessage)) {
        $failureMessage = '未知错误'
    }
    [Console]::Error.WriteLine("环境安装失败：$failureMessage")
    exit 1
}

$pythonVersion = '3.11.13'
$uvVersion = '0.8.4'
$uvArchiveName = 'uv-x86_64-pc-windows-msvc.zip'
# Official release checksum:
# https://github.com/astral-sh/uv/releases/download/0.8.4/uv-x86_64-pc-windows-msvc.zip.sha256
$uvArchiveSha256 = '817c50c80229f88de9699626ee3774c0cceed86099663e8fb00c5ffae7ea911c'
$expectedUvExeSha256 = '658a2f7706f10eb424e7a13e8601aae5ed1f91d0ea53b5a313d238cfb10ffa9f'

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$toolsDir = Join-Path $repoRoot '.tools'
$runtimeDir = Join-Path $repoRoot '.runtime'
$logDir = Join-Path $repoRoot 'logs'
$venvDir = Join-Path $repoRoot '.venv'
$requirementsPath = Join-Path $repoRoot 'requirements.txt'
$requirementsLockPath = Join-Path $repoRoot 'requirements.lock'
$stampPath = Join-Path $runtimeDir 'install-stamp.json'
$pythonInstallDir = Join-Path $runtimeDir 'python'
$uvCacheDir = Join-Path $runtimeDir 'uv-cache'
$logPath = Join-Path $logDir 'install-env.log'
$uvDir = Join-Path $toolsDir "uv-$uvVersion"
$uvExe = Join-Path $uvDir 'uv.exe'
$uvVerificationPath = Join-Path $uvDir 'verification.json'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

function Write-InstallLog {
    param([string]$Message)
    $line = "$(Get-Date -Format o) $Message"
    $safeLogPath = Assert-SafeManagedFile `
        -Path $logPath `
        -BoundaryRoot $repoRoot `
        -Context '安装日志'
    Add-Content -LiteralPath $safeLogPath -Value $line -Encoding utf8
    [void](Assert-SafeManagedFile `
        -Path $safeLogPath `
        -BoundaryRoot $repoRoot `
        -Context '安装日志')
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )
    Write-InstallLog "RUN $FilePath $($Arguments -join ' ')"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
    }
}

function Invoke-QuietExitCode {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    # Windows PowerShell 5.1 can promote a successful native program's stderr
    # progress text to NativeCommandError when the script uses Stop globally.
    # Capture it under Continue and trust the native exit code.
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $commandOutput = & $FilePath @Arguments 2>&1
        $commandExitCode = $LASTEXITCODE
        if ($null -ne $commandOutput) {
            foreach ($line in @($commandOutput)) {
                Write-InstallLog "CHECK $line"
            }
        }
        return $commandExitCode
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Assert-NoReparsePoint {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Context
    )

    $item = Get-Item -LiteralPath $Path -Force
    if (
        ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "$Context 包含符号链接、junction 或 reparse point：$Path"
    }
    $linkTypeProperty = $item.PSObject.Properties['LinkType']
    if (
        $null -ne $linkTypeProperty -and
        -not [string]::IsNullOrWhiteSpace([string]$linkTypeProperty.Value) -and
        [string]$linkTypeProperty.Value -ne 'HardLink'
    ) {
        throw "$Context 包含链接：$Path"
    }
}

function Assert-SingleHardLink {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Context
    )

    if (
        [System.Environment]::OSVersion.Platform -eq
        [System.PlatformID]::Win32NT
    ) {
        $fsutil = Get-Command 'fsutil.exe' -ErrorAction SilentlyContinue
        if ($null -eq $fsutil) {
            throw "$Context 无法验证硬链接数量（缺少 fsutil.exe）：$Path"
        }
        $savedErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $links = @(
                & $fsutil.Source hardlink list $Path 2>$null |
                    ForEach-Object { $_.ToString() }
            )
            $linkExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $savedErrorActionPreference
        }
        if ($linkExitCode -ne 0 -or $links.Count -ne 1) {
            throw "$Context 是硬链接或无法唯一验证：$Path"
        }
        return
    }

    $stat = Get-Command 'stat' -ErrorAction SilentlyContinue
    if ($null -eq $stat) {
        throw "$Context 无法验证硬链接数量（缺少 stat）：$Path"
    }
    $savedErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $countText = @(& $stat.Source -c '%h' -- $Path 2>$null)
        $statExitCode = $LASTEXITCODE
        if ($statExitCode -ne 0 -or $countText.Count -ne 1) {
            $countText = @(& $stat.Source -f '%l' -- $Path 2>$null)
            $statExitCode = $LASTEXITCODE
        }
    }
    finally {
        $ErrorActionPreference = $savedErrorActionPreference
    }
    $linkCount = 0L
    if (
        $statExitCode -ne 0 -or
        $countText.Count -ne 1 -or
        -not [long]::TryParse(
            $countText[0].ToString().Trim(),
            [ref]$linkCount
        ) -or
        $linkCount -ne 1
    ) {
        throw "$Context 是硬链接或无法唯一验证：$Path"
    }
}

function Assert-SafeManagedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$BoundaryRoot,
        [Parameter(Mandatory = $true)][string]$Context
    )

    $fileFull = [System.IO.Path]::GetFullPath($Path)
    $boundaryFull = [System.IO.Path]::GetFullPath($BoundaryRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $boundaryPrefix = $boundaryFull + [System.IO.Path]::DirectorySeparatorChar
    if (-not $fileFull.StartsWith(
        $boundaryPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Context 越出仓库边界：$fileFull"
    }

    $current = [System.IO.Path]::GetDirectoryName($fileFull)
    while ($true) {
        if (
            [string]::IsNullOrWhiteSpace($current) -or
            -not (
                $current.Equals(
                    $boundaryFull,
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -or
                $current.StartsWith(
                    $boundaryPrefix,
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            )
        ) {
            throw "$Context 的父目录越出仓库边界：$fileFull"
        }
        if (-not (Test-Path -LiteralPath $current -PathType Container)) {
            throw "$Context 的父目录不存在或不是目录：$current"
        }
        Assert-NoReparsePoint -Path $current -Context $Context
        if ($current.Equals(
            $boundaryFull,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            break
        }
        $current = [System.IO.Path]::GetDirectoryName(
            $current.TrimEnd('\', '/')
        )
    }

    $existing = Get-Item `
        -LiteralPath $fileFull `
        -Force `
        -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if ($existing.PSIsContainer) {
            throw "$Context 的文件目标是目录：$fileFull"
        }
        Assert-NoReparsePoint -Path $fileFull -Context $Context
        Assert-SingleHardLink -Path $fileFull -Context $Context
    }
    return $fileFull
}

function Assert-ExactManagedDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$ExpectedPath,
        [Parameter(Mandatory = $true)][string]$BoundaryRoot
    )

    $targetFull = [System.IO.Path]::GetFullPath($TargetPath)
    $expectedFull = [System.IO.Path]::GetFullPath($ExpectedPath)
    $boundaryFull = [System.IO.Path]::GetFullPath($BoundaryRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $boundaryPrefix = $boundaryFull + [System.IO.Path]::DirectorySeparatorChar
    if (-not (Test-Path -LiteralPath $boundaryFull -PathType Container)) {
        throw "Managed directory boundary is not a directory: $boundaryFull"
    }
    Assert-NoReparsePoint `
        -Path $boundaryFull `
        -Context 'Managed directory boundary'
    if (-not [string]::Equals(
        $targetFull,
        $expectedFull,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to manage an unexpected directory: $targetFull"
    }
    if (-not $targetFull.StartsWith(
        $boundaryPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to manage a directory outside the repository boundary: $targetFull"
    }
    if (Test-Path -LiteralPath $targetFull) {
        $item = Get-Item -LiteralPath $targetFull -Force
        if (-not $item.PSIsContainer) {
            throw "Managed directory target is not a directory: $targetFull"
        }
        if (
            ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "Refusing to manage a reparse-point directory: $targetFull"
        }
        $resolved = (Resolve-Path -LiteralPath $targetFull).Path
        if (-not [string]::Equals(
            $resolved,
            $expectedFull,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Managed directory resolves outside its exact target: $targetFull"
        }
    }
    return $targetFull
}

function Initialize-ExactManagedDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$ExpectedPath,
        [Parameter(Mandatory = $true)][string]$BoundaryRoot
    )

    $verified = Assert-ExactManagedDirectory `
        -TargetPath $TargetPath `
        -ExpectedPath $ExpectedPath `
        -BoundaryRoot $BoundaryRoot
    if (-not (Test-Path -LiteralPath $verified)) {
        New-Item -ItemType Directory -Path $verified | Out-Null
    }
    return (Assert-ExactManagedDirectory `
        -TargetPath $verified `
        -ExpectedPath $ExpectedPath `
        -BoundaryRoot $BoundaryRoot)
}

function Assert-NoReparseDescendants {
    param(
        [Parameter(Mandatory = $true)][string]$RootPath,
        [Parameter(Mandatory = $true)][string]$Context
    )

    Assert-NoReparsePoint -Path $RootPath -Context $Context
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push([System.IO.Path]::GetFullPath($RootPath))
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        foreach ($child in @(
            Get-ChildItem -LiteralPath $current -Force
        )) {
            Assert-NoReparsePoint -Path $child.FullName -Context $Context
            if ($child.PSIsContainer) {
                $pending.Push($child.FullName)
            }
        }
    }
}

function Remove-ExactManagedDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$ExpectedPath,
        [Parameter(Mandatory = $true)][string]$BoundaryRoot
    )
    $verified = Assert-ExactManagedDirectory `
        -TargetPath $TargetPath `
        -ExpectedPath $ExpectedPath `
        -BoundaryRoot $BoundaryRoot
    if (Test-Path -LiteralPath $verified -PathType Container) {
        Assert-NoReparseDescendants `
            -RootPath $verified `
            -Context 'Managed directory recursive removal'
        Remove-Item -LiteralPath $verified -Recurse -Force
    }
}

function Get-ExactPythonVersion {
    param([Parameter(Mandatory = $true)][string]$PythonPath)
    $detected = & $PythonPath -c (
        "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to query Python version: $PythonPath"
    }
    return $detected.Trim()
}

if (-not (Test-Path -LiteralPath $repoRoot -PathType Container)) {
    throw "Repository root is not a directory: $repoRoot"
}
Assert-NoReparsePoint -Path $repoRoot -Context 'Repository root'

# Fail before creating caches or attempting any download.  A missing lock is a
# local configuration error and must never be turned into network activity.
if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    throw '缺少 requirements.txt，无法验证锁文件输入。'
}
if (-not (Test-Path -LiteralPath $requirementsLockPath -PathType Leaf)) {
    throw '缺少 requirements.lock，拒绝从未锁定的传递依赖安装。'
}

[void](Initialize-ExactManagedDirectory `
    -TargetPath $toolsDir `
    -ExpectedPath (Join-Path $repoRoot '.tools') `
    -BoundaryRoot $repoRoot)
[void](Initialize-ExactManagedDirectory `
    -TargetPath $runtimeDir `
    -ExpectedPath (Join-Path $repoRoot '.runtime') `
    -BoundaryRoot $repoRoot)
[void](Initialize-ExactManagedDirectory `
    -TargetPath $logDir `
    -ExpectedPath (Join-Path $repoRoot 'logs') `
    -BoundaryRoot $repoRoot)
[void](Initialize-ExactManagedDirectory `
    -TargetPath $pythonInstallDir `
    -ExpectedPath (Join-Path $runtimeDir 'python') `
    -BoundaryRoot $runtimeDir)
[void](Initialize-ExactManagedDirectory `
    -TargetPath $uvCacheDir `
    -ExpectedPath (Join-Path $runtimeDir 'uv-cache') `
    -BoundaryRoot $runtimeDir)
$env:UV_CACHE_DIR = $uvCacheDir

Write-Host "正在准备 Product Evidence Guard 独立 Python $pythonVersion 环境……"
Write-InstallLog "Install started. Repository=$repoRoot"

$uvReady = $false
if (Test-Path -LiteralPath $uvDir) {
    [void](Assert-ExactManagedDirectory `
        -TargetPath $uvDir `
        -ExpectedPath (Join-Path $toolsDir "uv-$uvVersion") `
        -BoundaryRoot $toolsDir)
}
if (
    (Test-Path -LiteralPath $uvExe -PathType Leaf) -and
    (Test-Path -LiteralPath $uvVerificationPath -PathType Leaf)
) {
    try {
        $safeUvExe = Assert-SafeManagedFile `
            -Path $uvExe `
            -BoundaryRoot $uvDir `
            -Context 'uv cache executable'
        $safeUvVerificationPath = Assert-SafeManagedFile `
            -Path $uvVerificationPath `
            -BoundaryRoot $uvDir `
            -Context 'uv cache verification'
        $verification = Get-Content -LiteralPath $safeUvVerificationPath -Raw |
            ConvertFrom-Json
        $currentUvExeSha256 = (
            Get-FileHash -LiteralPath $safeUvExe -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        $uvReady = (
            $verification.uv_version -eq $uvVersion -and
            $verification.archive_name -eq $uvArchiveName -and
            $verification.archive_sha256 -eq $uvArchiveSha256 -and
            $verification.uv_exe_sha256 -eq $expectedUvExeSha256 -and
            $currentUvExeSha256 -eq $expectedUvExeSha256
        )
    }
    catch {
        $uvReady = $false
    }
}

if (-not $uvReady) {
    $downloadId = [Guid]::NewGuid().ToString('N')
    $archive = Join-Path $toolsDir "uv-$uvVersion-$downloadId.zip.partial"
    $verifiedArchive = Join-Path $toolsDir "uv-$uvVersion-$downloadId.verified.zip"
    $extractDir = Join-Path $toolsDir "uv-$uvVersion-$downloadId.partial"
    $url = "https://github.com/astral-sh/uv/releases/download/$uvVersion/$uvArchiveName"
    try {
        Write-Host "首次安装需要下载并校验 uv $uvVersion。"
        Write-InstallLog "Downloading uv primary artifact from $url"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
        $actualArchiveSha256 = (
            Get-FileHash -LiteralPath $archive -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        if ($actualArchiveSha256 -ne $uvArchiveSha256) {
            throw (
                "uv archive SHA-256 mismatch. expected=$uvArchiveSha256 " +
                "actual=$actualArchiveSha256"
            )
        }
        Write-InstallLog "uv archive SHA-256 verified: $actualArchiveSha256"

        # Windows PowerShell 5.1 only accepts a .zip suffix for Expand-Archive.
        # The untrusted download remains .partial until its hash is verified.
        Move-Item -LiteralPath $archive -Destination $verifiedArchive
        Expand-Archive -LiteralPath $verifiedArchive -DestinationPath $extractDir
        $downloadedUv = Get-ChildItem -LiteralPath $extractDir -Recurse -Filter 'uv.exe' |
            Select-Object -First 1
        if ($null -eq $downloadedUv) {
            throw 'uv 压缩包中没有 uv.exe。'
        }
        Assert-NoReparsePoint `
            -Path $downloadedUv.FullName `
            -Context 'verified uv archive executable'
        $extractedUvExeSha256 = (
            Get-FileHash -LiteralPath $downloadedUv.FullName -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        if ($extractedUvExeSha256 -ne $expectedUvExeSha256) {
            throw (
                "uv.exe SHA-256 mismatch after extraction. " +
                "expected=$expectedUvExeSha256 actual=$extractedUvExeSha256"
            )
        }

        if (Test-Path -LiteralPath $uvDir) {
            Remove-ExactManagedDirectory `
                -TargetPath $uvDir `
                -ExpectedPath (Join-Path $toolsDir "uv-$uvVersion") `
                -BoundaryRoot $toolsDir
        }
        [void](Initialize-ExactManagedDirectory `
            -TargetPath $uvDir `
            -ExpectedPath (Join-Path $toolsDir "uv-$uvVersion") `
            -BoundaryRoot $toolsDir)
        Copy-Item -LiteralPath $downloadedUv.FullName -Destination $uvExe
        $safeUvExe = Assert-SafeManagedFile `
            -Path $uvExe `
            -BoundaryRoot $uvDir `
            -Context 'installed uv executable'
        $installedUvExeSha256 = (
            Get-FileHash -LiteralPath $safeUvExe -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        if ($installedUvExeSha256 -ne $expectedUvExeSha256) {
            throw (
                "installed uv.exe SHA-256 mismatch. " +
                "expected=$expectedUvExeSha256 actual=$installedUvExeSha256"
            )
        }
        $safeUvVerificationPath = Assert-SafeManagedFile `
            -Path $uvVerificationPath `
            -BoundaryRoot $uvDir `
            -Context 'uv verification write'
        @{
            schema_version = 1
            uv_version = $uvVersion
            archive_name = $uvArchiveName
            archive_sha256 = $uvArchiveSha256
            uv_exe_sha256 = $expectedUvExeSha256
            verified_at = (Get-Date).ToUniversalTime().ToString('o')
        } | ConvertTo-Json | Set-Content `
            -LiteralPath $safeUvVerificationPath `
            -Encoding utf8
        [void](Assert-SafeManagedFile `
            -Path $safeUvVerificationPath `
            -BoundaryRoot $uvDir `
            -Context 'uv verification write')
    }
    finally {
        if (Test-Path -LiteralPath $archive -PathType Leaf) {
            Remove-Item -LiteralPath $archive -Force
        }
        if (Test-Path -LiteralPath $verifiedArchive -PathType Leaf) {
            Remove-Item -LiteralPath $verifiedArchive -Force
        }
        if (Test-Path -LiteralPath $extractDir -PathType Container) {
            Remove-ExactManagedDirectory `
                -TargetPath $extractDir `
                -ExpectedPath (Join-Path $toolsDir "uv-$uvVersion-$downloadId.partial") `
                -BoundaryRoot $toolsDir
        }
    }
}

if (Test-Path -LiteralPath $venvDir) {
    [void](Assert-ExactManagedDirectory `
        -TargetPath $venvDir `
        -ExpectedPath (Join-Path $repoRoot '.venv') `
        -BoundaryRoot $repoRoot)
}

$safeRequirementsPath = Assert-SafeManagedFile `
    -Path $requirementsPath `
    -BoundaryRoot $repoRoot `
    -Context 'requirements input'
$safeRequirementsLockPath = Assert-SafeManagedFile `
    -Path $requirementsLockPath `
    -BoundaryRoot $repoRoot `
    -Context 'requirements lock'
if (-not (Test-Path -LiteralPath $safeRequirementsPath -PathType Leaf)) {
    throw '缺少 requirements.txt，无法验证锁文件输入。'
}
if (-not (Test-Path -LiteralPath $safeRequirementsLockPath -PathType Leaf)) {
    throw '缺少 requirements.lock，拒绝从未锁定的传递依赖安装。'
}
$requirementsHash = (
    Get-FileHash -LiteralPath $safeRequirementsPath -Algorithm SHA256
).Hash
$requirementsLockHash = (
    Get-FileHash -LiteralPath $safeRequirementsLockPath -Algorithm SHA256
).Hash
$stampMatches = $false
if (
    -not $Force -and
    (Test-Path -LiteralPath $stampPath -PathType Leaf) -and
    (Test-Path -LiteralPath $venvPython -PathType Leaf)
) {
    try {
        $safeStampPath = Assert-SafeManagedFile `
            -Path $stampPath `
            -BoundaryRoot $repoRoot `
            -Context 'install stamp'
        $stamp = Get-Content -LiteralPath $safeStampPath -Raw | ConvertFrom-Json
        $detectedPythonVersion = Get-ExactPythonVersion -PythonPath $venvPython
        $stampMatches = (
            $stamp.requirements_sha256 -eq $requirementsHash -and
            $stamp.requirements_lock_sha256 -eq $requirementsLockHash -and
            $stamp.python_version -eq $pythonVersion -and
            $stamp.uv_version -eq $uvVersion -and
            $stamp.uv_archive_sha256 -eq $uvArchiveSha256 -and
            $detectedPythonVersion -eq $pythonVersion
        )
        if ($stampMatches) {
            $checkExitCode = Invoke-QuietExitCode `
                -FilePath $uvExe `
                -Arguments @('pip', 'check', '--python', $venvPython)
            $stampMatches = $checkExitCode -eq 0
        }
    }
    catch {
        $stampMatches = $false
    }
}

if ($stampMatches) {
    Write-Host '环境已安装且精确版本匹配，无需重复安装。'
    Write-InstallLog 'Install skipped because stamp, Python patch version, and pip check match.'
    exit 0
}

if (-not $Force -and (Test-Path -LiteralPath $venvDir)) {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw '现有 .venv 不完整。请使用 -Force 在安全校验后重新创建。'
    }
    $detectedPythonVersion = Get-ExactPythonVersion -PythonPath $venvPython
    if ($detectedPythonVersion -ne $pythonVersion) {
        throw (
            "现有 .venv 是 Python $detectedPythonVersion，不是 $pythonVersion。" +
            '请使用 -Force 在安全校验后重新创建。'
        )
    }
}

$env:UV_PYTHON_INSTALL_DIR = $pythonInstallDir
Invoke-Checked -FilePath $uvExe -Arguments @('python', 'install', $pythonVersion)

if ($Force -and (Test-Path -LiteralPath $venvDir)) {
    Write-Host '正在安全重建仓库内的 .venv……'
    Write-InstallLog "Force rebuild requested for exact target $venvDir"
    Remove-ExactManagedDirectory `
        -TargetPath $venvDir `
        -ExpectedPath (Join-Path $repoRoot '.venv') `
        -BoundaryRoot $repoRoot
}

if (-not (Test-Path -LiteralPath $venvDir)) {
    Invoke-Checked -FilePath $uvExe -Arguments @(
        'venv', '--python', $pythonVersion, $venvDir
    )
}

$detectedPythonVersion = Get-ExactPythonVersion -PythonPath $venvPython
if ($detectedPythonVersion -ne $pythonVersion) {
    throw "新环境 Python 版本错误：expected=$pythonVersion actual=$detectedPythonVersion"
}

Invoke-Checked -FilePath $uvExe -Arguments @(
    'pip', 'sync',
    '--python', $venvPython,
    '--quiet',
    '--require-hashes',
    '--strict',
    $safeRequirementsLockPath
)
Invoke-Checked -FilePath $uvExe -Arguments @(
    'pip', 'install',
    '--python', $venvPython,
    '--no-build-isolation',
    '--no-deps',
    '--no-index',
    '--editable', $repoRoot
)
Invoke-Checked -FilePath $uvExe -Arguments @(
    'pip', 'check', '--python', $venvPython
)

$stamp = @{
    schema_version = 3
    python_version = $pythonVersion
    requirements_sha256 = $requirementsHash
    requirements_lock_sha256 = $requirementsLockHash
    uv_version = $uvVersion
    uv_archive_sha256 = $uvArchiveSha256
    installed_at = (Get-Date).ToUniversalTime().ToString('o')
}
$safeStampPath = Assert-SafeManagedFile `
    -Path $stampPath `
    -BoundaryRoot $repoRoot `
    -Context 'install stamp write'
$stamp | ConvertTo-Json | Set-Content `
    -LiteralPath $safeStampPath `
    -Encoding utf8
[void](Assert-SafeManagedFile `
    -Path $safeStampPath `
    -BoundaryRoot $repoRoot `
    -Context 'install stamp write')

Write-InstallLog 'Install completed successfully.'
Write-Host "环境安装完成。日志：$logPath"
