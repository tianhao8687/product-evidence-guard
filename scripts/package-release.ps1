param(
    [string]$Version = '1.0.0',
    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$RepositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if ($Version -notmatch '^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$') {
    [Console]::Error.WriteLine('版本号必须是安全的 SemVer 字符串。')
    exit 1
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $ReleaseRoot = Join-Path $RepositoryRoot 'release'
}
elseif ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $ReleaseRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
}
else {
    $ReleaseRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $RepositoryRoot $OutputDirectory)
    )
}

$ArchiveName = "local-product-evidence-guard-v$Version.zip"
$ArchivePath = Join-Path $ReleaseRoot $ArchiveName
$PartialPath = "$ArchivePath.partial"
$HashPath = "$ArchivePath.sha256"

# Only these product roots may enter a release. In particular, never include
# .models, .runtime, .git, caches, logs, or original samples\real material.
$AllowedRootFiles = @(
    '.gitignore',
    'CHANGELOG.md',
    'LICENSE',
    'PRIVACY.md',
    'README.md',
    'SECURITY.md',
    'SKILL.md',
    'THIRD_PARTY_NOTICES.md',
    'info.json',
    'meta.json',
    'pyproject.toml',
    'requirements.txt'
)
$AllowedDirectories = @(
    '.github',
    'docs',
    'product_evidence_guard',
    'samples',
    'scripts',
    'tests'
)
$ExcludedSegments = @(
    '.git',
    '.models',
    '.runtime',
    '.tools',
    '.venv',
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    'benchmark-output',
    'demo-output',
    '.peg-output',
    'release',
    'logs',
    'build',
    'dist'
)
$ExcludedFilePatterns = @(
    '*.pyc',
    '*.pyo',
    '*.partial',
    '*.pem',
    '*.key',
    '.env',
    '.env.*',
    'auth.json',
    'pending-request.json',
    'server-state.json',
    'server.pid',
    'server.lock'
)

function Get-RepositoryRelativePath {
    param([Parameter(Mandatory = $true)][string]$FullName)

    $Resolved = [System.IO.Path]::GetFullPath($FullName)
    $Prefix = $RepositoryRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $Resolved.StartsWith($Prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "发布文件不在仓库内：$Resolved"
    }
    return $Resolved.Substring($Prefix.Length).Replace('\', '/')
}

function Test-ReleasePath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $Normalized = $RelativePath.Replace('\', '/')
    if ($Normalized.StartsWith('samples/real/', [System.StringComparison]::OrdinalIgnoreCase) -or
        $Normalized.Equals('samples/real', [System.StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    $Segments = $Normalized.Split('/')
    foreach ($Segment in $Segments) {
        if ($ExcludedSegments -contains $Segment) {
            return $false
        }
    }
    $Leaf = $Segments[$Segments.Length - 1]
    foreach ($Pattern in $ExcludedFilePatterns) {
        if ($Leaf -like $Pattern) {
            return $false
        }
    }
    return $true
}

try {
    New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null

    $FilesByRelativePath = @{}
    foreach ($Name in $AllowedRootFiles) {
        $Path = Join-Path $RepositoryRoot $Name
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $Relative = Get-RepositoryRelativePath -FullName $Path
            if (Test-ReleasePath -RelativePath $Relative) {
                $FilesByRelativePath[$Relative] = (Resolve-Path -LiteralPath $Path).Path
            }
        }
    }
    foreach ($DirectoryName in $AllowedDirectories) {
        $DirectoryPath = Join-Path $RepositoryRoot $DirectoryName
        if (-not (Test-Path -LiteralPath $DirectoryPath -PathType Container)) {
            continue
        }
        foreach ($File in Get-ChildItem -LiteralPath $DirectoryPath -Recurse -File -Force) {
            $Relative = Get-RepositoryRelativePath -FullName $File.FullName
            if (Test-ReleasePath -RelativePath $Relative) {
                $FilesByRelativePath[$Relative] = $File.FullName
            }
        }
    }
    if ($FilesByRelativePath.Count -eq 0) {
        throw '发布白名单中没有任何文件。'
    }

    foreach ($Target in @($PartialPath, $ArchivePath, $HashPath)) {
        if (Test-Path -LiteralPath $Target -PathType Leaf) {
            Remove-Item -LiteralPath $Target -Force
        }
    }

    Add-Type -AssemblyName System.IO.Compression
    $ArchiveStream = [System.IO.File]::Open(
        $PartialPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
    try {
        $Archive = [System.IO.Compression.ZipArchive]::new(
            $ArchiveStream,
            [System.IO.Compression.ZipArchiveMode]::Create,
            $true
        )
        try {
            $FixedTime = [System.DateTimeOffset]::new(
                2020, 1, 1, 0, 0, 0, [System.TimeSpan]::Zero
            )
            foreach ($Relative in @($FilesByRelativePath.Keys | Sort-Object)) {
                $Entry = $Archive.CreateEntry(
                    $Relative,
                    [System.IO.Compression.CompressionLevel]::Optimal
                )
                $Entry.LastWriteTime = $FixedTime
                $Entry.ExternalAttributes = 0
                $InputStream = [System.IO.File]::OpenRead($FilesByRelativePath[$Relative])
                try {
                    $OutputStream = $Entry.Open()
                    try {
                        $InputStream.CopyTo($OutputStream)
                    }
                    finally {
                        $OutputStream.Dispose()
                    }
                }
                finally {
                    $InputStream.Dispose()
                }
            }
        }
        finally {
            $Archive.Dispose()
        }
    }
    finally {
        $ArchiveStream.Dispose()
    }

    Move-Item -LiteralPath $PartialPath -Destination $ArchivePath
    $Hash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $ArchiveName" | Set-Content -LiteralPath $HashPath -Encoding ascii

    $Response = [ordered]@{
        schema_version = 1
        status = 'ready'
        version = $Version
        zip = $ArchivePath
        sha256 = $Hash
        sha256_file = $HashPath
        file_count = $FilesByRelativePath.Count
        exclusions = @(
            '.models',
            '.runtime',
            '.git',
            'caches',
            'samples\real',
            'logs',
            'user output'
        )
    }
    [Console]::Out.WriteLine(($Response | ConvertTo-Json -Compress -Depth 5))
    exit 0
}
catch {
    if (Test-Path -LiteralPath $PartialPath -PathType Leaf) {
        Remove-Item -LiteralPath $PartialPath -Force
    }
    $Failure = [ordered]@{
        schema_version = 1
        status = 'error'
        error = $_.Exception.Message
    }
    [Console]::Out.WriteLine(($Failure | ConvertTo-Json -Compress))
    exit 1
}
