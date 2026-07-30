$ErrorActionPreference = 'Stop'

$Force = $args -contains '-Force'

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$toolsDir = Join-Path $repoRoot '.tools'
$runtimeDir = Join-Path $repoRoot '.runtime'
$logDir = Join-Path $repoRoot 'logs'
$venvDir = Join-Path $repoRoot '.venv'
$requirementsPath = Join-Path $repoRoot 'requirements.txt'
$stampPath = Join-Path $runtimeDir 'install-stamp.json'
$logPath = Join-Path $logDir 'install-env.log'
$uvVersion = '0.8.4'
$uvDir = Join-Path $toolsDir "uv-$uvVersion"
$uvExe = Join-Path $uvDir 'uv.exe'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

New-Item -ItemType Directory -Force -Path $toolsDir, $runtimeDir, $logDir | Out-Null

function Write-InstallLog {
    param([string]$Message)
    $line = "$(Get-Date -Format o) $Message"
    Add-Content -LiteralPath $logPath -Value $line -Encoding utf8
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

Write-Host '正在准备 Product Evidence Guard 独立 Python 3.11 环境……'
Write-InstallLog "Install started. Repository=$repoRoot"

if (-not (Test-Path -LiteralPath $uvExe)) {
    $archive = Join-Path $toolsDir "uv-$uvVersion.zip"
    $extractDir = Join-Path $toolsDir "uv-$uvVersion.partial"
    $url = "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip"
    Write-Host "首次安装需要下载 uv $uvVersion。"
    Write-InstallLog "Downloading uv from $url"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
    if (Test-Path -LiteralPath $extractDir) {
        Remove-Item -LiteralPath $extractDir -Recurse -Force
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $extractDir -Force
    $downloadedUv = Get-ChildItem -LiteralPath $extractDir -Recurse -Filter 'uv.exe' |
        Select-Object -First 1
    if ($null -eq $downloadedUv) {
        throw 'uv 压缩包中没有 uv.exe。'
    }
    New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
    Copy-Item -LiteralPath $downloadedUv.FullName -Destination $uvExe
    Remove-Item -LiteralPath $archive -Force
    Remove-Item -LiteralPath $extractDir -Recurse -Force
}

$requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
$stampMatches = $false
if (-not $Force -and (Test-Path -LiteralPath $stampPath) -and (Test-Path -LiteralPath $venvPython)) {
    try {
        $stamp = Get-Content -LiteralPath $stampPath -Raw | ConvertFrom-Json
        $pythonVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        $stampMatches = (
            $stamp.requirements_sha256 -eq $requirementsHash -and
            $stamp.python_version -eq '3.11' -and
            $pythonVersion.Trim() -eq '3.11'
        )
    } catch {
        $stampMatches = $false
    }
}

if ($stampMatches) {
    Write-Host '环境已安装且版本匹配，无需重复安装。'
    Write-InstallLog 'Install skipped because stamp and Python version match.'
    exit 0
}

$env:UV_PYTHON_INSTALL_DIR = Join-Path $runtimeDir 'python'
Invoke-Checked -FilePath $uvExe -Arguments @('python', 'install', '3.11')

if (-not (Test-Path -LiteralPath $venvPython)) {
    Invoke-Checked -FilePath $uvExe -Arguments @('venv', '--python', '3.11', $venvDir)
} else {
    $pythonVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($pythonVersion.Trim() -ne '3.11') {
        throw "现有 .venv 不是 Python 3.11。请先备份后删除该目录，或使用 -Force 重新创建。"
    }
}

Invoke-Checked -FilePath $uvExe -Arguments @(
    'pip', 'install', '--python', $venvPython, '--upgrade',
    '--requirements', $requirementsPath
)
Invoke-Checked -FilePath $uvExe -Arguments @(
    'pip', 'install', '--python', $venvPython, '--editable', $repoRoot
)
Invoke-Checked -FilePath $uvExe -Arguments @('pip', 'check', '--python', $venvPython)

$stamp = @{
    schema_version = 1
    python_version = '3.11'
    requirements_sha256 = $requirementsHash
    uv_version = $uvVersion
    installed_at = (Get-Date).ToUniversalTime().ToString('o')
}
$stamp | ConvertTo-Json | Set-Content -LiteralPath $stampPath -Encoding utf8

Write-InstallLog 'Install completed successfully.'
Write-Host "环境安装完成。日志：$logPath"
