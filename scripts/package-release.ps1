param(
    [string]$Version = '1.0.0',
    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$ArchivePath = $null
$PartialPath = $null
$HashPath = $null
$HashPartialPath = $null

# A release is a reproducible projection of the current Git HEAD.  It is not a
# copy of whatever happens to be present in the working directory.
$AllowedRootFiles = @(
    '.gitattributes',
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
    'requirements.lock',
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
$RequiredPaths = @(
    '.gitattributes',
    '.gitignore',
    '.github/workflows/tests.yml',
    'CHANGELOG.md',
    'LICENSE',
    'PRIVACY.md',
    'README.md',
    'SECURITY.md',
    'SKILL.md',
    'THIRD_PARTY_NOTICES.md',
    'docs/ARCHITECTURE.md',
    'docs/ARTICLE_DRAFT.md',
    'docs/BENCHMARK.md',
    'docs/COMPETITION_COMPLIANCE.md',
    'docs/DEMO_SCRIPT.md',
    'docs/LIMITATIONS.md',
    'docs/MODEL_AND_RUNTIME.md',
    'docs/NEXT_STEPS.md',
    'docs/QODER_VALIDATION.md',
    'docs/SUBMISSION_CHECKLIST.md',
    'docs/USER_GUIDE.md',
    'info.json',
    'meta.json',
    'pyproject.toml',
    'requirements.lock',
    'requirements.txt',
    'samples/real/.gitkeep',
    'samples/real/README.md',
    'scripts/run.ps1'
)
$AllowedRealSampleFiles = @(
    'samples/real/.gitkeep',
    'samples/real/README.md'
)
$ExcludedSegments = @(
    '.git',
    '.models',
    '.runtime',
    '.tools',
    '.venv',
    '.aws',
    '.azure',
    '.ssh',
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    '.tox',
    '.nox',
    '.hypothesis',
    'benchmark-output',
    'demo-output',
    '.peg-output',
    'release',
    'logs',
    'build',
    'dist',
    'htmlcov'
)
$DeniedLeafPatterns = @(
    '*.pyc',
    '*.pyo',
    '*.partial',
    '*.tmp',
    '*.temp',
    '*.log',
    '*.trace',
    '*.dmp',
    '*.core',
    '*.bak',
    '*.pem',
    '*.key',
    '*.pfx',
    '*.p12',
    '*.ppk',
    '*.kdbx',
    '*.sqlite',
    '*.sqlite3',
    '*.db',
    '*.jsonl',
    '.env',
    '.env.*',
    '.netrc',
    '.npmrc',
    '.pypirc',
    'auth.json',
    'credentials.json',
    'secrets.json',
    'id_rsa',
    'id_ed25519',
    'pending-request.json',
    'server-state.json',
    'server.pid',
    'server.lock'
)
$DeniedBinaryExtensions = @(
    '.7z',
    '.bin',
    '.ckpt',
    '.dll',
    '.dylib',
    '.engine',
    '.exe',
    '.gguf',
    '.gz',
    '.mlmodel',
    '.model',
    '.onnx',
    '.pkl',
    '.pt',
    '.pth',
    '.rar',
    '.safetensors',
    '.so',
    '.tar',
    '.tflite',
    '.whl',
    '.xml',
    '.zip'
)
$ControlledMediaExtensions = @(
    '.bmp',
    '.docx',
    '.jpeg',
    '.jpg',
    '.pdf',
    '.png',
    '.webp',
    '.xlsx'
)
$MaximumFileBytes = 25MB
$MaximumArchiveInputBytes = 100MB
$MaximumFileCount = 2000
$PathComparison = if (
    [System.Environment]::OSVersion.Platform -eq
    [System.PlatformID]::Win32NT
) {
    [System.StringComparison]::OrdinalIgnoreCase
}
else {
    [System.StringComparison]::Ordinal
}

function Test-IsSameOrChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Directory
    )

    $CandidateFull = [System.IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/')
    $DirectoryFull = [System.IO.Path]::GetFullPath($Directory).TrimEnd('\', '/')
    if ($CandidateFull.Equals($DirectoryFull, $PathComparison)) {
        return $true
    }
    $Prefix = $DirectoryFull + [System.IO.Path]::DirectorySeparatorChar
    return $CandidateFull.StartsWith($Prefix, $PathComparison)
}

function Invoke-GitText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $Output = @(
        & $script:GitExecutable `
            -c core.excludesFile= `
            -c core.quotepath=false `
            @Arguments 2>&1 |
            ForEach-Object { $_.ToString() }
    )
    if ($LASTEXITCODE -ne 0) {
        $Detail = ($Output -join "`n").Trim()
        if ([string]::IsNullOrWhiteSpace($Detail)) {
            $Detail = "exit code $LASTEXITCODE"
        }
        throw "Git 命令失败（git $($Arguments -join ' ')）：$Detail"
    }
    return $Output
}

function Get-GitQuietExitCode {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & $script:GitExecutable `
        -c core.excludesFile= `
        -c core.quotepath=false `
        @Arguments *> $null
    return $LASTEXITCODE
}

function Assert-SafeGitPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    if ([string]::IsNullOrWhiteSpace($RelativePath)) {
        throw 'Git 树包含空路径。'
    }
    if (
        [System.IO.Path]::IsPathRooted($RelativePath) -or
        $RelativePath.StartsWith('/') -or
        $RelativePath.StartsWith('\') -or
        $RelativePath -match '^[A-Za-z]:' -or
        $RelativePath.Contains('\')
    ) {
        throw "Git 树包含绝对路径或非规范路径：$RelativePath"
    }
    if ($RelativePath -match '[\x00-\x1f\x7f]') {
        throw "Git 树路径包含控制字符：$RelativePath"
    }
    if (-not $RelativePath.IsNormalized(
        [System.Text.NormalizationForm]::FormC
    )) {
        throw "Git 树路径不是 Unicode NFC 规范形式：$RelativePath"
    }
    foreach ($Segment in $RelativePath.Split('/')) {
        if (
            [string]::IsNullOrWhiteSpace($Segment) -or
            $Segment -eq '.' -or
            $Segment -eq '..'
        ) {
            throw "Git 树包含越界或空路径段：$RelativePath"
        }
    }
}

function Test-IsAllowedScope {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    foreach ($RootFile in $AllowedRootFiles) {
        if ($RelativePath.Equals(
            $RootFile,
            [System.StringComparison]::Ordinal
        )) {
            return $true
        }
    }
    foreach ($Directory in $AllowedDirectories) {
        $Prefix = "$Directory/"
        if ($RelativePath.StartsWith(
            $Prefix,
            [System.StringComparison]::Ordinal
        )) {
            return $true
        }
    }
    return $false
}

function Assert-AllowedReleasePath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    if ($RelativePath.StartsWith(
        'samples/real/',
        [System.StringComparison]::Ordinal
    )) {
        $Allowed = $false
        foreach ($AllowedRealPath in $AllowedRealSampleFiles) {
            if ($RelativePath.Equals(
                $AllowedRealPath,
                [System.StringComparison]::Ordinal
            )) {
                $Allowed = $true
                break
            }
        }
        if (-not $Allowed) {
            throw (
                'samples/real 仅允许 README.md 与 .gitkeep，发现：' +
                $RelativePath
            )
        }
    }

    $Segments = $RelativePath.Split('/')
    foreach ($Segment in $Segments) {
        foreach ($Excluded in $ExcludedSegments) {
            if ($Segment.Equals(
                $Excluded,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                throw "发布路径命中禁止目录：$RelativePath"
            }
        }
        if (
            $Segment.EndsWith(
                '.partial',
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "发布路径命中临时目录：$RelativePath"
        }
    }

    $Leaf = $Segments[$Segments.Length - 1]
    foreach ($Pattern in $DeniedLeafPatterns) {
        $Wildcard = [System.Management.Automation.WildcardPattern]::new(
            $Pattern,
            [System.Management.Automation.WildcardOptions]::IgnoreCase
        )
        if ($Wildcard.IsMatch($Leaf)) {
            throw "发布路径命中敏感文件规则 $Pattern：$RelativePath"
        }
    }

    $Extension = [System.IO.Path]::GetExtension($Leaf)
    foreach ($DeniedExtension in $DeniedBinaryExtensions) {
        if ($Extension.Equals(
            $DeniedExtension,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "发布路径命中模型、可执行或归档扩展名：$RelativePath"
        }
    }
    foreach ($MediaExtension in $ControlledMediaExtensions) {
        if ($Extension.Equals(
            $MediaExtension,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            $IsGeneratedFixture = $RelativePath.StartsWith(
                'samples/generated-benchmark/',
                [System.StringComparison]::Ordinal
            )
            $IsDocumentationAsset = $RelativePath.StartsWith(
                'docs/assets/',
                [System.StringComparison]::Ordinal
            )
            if (-not ($IsGeneratedFixture -or $IsDocumentationAsset)) {
                throw "媒体或办公文件不在受控资产目录：$RelativePath"
            }
        }
    }
}

function Assert-NoReparsePoint {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Context
    )

    $Item = Get-Item -LiteralPath $Path -Force
    if (
        ($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "$Context 包含符号链接或 reparse point：$Path"
    }
    $LinkTypeProperty = $Item.PSObject.Properties['LinkType']
    if (
        $null -ne $LinkTypeProperty -and
        -not [string]::IsNullOrWhiteSpace([string]$LinkTypeProperty.Value) -and
        [string]$LinkTypeProperty.Value -ne 'HardLink'
    ) {
        throw "$Context 包含链接：$Path"
    }
}

function Assert-NoLinkedAncestors {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$StopDirectory,
        [Parameter(Mandatory = $true)][string]$Context
    )

    $Current = [System.IO.Path]::GetFullPath($Path)
    $Stop = [System.IO.Path]::GetFullPath($StopDirectory).TrimEnd('\', '/')
    while ($true) {
        Assert-NoReparsePoint -Path $Current -Context $Context
        $CurrentTrimmed = $Current.TrimEnd('\', '/')
        if ($CurrentTrimmed.Equals($Stop, $PathComparison)) {
            break
        }
        $Parent = [System.IO.Path]::GetDirectoryName($CurrentTrimmed)
        if (
            [string]::IsNullOrWhiteSpace($Parent) -or
            -not (Test-IsSameOrChildPath -Candidate $Parent -Directory $Stop)
        ) {
            throw "$Context 路径越出预期根目录：$Path"
        }
        $Current = $Parent
    }
}

function Assert-SingleHardLink {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (
        [System.Environment]::OSVersion.Platform -eq
        [System.PlatformID]::Win32NT
    ) {
        $Fsutil = Get-Command 'fsutil.exe' -ErrorAction SilentlyContinue
        if ($null -eq $Fsutil) {
            throw "无法验证硬链接数量（缺少 fsutil.exe）：$Path"
        }
        $Links = @(& $Fsutil.Source hardlink list $Path 2>$null)
        if ($LASTEXITCODE -ne 0 -or $Links.Count -lt 1) {
            throw "无法验证硬链接数量：$Path"
        }
        if ($Links.Count -ne 1) {
            throw "发布文件是硬链接：$Path"
        }
        return
    }

    $Stat = Get-Command 'stat' -ErrorAction SilentlyContinue
    if ($null -eq $Stat) {
        throw "无法验证硬链接数量（缺少 stat）：$Path"
    }
    $CountText = @(& $Stat.Source -c '%h' -- $Path 2>$null)
    if ($LASTEXITCODE -ne 0 -or $CountText.Count -ne 1) {
        $CountText = @(& $Stat.Source -f '%l' -- $Path 2>$null)
    }
    $LinkCount = 0L
    if (
        $LASTEXITCODE -ne 0 -or
        $CountText.Count -ne 1 -or
        -not [long]::TryParse(
            $CountText[0].ToString().Trim(),
            [ref]$LinkCount
        )
    ) {
        throw "无法验证硬链接数量：$Path"
    }
    if ($LinkCount -ne 1) {
        throw "发布文件是硬链接：$Path"
    }
}

function Assert-SafeOutputDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    foreach ($Directory in $AllowedDirectories) {
        $AllowedPath = Join-Path $script:RepositoryRoot $Directory
        if (Test-IsSameOrChildPath -Candidate $Path -Directory $AllowedPath) {
            throw "输出目录不能位于发布输入白名单目录中：$Path"
        }
    }

    $Probe = [System.IO.Path]::GetFullPath($Path)
    while (-not (Test-Path -LiteralPath $Probe)) {
        $Parent = [System.IO.Path]::GetDirectoryName(
            $Probe.TrimEnd('\', '/')
        )
        if ([string]::IsNullOrWhiteSpace($Parent) -or $Parent -eq $Probe) {
            throw "无法解析输出目录：$Path"
        }
        $Probe = $Parent
    }
    while (-not [string]::IsNullOrWhiteSpace($Probe)) {
        Assert-NoReparsePoint -Path $Probe -Context '输出目录'
        $Parent = [System.IO.Path]::GetDirectoryName(
            $Probe.TrimEnd('\', '/')
        )
        if ([string]::IsNullOrWhiteSpace($Parent) -or $Parent -eq $Probe) {
            break
        }
        $Probe = $Parent
    }
}

function Assert-ReplaceableOutputFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "输出目标不是普通文件：$Path"
    }
    Assert-NoReparsePoint -Path $Path -Context '输出目标'
    Assert-SingleHardLink -Path $Path
}

function Get-GitBlobBytes {
    param([Parameter(Mandatory = $true)][string]$ObjectId)

    if ($ObjectId -notmatch '^[0-9a-fA-F]{40,64}$') {
        throw "无效的 Git 对象 ID：$ObjectId"
    }
    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $script:GitExecutable
    $StartInfo.Arguments = "cat-file blob $ObjectId"
    $StartInfo.WorkingDirectory = $script:RepositoryRoot
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true

    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    $Memory = [System.IO.MemoryStream]::new()
    try {
        if (-not $Process.Start()) {
            throw "无法启动 git cat-file：$ObjectId"
        }
        $Process.StandardOutput.BaseStream.CopyTo($Memory)
        $ErrorText = $Process.StandardError.ReadToEnd()
        $Process.WaitForExit()
        if ($Process.ExitCode -ne 0) {
            throw "读取 Git 对象失败 $ObjectId：$($ErrorText.Trim())"
        }
        return ,$Memory.ToArray()
    }
    finally {
        $Memory.Dispose()
        $Process.Dispose()
    }
}

function Get-FileSha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Stream = $null
    $Hasher = $null
    try {
        $Stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $Hasher = [System.Security.Cryptography.SHA256]::Create()
        $Digest = $Hasher.ComputeHash($Stream)
        return (
            [System.BitConverter]::ToString($Digest)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        if ($null -ne $Hasher) {
            $Hasher.Dispose()
        }
        if ($null -ne $Stream) {
            $Stream.Dispose()
        }
    }
}

function Write-UInt16LittleEndian {
    param(
        [Parameter(Mandatory = $true)][System.IO.Stream]$Stream,
        [Parameter(Mandatory = $true)][uint16]$Value
    )

    $Bytes = [System.BitConverter]::GetBytes($Value)
    if (-not [System.BitConverter]::IsLittleEndian) {
        [System.Array]::Reverse($Bytes)
    }
    $Stream.Write($Bytes, 0, $Bytes.Length)
}

function Write-UInt32LittleEndian {
    param(
        [Parameter(Mandatory = $true)][System.IO.Stream]$Stream,
        [Parameter(Mandatory = $true)][uint32]$Value
    )

    $Bytes = [System.BitConverter]::GetBytes($Value)
    if (-not [System.BitConverter]::IsLittleEndian) {
        [System.Array]::Reverse($Bytes)
    }
    $Stream.Write($Bytes, 0, $Bytes.Length)
}

function Write-DeterministicStoredZip {
    param(
        [Parameter(Mandatory = $true)][object[]]$Entries,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if ($null -eq ('ReleasePackageCrc32' -as [type])) {
        Add-Type -TypeDefinition @'
using System;

public static class ReleasePackageCrc32
{
    private static readonly UInt32[] Table = BuildTable();

    private static UInt32[] BuildTable()
    {
        UInt32[] table = new UInt32[256];
        for (UInt32 i = 0; i < table.Length; i++)
        {
            UInt32 value = i;
            for (int bit = 0; bit < 8; bit++)
            {
                value = (value & 1) == 1
                    ? 0xEDB88320U ^ (value >> 1)
                    : value >> 1;
            }
            table[i] = value;
        }
        return table;
    }

    public static UInt32 Compute(byte[] data)
    {
        UInt32 value = 0xFFFFFFFFU;
        foreach (byte item in data)
        {
            value = Table[(value ^ item) & 0xFF] ^ (value >> 8);
        }
        return value ^ 0xFFFFFFFFU;
    }
}
'@
    }

    $Utf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $CentralRecords = [System.Collections.Generic.List[object]]::new()
    $Stream = [System.IO.File]::Open(
        $Destination,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        foreach ($Entry in $Entries) {
            $NameBytes = $Utf8.GetBytes([string]$Entry.Path)
            if ($NameBytes.Length -gt [uint16]::MaxValue) {
                throw "ZIP 条目名称过长：$($Entry.Path)"
            }
            $Data = Get-GitBlobBytes -ObjectId $Entry.ObjectId
            if ($Data.LongLength -ne $Entry.Size) {
                throw "Git 对象大小在打包期间发生异常：$($Entry.Path)"
            }
            if ($Data.LongLength -gt [uint32]::MaxValue) {
                throw "文件超过非 ZIP64 格式上限：$($Entry.Path)"
            }
            if ($Stream.Position -gt [uint32]::MaxValue) {
                throw '归档超过非 ZIP64 格式上限。'
            }

            $Crc32 = [ReleasePackageCrc32]::Compute($Data)
            $Offset = [uint32]$Stream.Position
            $UnixModeBits = if ($Entry.Mode -eq '100755') {
                [uint32]0x81ED
            }
            else {
                [uint32]0x81A4
            }
            $ExternalAttributes = [uint32](
                [uint64]$UnixModeBits * [uint64]65536
            )

            Write-UInt32LittleEndian -Stream $Stream -Value 0x04034b50
            Write-UInt16LittleEndian -Stream $Stream -Value 20
            Write-UInt16LittleEndian -Stream $Stream -Value 0x0800
            Write-UInt16LittleEndian -Stream $Stream -Value 0
            Write-UInt16LittleEndian -Stream $Stream -Value 0
            Write-UInt16LittleEndian -Stream $Stream -Value 0x5021
            Write-UInt32LittleEndian -Stream $Stream -Value $Crc32
            Write-UInt32LittleEndian -Stream $Stream -Value ([uint32]$Data.Length)
            Write-UInt32LittleEndian -Stream $Stream -Value ([uint32]$Data.Length)
            Write-UInt16LittleEndian -Stream $Stream -Value (
                [uint16]$NameBytes.Length
            )
            Write-UInt16LittleEndian -Stream $Stream -Value 0
            $Stream.Write($NameBytes, 0, $NameBytes.Length)
            $Stream.Write($Data, 0, $Data.Length)

            $CentralRecords.Add([pscustomobject]@{
                NameBytes = $NameBytes
                Crc32 = $Crc32
                Size = [uint32]$Data.Length
                Offset = $Offset
                ExternalAttributes = $ExternalAttributes
            })
        }

        if ($CentralRecords.Count -gt [uint16]::MaxValue) {
            throw '文件数量超过非 ZIP64 格式上限。'
        }
        $CentralOffset = $Stream.Position
        foreach ($Record in $CentralRecords) {
            Write-UInt32LittleEndian -Stream $Stream -Value 0x02014b50
            # ZIP "version made by": Unix host (3), specification 2.0 (20).
            Write-UInt16LittleEndian -Stream $Stream -Value 0x0314
            Write-UInt16LittleEndian -Stream $Stream -Value 20
            Write-UInt16LittleEndian -Stream $Stream -Value 0x0800
            Write-UInt16LittleEndian -Stream $Stream -Value 0
            Write-UInt16LittleEndian -Stream $Stream -Value 0
            Write-UInt16LittleEndian -Stream $Stream -Value 0x5021
            Write-UInt32LittleEndian -Stream $Stream -Value $Record.Crc32
            Write-UInt32LittleEndian -Stream $Stream -Value $Record.Size
            Write-UInt32LittleEndian -Stream $Stream -Value $Record.Size
            Write-UInt16LittleEndian -Stream $Stream -Value (
                [uint16]$Record.NameBytes.Length
            )
            Write-UInt16LittleEndian -Stream $Stream -Value 0
            Write-UInt16LittleEndian -Stream $Stream -Value 0
            Write-UInt16LittleEndian -Stream $Stream -Value 0
            Write-UInt16LittleEndian -Stream $Stream -Value 0
            Write-UInt32LittleEndian `
                -Stream $Stream `
                -Value $Record.ExternalAttributes
            Write-UInt32LittleEndian -Stream $Stream -Value $Record.Offset
            $Stream.Write(
                $Record.NameBytes,
                0,
                $Record.NameBytes.Length
            )
        }
        $CentralSize = $Stream.Position - $CentralOffset
        if (
            $CentralOffset -gt [uint32]::MaxValue -or
            $CentralSize -gt [uint32]::MaxValue
        ) {
            throw '中央目录超过非 ZIP64 格式上限。'
        }

        Write-UInt32LittleEndian -Stream $Stream -Value 0x06054b50
        Write-UInt16LittleEndian -Stream $Stream -Value 0
        Write-UInt16LittleEndian -Stream $Stream -Value 0
        Write-UInt16LittleEndian -Stream $Stream -Value (
            [uint16]$CentralRecords.Count
        )
        Write-UInt16LittleEndian -Stream $Stream -Value (
            [uint16]$CentralRecords.Count
        )
        Write-UInt32LittleEndian -Stream $Stream -Value ([uint32]$CentralSize)
        Write-UInt32LittleEndian -Stream $Stream -Value (
            [uint32]$CentralOffset
        )
        Write-UInt16LittleEndian -Stream $Stream -Value 0
        $Stream.Flush()
    }
    finally {
        $Stream.Dispose()
    }
}

try {
    if ($Version -notmatch '^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$') {
        throw '版本号必须是安全的 SemVer 字符串。'
    }

    $script:RepositoryRoot = (
        Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')
    ).Path
    $GitCommand = Get-Command 'git' -ErrorAction SilentlyContinue
    if ($null -eq $GitCommand) {
        throw '发布需要 Git，但当前环境找不到 git。'
    }
    $script:GitExecutable = $GitCommand.Source

    $TopLevelLines = @(
        Invoke-GitText -Arguments @('rev-parse', '--show-toplevel')
    )
    if ($TopLevelLines.Count -ne 1) {
        throw '无法唯一确定 Git 仓库根目录。'
    }
    $GitTopLevel = (Resolve-Path -LiteralPath $TopLevelLines[0]).Path
    if (-not $GitTopLevel.Equals($RepositoryRoot, $PathComparison)) {
        throw "脚本目录不是当前 Git 仓库根目录：$GitTopLevel"
    }

    $HeadLines = @(
        Invoke-GitText -Arguments @('rev-parse', '--verify', 'HEAD^{commit}')
    )
    if ($HeadLines.Count -ne 1 -or $HeadLines[0] -notmatch '^[0-9a-fA-F]{40,64}$') {
        throw '当前仓库没有可发布的 HEAD commit。'
    }
    $HeadCommit = $HeadLines[0].ToLowerInvariant()

    $IndexExit = Get-GitQuietExitCode -Arguments @(
        'diff',
        '--cached',
        '--quiet',
        '--exit-code',
        'HEAD',
        '--'
    )
    if ($IndexExit -eq 1) {
        throw 'Git 索引与 HEAD 不一致；请先提交或还原 staged 改动。'
    }
    if ($IndexExit -ne 0) {
        throw "无法验证 Git 索引与 HEAD（exit code $IndexExit）。"
    }

    $WorktreeExit = Get-GitQuietExitCode -Arguments @(
        'diff',
        '--quiet',
        '--exit-code',
        '--'
    )
    if ($WorktreeExit -eq 1) {
        throw '工作目录包含 tracked 改动；发布只接受干净的 HEAD。'
    }
    if ($WorktreeExit -ne 0) {
        throw "无法验证工作目录状态（exit code $WorktreeExit）。"
    }

    $Untracked = @(
        Invoke-GitText -Arguments @(
            'ls-files',
            '--others',
            '--exclude-standard',
            '--'
        )
    )
    $Untracked = @($Untracked | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_)
    })
    if ($Untracked.Count -gt 0) {
        $Preview = ($Untracked | Select-Object -First 5) -join ', '
        throw "工作目录包含未跟踪且未忽略的文件：$Preview"
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
    Assert-SafeOutputDirectory -Path $ReleaseRoot

    $ArchiveName = "local-product-evidence-guard-v$Version.zip"
    $ArchivePath = Join-Path $ReleaseRoot $ArchiveName
    $PartialPath = "$ArchivePath.partial"
    $HashPath = "$ArchivePath.sha256"
    $HashPartialPath = "$HashPath.partial"

    $TreeLines = @(
        Invoke-GitText -Arguments @(
            'ls-tree',
            '-r',
            '--full-tree',
            'HEAD',
            '--'
        )
    )
    if ($TreeLines.Count -eq 0) {
        throw 'HEAD 的 Git 树为空。'
    }

    $TrackedPaths = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    $CaseFoldedPaths = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    $Entries = [System.Collections.Generic.List[object]]::new()
    $ObjectSizes = @{}
    $TotalBytes = 0L

    foreach ($Line in $TreeLines) {
        if (
            $Line -notmatch (
                '^(?<mode>[0-9]{6})\s+' +
                '(?<type>[^\s]+)\s+' +
                '(?<oid>[0-9a-fA-F]{40,64})\t' +
                '(?<path>.*)$'
            )
        ) {
            throw "无法解析 Git 树条目：$Line"
        }
        $Mode = $Matches['mode']
        $ObjectType = $Matches['type']
        $ObjectId = $Matches['oid'].ToLowerInvariant()
        $RelativePath = $Matches['path']
        Assert-SafeGitPath -RelativePath $RelativePath
        $null = $TrackedPaths.Add($RelativePath)

        if (-not (Test-IsAllowedScope -RelativePath $RelativePath)) {
            continue
        }
        if (-not $CaseFoldedPaths.Add($RelativePath)) {
            throw "发布路径存在大小写冲突：$RelativePath"
        }
        if ($Mode -eq '120000') {
            throw "发布范围包含 Git 符号链接：$RelativePath"
        }
        if (
            ($Mode -ne '100644' -and $Mode -ne '100755') -or
            $ObjectType -ne 'blob'
        ) {
            throw "发布范围包含非普通文件：$RelativePath ($Mode $ObjectType)"
        }
        Assert-AllowedReleasePath -RelativePath $RelativePath

        if ($ObjectSizes.ContainsKey($ObjectId)) {
            $Size = [long]$ObjectSizes[$ObjectId]
        }
        else {
            $SizeLines = @(
                Invoke-GitText -Arguments @('cat-file', '-s', $ObjectId)
            )
            $Size = 0L
            if (
                $SizeLines.Count -ne 1 -or
                -not [long]::TryParse($SizeLines[0].Trim(), [ref]$Size) -or
                $Size -lt 0
            ) {
                throw "无法读取 Git 对象大小：$RelativePath"
            }
            $ObjectSizes[$ObjectId] = $Size
        }
        if ($Size -gt $MaximumFileBytes) {
            throw (
                "单文件超过 $MaximumFileBytes 字节上限：" +
                "$RelativePath ($Size bytes)"
            )
        }
        $TotalBytes += $Size
        if ($TotalBytes -gt $MaximumArchiveInputBytes) {
            throw "发布输入超过 $MaximumArchiveInputBytes 字节总上限。"
        }

        $WorkingPath = Join-Path $RepositoryRoot (
            $RelativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
        )
        $FullWorkingPath = [System.IO.Path]::GetFullPath($WorkingPath)
        if (-not (
            Test-IsSameOrChildPath `
                -Candidate $FullWorkingPath `
                -Directory $RepositoryRoot
        )) {
            throw "发布文件越出仓库：$RelativePath"
        }
        if (-not (Test-Path -LiteralPath $FullWorkingPath -PathType Leaf)) {
            throw "HEAD 文件未正常检出到工作目录：$RelativePath"
        }
        Assert-NoLinkedAncestors `
            -Path $FullWorkingPath `
            -StopDirectory $RepositoryRoot `
            -Context '发布输入'
        Assert-SingleHardLink -Path $FullWorkingPath

        $Entries.Add([pscustomobject]@{
            Path = $RelativePath
            ObjectId = $ObjectId
            Size = $Size
            Mode = $Mode
        })
        if ($Entries.Count -gt $MaximumFileCount) {
            throw "发布文件数超过 $MaximumFileCount 上限。"
        }
    }

    foreach ($RequiredPath in $RequiredPaths) {
        if (-not $TrackedPaths.Contains($RequiredPath)) {
            throw "HEAD 缺少必备发布文件：$RequiredPath"
        }
    }
    if ($Entries.Count -eq 0) {
        throw '发布白名单中没有任何 HEAD 文件。'
    }

    $CurrentHead = @(
        Invoke-GitText -Arguments @('rev-parse', '--verify', 'HEAD^{commit}')
    )[0].ToLowerInvariant()
    if ($CurrentHead -ne $HeadCommit) {
        throw 'HEAD 在发布校验期间发生变化，请重新运行。'
    }
    if (
        (Get-GitQuietExitCode -Arguments @(
            'diff',
            '--cached',
            '--quiet',
            '--exit-code',
            'HEAD',
            '--'
        )) -ne 0 -or
        (Get-GitQuietExitCode -Arguments @(
            'diff',
            '--quiet',
            '--exit-code',
            '--'
        )) -ne 0
    ) {
        throw '索引或工作目录在发布校验期间发生变化，请重新运行。'
    }

    $OrderedPaths = [string[]]@($Entries | ForEach-Object { $_.Path })
    [System.Array]::Sort(
        $OrderedPaths,
        [System.StringComparer]::Ordinal
    )
    $EntryByPath = @{}
    foreach ($Entry in $Entries) {
        $EntryByPath[$Entry.Path] = $Entry
    }
    $OrderedEntries = [System.Collections.Generic.List[object]]::new()
    foreach ($OrderedPath in $OrderedPaths) {
        $OrderedEntries.Add($EntryByPath[$OrderedPath])
    }

    New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null
    Assert-NoReparsePoint -Path $ReleaseRoot -Context '输出目录'
    foreach ($Target in @(
        $PartialPath,
        $HashPartialPath,
        $ArchivePath,
        $HashPath
    )) {
        Assert-ReplaceableOutputFile -Path $Target
    }
    foreach ($Target in @($PartialPath, $HashPartialPath)) {
        if (Test-Path -LiteralPath $Target -PathType Leaf) {
            Remove-Item -LiteralPath $Target -Force
        }
    }

    Write-DeterministicStoredZip `
        -Entries $OrderedEntries.ToArray() `
        -Destination $PartialPath
    $Hash = Get-FileSha256Hex -Path $PartialPath
    [System.IO.File]::WriteAllText(
        $HashPartialPath,
        "$Hash  $ArchiveName`n",
        [System.Text.ASCIIEncoding]::new()
    )

    foreach ($Target in @($ArchivePath, $HashPath)) {
        if (Test-Path -LiteralPath $Target -PathType Leaf) {
            Remove-Item -LiteralPath $Target -Force
        }
    }
    Move-Item -LiteralPath $PartialPath -Destination $ArchivePath
    Move-Item -LiteralPath $HashPartialPath -Destination $HashPath

    $Response = [ordered]@{
        schema_version = 1
        status = 'ready'
        version = $Version
        commit = $HeadCommit
        source = 'clean HEAD tracked files'
        zip = $ArchivePath
        sha256 = $Hash
        sha256_file = $HashPath
        file_count = $Entries.Count
        input_bytes = $TotalBytes
        archive_format = 'deterministic ZIP/store'
        exclusions = @(
            '.models',
            '.runtime',
            '.git',
            'caches',
            'samples/real user material (README.md/.gitkeep only)',
            'logs',
            'models and credentials',
            'user output'
        )
    }
    [Console]::Out.WriteLine(($Response | ConvertTo-Json -Compress -Depth 5))
    exit 0
}
catch {
    foreach ($TemporaryPath in @($PartialPath, $HashPartialPath)) {
        if (
            -not [string]::IsNullOrWhiteSpace($TemporaryPath) -and
            (Test-Path -LiteralPath $TemporaryPath -PathType Leaf)
        ) {
            Remove-Item -LiteralPath $TemporaryPath -Force
        }
    }
    $Failure = [ordered]@{
        schema_version = 1
        status = 'error'
        error = $_.Exception.Message
    }
    [Console]::Out.WriteLine(($Failure | ConvertTo-Json -Compress))
    exit 1
}
