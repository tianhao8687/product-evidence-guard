param(
    [ValidateSet('User', 'Project')]
    [string]$Scope = 'User',
    [string]$ProjectRoot = '',
    [switch]$Update
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$skillName = 'local-product-evidence-guard'

switch ($Scope) {
    'User' {
        $destination = Join-Path $env:USERPROFILE ".qoder\skills\$skillName"
    }
    'Project' {
        if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
            throw '项目级安装必须提供 -ProjectRoot。'
        }
        $resolvedProject = (Resolve-Path -LiteralPath $ProjectRoot).Path
        $destination = Join-Path $resolvedProject ".qoder\skills\$skillName"
    }
}

if (Test-Path -LiteralPath $destination) {
    if (-not $Update) {
        throw "目标已存在：$destination。为避免覆盖，请显式添加 -Update。"
    }
    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = "$destination.backup-$timestamp"
    Move-Item -LiteralPath $destination -Destination $backup
    Write-Host "旧版本已备份到：$backup"
}

$parent = Split-Path -Parent $destination
New-Item -ItemType Directory -Force -Path $parent | Out-Null
New-Item -ItemType Directory -Force -Path $destination | Out-Null

$rootFiles = @(
    'SKILL.md',
    'info.json',
    'meta.json',
    'requirements.txt',
    'pyproject.toml',
    'README.md',
    'LICENSE',
    'THIRD_PARTY_NOTICES.md',
    'PRIVACY.md',
    'SECURITY.md'
)
foreach ($file in $rootFiles) {
    $source = Join-Path $repoRoot $file
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $destination $file)
    }
}

foreach ($directory in @('product_evidence_guard', 'scripts', 'docs')) {
    $source = Join-Path $repoRoot $directory
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $destination $directory) -Recurse
    }
}

Write-Host "Skill 已安装到：$destination"
Write-Host '请重启 Qoder，或在 Qoder CLI 中运行 /skills reload，然后输入 /local-product-evidence-guard 验证。'
