param(
    [ValidateSet('User', 'Project')]
    [string]$Scope = 'User',
    [string]$ProjectRoot = '',
    [switch]$Update
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$skillName = 'local-product-evidence-guard'
$pathComparison = if (
    [System.Environment]::OSVersion.Platform -eq
    [System.PlatformID]::Win32NT
) {
    [System.StringComparison]::OrdinalIgnoreCase
}
else {
    [System.StringComparison]::Ordinal
}

# Only these three anonymous, tracked demo inputs may be installed. Other
# samples, arbitrary working-tree files, model data, logs, and output remain out.
$demoSampleFiles = @(
    'samples/demo/说明书.txt',
    'samples/demo/参数表.csv',
    'samples/demo/包装正面.jpg.ocr.json'
)

# Every executable/runtime file in this list is required. A partial Python
# package must fail closed instead of being installed and failing much later.
$requiredRuntimeFiles = @(
    'product_evidence_guard/__init__.py',
    'product_evidence_guard/__main__.py',
    'product_evidence_guard/cli.py',
    'product_evidence_guard/confirmation.py',
    'product_evidence_guard/engine.py',
    'product_evidence_guard/extractor.py',
    'product_evidence_guard/graph.py',
    'product_evidence_guard/model_output_schema.py',
    'product_evidence_guard/models.py',
    'product_evidence_guard/normalization.py',
    'product_evidence_guard/openvino_adapter.py',
    'product_evidence_guard/parsers.py',
    'product_evidence_guard/qwen_vl_reader.py',
    'product_evidence_guard/reports.py',
    'product_evidence_guard/state.py',
    'scripts/benchmark.py',
    'scripts/client.py',
    'scripts/install-env.ps1',
    'scripts/install-qoder-skill.ps1',
    'scripts/model_download.py',
    'scripts/package-release.ps1',
    'scripts/protocol.py',
    'scripts/run.ps1',
    'scripts/run-demo.ps1',
    'scripts/run-demo.sh',
    'scripts/server.py'
)
$requiredFiles = @(
    'SKILL.md',
    'info.json',
    'meta.json',
    'requirements.txt',
    'requirements.lock',
    'pyproject.toml',
    'README.md',
    'LICENSE',
    'CHANGELOG.md',
    'docs/ARCHITECTURE.md',
    'docs/NEXT_STEPS.md'
) + $requiredRuntimeFiles + $demoSampleFiles

# This is a file allowlist, not a directory mirror. Optional notices and
# documentation are copied only when they are tracked in a Git working tree.
$allowedFiles = $requiredFiles + @(
    'THIRD_PARTY_NOTICES.md',
    'PRIVACY.md',
    'SECURITY.md',
    'docs/ARTICLE_DRAFT.md',
    'docs/BENCHMARK.md',
    'docs/COMPETITION_COMPLIANCE.md',
    'docs/DEMO_SCRIPT.md',
    'docs/LIMITATIONS.md',
    'docs/MODEL_AND_RUNTIME.md',
    'docs/QODER_VALIDATION.md',
    'docs/SUBMISSION_CHECKLIST.md',
    'docs/USER_GUIDE.md'
)
$deniedSegments = @(
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
    'output',
    'outputs',
    'build',
    'dist',
    'htmlcov'
)
$deniedLeafPatterns = @(
    '*.pyc',
    '*.pyo',
    '*.log',
    '*.trace',
    '*.tmp',
    '*.temp',
    '*.partial',
    '*.pem',
    '*.key',
    '*.pfx',
    '*.p12',
    '*.ppk',
    '*.safetensors',
    '*.onnx',
    '*.gguf',
    '*.bin',
    '*.xml',
    '*.engine',
    '*.mlmodel',
    '.env',
    '.env.*',
    'auth.json',
    'credentials.json',
    'secrets.json',
    'pending-request.json',
    'server-state.json',
    'server.pid',
    'server.lock'
)
$preservedRuntimeDirectories = @('.models', '.venv', '.tools', '.runtime')
$maximumFileBytes = 5MB
$maximumTotalBytes = 25MB

function Test-IsSameOrChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Directory
    )

    $candidateFull = [System.IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/')
    $directoryFull = [System.IO.Path]::GetFullPath($Directory).TrimEnd('\', '/')
    if ($candidateFull.Equals($directoryFull, $pathComparison)) {
        return $true
    }
    $prefix = $directoryFull + [System.IO.Path]::DirectorySeparatorChar
    return $candidateFull.StartsWith($prefix, $pathComparison)
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

function Get-RelativeSegments {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Boundary
    )

    $candidateFull = [System.IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/')
    $boundaryFull = [System.IO.Path]::GetFullPath($Boundary).TrimEnd('\', '/')
    if (-not (Test-IsSameOrChildPath -Candidate $candidateFull -Directory $boundaryFull)) {
        throw "Qoder 路径越出安装边界：$candidateFull；边界：$boundaryFull"
    }
    if ($candidateFull.Equals($boundaryFull, $pathComparison)) {
        return @()
    }
    $relative = $candidateFull.Substring($boundaryFull.Length).TrimStart('\', '/')
    return @(
        $relative.Split(
            [char[]]@(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar
            ),
            [System.StringSplitOptions]::RemoveEmptyEntries
        )
    )
}

function Assert-SafePathChain {
    param(
        [Parameter(Mandatory = $true)][string]$Boundary,
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Context
    )

    $boundaryFull = [System.IO.Path]::GetFullPath($Boundary).TrimEnd('\', '/')
    if (-not (Test-Path -LiteralPath $boundaryFull -PathType Container)) {
        throw "$Context 的安装边界不是目录：$boundaryFull"
    }
    Assert-NoReparsePoint -Path $boundaryFull -Context $Context

    $current = $boundaryFull
    foreach ($segment in (Get-RelativeSegments -Candidate $Candidate -Boundary $boundaryFull)) {
        $current = Join-Path $current $segment
        if (Test-Path -LiteralPath $current) {
            Assert-NoReparsePoint -Path $current -Context $Context
            if (
                -not $current.TrimEnd('\', '/').Equals(
                    [System.IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/'),
                    $pathComparison
                ) -and
                -not (Test-Path -LiteralPath $current -PathType Container)
            ) {
                throw "$Context 的祖先不是目录：$current"
            }
        }
    }

    if (-not (Test-IsSameOrChildPath -Candidate $Candidate -Directory $boundaryFull)) {
        throw "$Context 越出安装边界：$Candidate"
    }
}

function New-SafeDirectoryChain {
    param(
        [Parameter(Mandatory = $true)][string]$Boundary,
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$Context
    )

    Assert-SafePathChain -Boundary $Boundary -Candidate $Directory -Context $Context
    $current = [System.IO.Path]::GetFullPath($Boundary).TrimEnd('\', '/')
    foreach ($segment in (Get-RelativeSegments -Candidate $Directory -Boundary $Boundary)) {
        $current = Join-Path $current $segment
        if (-not (Test-Path -LiteralPath $current)) {
            New-Item -ItemType Directory -Path $current | Out-Null
        }
        if (-not (Test-Path -LiteralPath $current -PathType Container)) {
            throw "$Context 需要目录，但路径不是目录：$current"
        }
        Assert-NoReparsePoint -Path $current -Context $Context
        if (-not (Test-IsSameOrChildPath -Candidate $current -Directory $Boundary)) {
            throw "$Context 创建目录后越出安装边界：$current"
        }
    }
    Assert-SafePathChain -Boundary $Boundary -Candidate $Directory -Context $Context
}

function Assert-SameVolume {
    param(
        [Parameter(Mandatory = $true)][string]$First,
        [Parameter(Mandatory = $true)][string]$Second,
        [Parameter(Mandatory = $true)][string]$Context
    )

    $firstRoot = [System.IO.Path]::GetPathRoot(
        [System.IO.Path]::GetFullPath($First)
    ).TrimEnd('\', '/')
    $secondRoot = [System.IO.Path]::GetPathRoot(
        [System.IO.Path]::GetFullPath($Second)
    ).TrimEnd('\', '/')
    if (-not $firstRoot.Equals($secondRoot, $pathComparison)) {
        throw "$Context 必须在同一卷内使用 Move-Item，拒绝跨卷复制：$First；$Second"
    }
}

function Assert-SafeRelativePath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    if (
        [string]::IsNullOrWhiteSpace($RelativePath) -or
        [System.IO.Path]::IsPathRooted($RelativePath) -or
        $RelativePath.Contains('\') -or
        $RelativePath -match '^[A-Za-z]:' -or
        $RelativePath -match '[\x00-\x1f\x7f]'
    ) {
        throw "Qoder 安装白名单包含非规范路径：$RelativePath"
    }
    foreach ($segment in $RelativePath.Split('/')) {
        if (
            [string]::IsNullOrWhiteSpace($segment) -or
            $segment -eq '.' -or
            $segment -eq '..'
        ) {
            throw "Qoder 安装白名单包含越界路径：$RelativePath"
        }
        foreach ($deniedSegment in $deniedSegments) {
            if ($segment.Equals(
                $deniedSegment,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                throw "Qoder 安装路径命中禁止目录：$RelativePath"
            }
        }
    }

    $leaf = $RelativePath.Split('/')[-1]
    foreach ($pattern in $deniedLeafPatterns) {
        $wildcard = New-Object System.Management.Automation.WildcardPattern(
            $pattern,
            [System.Management.Automation.WildcardOptions]::IgnoreCase
        )
        if ($wildcard.IsMatch($leaf)) {
            throw "Qoder 安装路径命中敏感文件规则 $pattern：$RelativePath"
        }
    }

    if ($RelativePath.StartsWith(
        'product_evidence_guard/',
        [System.StringComparison]::Ordinal
    )) {
        if (-not $leaf.EndsWith(
            '.py',
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "产品源码白名单仅允许 .py：$RelativePath"
        }
    }
    elseif ($RelativePath.StartsWith(
        'scripts/',
        [System.StringComparison]::Ordinal
    )) {
        $extension = [System.IO.Path]::GetExtension($leaf)
        if ($extension -notin @('.py', '.ps1', '.sh')) {
            throw "脚本白名单包含非法扩展名：$RelativePath"
        }
    }
    elseif ($RelativePath.StartsWith(
        'docs/',
        [System.StringComparison]::Ordinal
    )) {
        if (-not $leaf.EndsWith(
            '.md',
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "文档白名单仅允许 Markdown：$RelativePath"
        }
    }
    elseif ($RelativePath.StartsWith(
        'samples/demo/',
        [System.StringComparison]::Ordinal
    )) {
        if ($demoSampleFiles -notcontains $RelativePath) {
            throw "匿名示例白名单之外的文件不得安装：$RelativePath"
        }
    }
}

function Assert-RegularSourceFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Qoder 安装源缺少普通文件：$Path"
    }
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-IsSameOrChildPath -Candidate $fullPath -Directory $repoRoot)) {
        throw "Qoder 安装源越出仓库：$Path"
    }

    $current = $fullPath
    while ($true) {
        Assert-NoReparsePoint -Path $current -Context 'Qoder 安装源'
        if ($current.TrimEnd('\', '/').Equals(
            $repoRoot.TrimEnd('\', '/'),
            $pathComparison
        )) {
            break
        }
        $parent = [System.IO.Path]::GetDirectoryName(
            $current.TrimEnd('\', '/')
        )
        if (
            [string]::IsNullOrWhiteSpace($parent) -or
            -not (Test-IsSameOrChildPath -Candidate $parent -Directory $repoRoot)
        ) {
            throw "Qoder 安装源越出仓库：$Path"
        }
        $current = $parent
    }

    if (
        [System.Environment]::OSVersion.Platform -eq
        [System.PlatformID]::Win32NT
    ) {
        $fsutil = Get-Command 'fsutil.exe' -ErrorAction SilentlyContinue
        if ($null -eq $fsutil) {
            throw "无法验证 Qoder 安装源硬链接数量：$Path"
        }
        $links = @(& $fsutil.Source hardlink list $fullPath 2>$null)
        if ($LASTEXITCODE -ne 0 -or $links.Count -ne 1) {
            throw "Qoder 安装源是硬链接或无法验证：$Path"
        }
    }
    else {
        $stat = Get-Command 'stat' -ErrorAction SilentlyContinue
        if ($null -eq $stat) {
            throw "无法验证 Qoder 安装源硬链接数量：$Path"
        }
        $countText = @(& $stat.Source -c '%h' -- $fullPath 2>$null)
        if ($LASTEXITCODE -ne 0 -or $countText.Count -ne 1) {
            $countText = @(& $stat.Source -f '%l' -- $fullPath 2>$null)
        }
        $linkCount = 0L
        if (
            $LASTEXITCODE -ne 0 -or
            $countText.Count -ne 1 -or
            -not [long]::TryParse(
                $countText[0].ToString().Trim(),
                [ref]$linkCount
            ) -or
            $linkCount -ne 1
        ) {
            throw "Qoder 安装源是硬链接或无法验证：$Path"
        }
    }
}

function Get-ValidationPython {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    $candidates = New-Object System.Collections.Generic.List[object]
    foreach ($localPython in @(
        (Join-Path $InstallRoot '.venv\Scripts\python.exe'),
        (Join-Path $repoRoot '.venv\Scripts\python.exe')
    )) {
        if (Test-Path -LiteralPath $localPython -PathType Leaf) {
            $candidates.Add([pscustomobject]@{
                Path = $localPython
                Prefix = @()
            })
        }
    }

    foreach ($commandName in @('python.exe', 'python3', 'python')) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            $candidates.Add([pscustomobject]@{
                Path = $command.Source
                Prefix = @()
            })
        }
    }
    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        $candidates.Add([pscustomobject]@{
            Path = $launcher.Source
            Prefix = @('-3.11')
        })
    }

    foreach ($candidate in $candidates) {
        try {
            & $candidate.Path @($candidate.Prefix) -I -B -c (
                'import sys; raise SystemExit(' +
                '0 if sys.version_info[:2] == (3, 11) else 1)'
            ) *> $null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        }
        catch {
            continue
        }
    }
    throw '安装后验证需要可用的 Python 3.11；未找到，拒绝留下未验证 Skill。'
}

function Invoke-InstalledValidation {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    foreach ($relativePath in $requiredFiles) {
        $path = Join-Path $InstallRoot (
            $relativePath.Replace(
                '/',
                [System.IO.Path]::DirectorySeparatorChar
            )
        )
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "安装后验证缺少必备文件：$relativePath"
        }
        Assert-NoReparsePoint -Path $path -Context 'Qoder 安装后验证'
    }

    $python = Get-ValidationPython -InstallRoot $InstallRoot
    $packageModules = @('product_evidence_guard')
    foreach ($relativePath in $requiredRuntimeFiles) {
        if (
            $relativePath.StartsWith(
                'product_evidence_guard/',
                [System.StringComparison]::Ordinal
            ) -and
            $relativePath.EndsWith(
                '.py',
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            $moduleName = $relativePath.Substring(
                0,
                $relativePath.Length - 3
            ).Replace('/', '.')
            if (
                -not $moduleName.EndsWith('.__init__') -and
                -not $moduleName.EndsWith('.__main__')
            ) {
                $packageModules += $moduleName
            }
        }
    }
    $scriptModules = @(
        'benchmark',
        'client',
        'model_download',
        'protocol',
        'server'
    )
    $moduleArgument = ($packageModules -join ',')
    $scriptArgument = ($scriptModules -join ',')
    $importCheck = @'
import importlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "scripts"))
sys.path.insert(0, str(root))
for name in sys.argv[2].split(","):
    importlib.import_module(name)
for name in sys.argv[3].split(","):
    importlib.import_module(name)
from product_evidence_guard.cli import build_parser
if not build_parser().format_help():
    raise RuntimeError("CLI help is empty")
print("QODER_IMPORT_OK")
'@
    $importCheckBase64 = [Convert]::ToBase64String(
        [System.Text.Encoding]::UTF8.GetBytes($importCheck)
    )
    $importLauncher = (
        "import base64;exec(base64.b64decode('$importCheckBase64'))"
    )
    $savedErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $importOutput = @(
            & $python.Path @($python.Prefix) `
                -I -B -c $importLauncher `
                $InstallRoot $moduleArgument $scriptArgument 2>&1 |
                ForEach-Object { $_.ToString() }
        )
        $importExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedErrorActionPreference
    }
    if ($importExitCode -ne 0 -or $importOutput -notcontains 'QODER_IMPORT_OK') {
        throw "安装后模块导入验证失败：$($importOutput -join "`n")"
    }

    # Actually execute the installed CLI module (without creating logs/output).
    $runCheck = @'
import pathlib
import runpy
import sys

root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
sys.argv = ["product-evidence-guard", "--help"]
runpy.run_module("product_evidence_guard", run_name="__main__")
'@
    $runCheckBase64 = [Convert]::ToBase64String(
        [System.Text.Encoding]::UTF8.GetBytes($runCheck)
    )
    $runLauncher = "import base64;exec(base64.b64decode('$runCheckBase64'))"
    try {
        $ErrorActionPreference = 'Continue'
        $runOutput = @(
            & $python.Path @($python.Prefix) `
                -I -B -c $runLauncher $InstallRoot 2>&1 |
                ForEach-Object { $_.ToString() }
        )
        $runExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedErrorActionPreference
    }
    if ($runExitCode -ne 0 -or $runOutput.Count -eq 0) {
        throw "安装后运行验证失败：$($runOutput -join "`n")"
    }

    # On the supported Windows/PowerShell 5.1 path, also execute the installed
    # public entry point. Prepending the already verified interpreter lets a
    # fresh install validate run.ps1 before it owns a .venv.
    if (
        [System.Environment]::OSVersion.Platform -eq
        [System.PlatformID]::Win32NT
    ) {
        $entryPoint = Join-Path $InstallRoot 'scripts\run.ps1'
        $powerShellHost = (Get-Process -Id $PID).Path
        $savedPath = $env:Path
        try {
            $pythonDirectory = Split-Path -Parent $python.Path
            $env:Path = (
                $pythonDirectory +
                [System.IO.Path]::PathSeparator +
                $savedPath
            )
            $ErrorActionPreference = 'Continue'
            $entryOutput = @(
                & $powerShellHost `
                    -NoLogo `
                    -NoProfile `
                    -NonInteractive `
                    -ExecutionPolicy Bypass `
                    -File $entryPoint `
                    --help 2>&1 |
                    ForEach-Object { $_.ToString() }
            )
            $entryExitCode = $LASTEXITCODE
        }
        finally {
            $env:Path = $savedPath
            $ErrorActionPreference = $savedErrorActionPreference
        }
        if ($entryExitCode -ne 0 -or $entryOutput.Count -eq 0) {
            throw "安装后公开入口运行验证失败：$($entryOutput -join "`n")"
        }
    }
}

function Remove-SafeStagingTree {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$StagingRoot,
        [Parameter(Mandatory = $true)][string]$Boundary
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    if (
        [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/').Equals(
            [System.IO.Path]::GetFullPath($StagingRoot).TrimEnd('\', '/'),
            $pathComparison
        ) -or
        -not (Test-IsSameOrChildPath -Candidate $Path -Directory $StagingRoot)
    ) {
        throw "拒绝清理非 staging 子目录：$Path"
    }
    Assert-SafePathChain `
        -Boundary $Boundary `
        -Candidate $Path `
        -Context 'Qoder staging 清理'
    Remove-Item -LiteralPath $Path -Recurse -Force
}

# In Git working trees, even a named optional file must be tracked. This keeps
# arbitrary/unreviewed working-tree content out of the installed Skill.
$trackedOnly = $false
$trackedPaths = New-Object 'System.Collections.Generic.HashSet[string]' (
    [System.StringComparer]::Ordinal
)
$gitMetadataPath = Join-Path $repoRoot '.git'
if (Test-Path -LiteralPath $gitMetadataPath) {
    $git = Get-Command 'git' -ErrorAction SilentlyContinue
    if ($null -eq $git) {
        throw '检测到 Git 工作树，但找不到 git；拒绝扫描未跟踪文件。'
    }
    $gitOutput = @(
        & $git.Source `
            -C $repoRoot `
            -c core.excludesFile= `
            -c core.quotepath=false `
            ls-files --cached -- 2>&1 |
            ForEach-Object { $_.ToString() }
    )
    if ($LASTEXITCODE -ne 0) {
        throw "无法读取 Git tracked 文件清单：$($gitOutput -join "`n")"
    }
    foreach ($trackedPath in $gitOutput) {
        if (-not [string]::IsNullOrWhiteSpace($trackedPath)) {
            $null = $trackedPaths.Add($trackedPath.Replace('\', '/'))
        }
    }
    $trackedOnly = $true
}

$filesToCopy = New-Object System.Collections.Generic.List[object]
$selectedPaths = New-Object 'System.Collections.Generic.HashSet[string]' (
    [System.StringComparer]::OrdinalIgnoreCase
)
$totalBytes = 0L
foreach ($relativePath in $allowedFiles) {
    Assert-SafeRelativePath -RelativePath $relativePath
    if (-not $selectedPaths.Add($relativePath)) {
        throw "Qoder 安装白名单存在重复或大小写冲突：$relativePath"
    }

    $isRequired = $requiredFiles -contains $relativePath
    if ($trackedOnly -and -not $trackedPaths.Contains($relativePath)) {
        if ($isRequired) {
            throw "Git 索引缺少必备 Qoder 文件：$relativePath"
        }
        continue
    }

    $source = Join-Path $repoRoot (
        $relativePath.Replace(
            '/',
            [System.IO.Path]::DirectorySeparatorChar
        )
    )
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        if ($isRequired) {
            throw "缺少必备 Qoder 文件：$relativePath"
        }
        continue
    }
    Assert-RegularSourceFile -Path $source

    $length = (Get-Item -LiteralPath $source -Force).Length
    if ($length -gt $maximumFileBytes) {
        throw "Qoder 单文件超过 $maximumFileBytes 字节上限：$relativePath"
    }
    $totalBytes += $length
    if ($totalBytes -gt $maximumTotalBytes) {
        throw "Qoder 安装输入超过 $maximumTotalBytes 字节总上限。"
    }
    $filesToCopy.Add([pscustomobject]@{
        RelativePath = $relativePath
        Source = $source
    })
}

foreach ($requiredPath in $requiredFiles) {
    if (-not $selectedPaths.Contains($requiredPath)) {
        throw "必备 Qoder 文件未纳入显式白名单：$requiredPath"
    }
    if (-not ($filesToCopy.RelativePath -contains $requiredPath)) {
        throw "必备 Qoder 文件未通过 fail-closed 选择：$requiredPath"
    }
}

switch ($Scope) {
    'User' {
        if ([string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
            throw '无法确定用户目录。'
        }
        $boundary = [System.IO.Path]::GetFullPath($env:USERPROFILE)
        if (-not (Test-Path -LiteralPath $boundary -PathType Container)) {
            throw "用户目录不存在：$boundary"
        }
    }
    'Project' {
        if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
            throw '项目级安装必须提供 -ProjectRoot。'
        }
        $boundary = [System.IO.Path]::GetFullPath($ProjectRoot)
        if (-not (Test-Path -LiteralPath $boundary -PathType Container)) {
            throw "Qoder 项目目录不存在：$boundary"
        }
    }
}
Assert-NoReparsePoint -Path $boundary -Context 'Qoder 安装边界'

$qoderRoot = Join-Path $boundary '.qoder'
$skillsRoot = Join-Path $qoderRoot 'skills'
$destination = Join-Path $skillsRoot $skillName
$stagingRoot = Join-Path $qoderRoot 'skill-staging'
$backupRoot = Join-Path $qoderRoot 'skill-backups'
$operationId = (
    (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' +
    [Guid]::NewGuid().ToString('N')
)
$staging = Join-Path $stagingRoot "$skillName-$operationId"
$backup = Join-Path $backupRoot "$skillName-$operationId"

# Check every existing destination ancestor (.qoder, skills, target) before any
# write, then check again after each directory creation and move.
foreach ($candidate in @(
    $qoderRoot,
    $skillsRoot,
    $destination,
    $stagingRoot,
    $staging,
    $backupRoot,
    $backup
)) {
    Assert-SafePathChain `
        -Boundary $boundary `
        -Candidate $candidate `
        -Context 'Qoder 安装目标'
}

$destinationExists = Test-Path -LiteralPath $destination
if ($destinationExists -and -not (Test-Path -LiteralPath $destination -PathType Container)) {
    throw "Qoder 安装目标存在但不是目录：$destination"
}
if ($destinationExists -and -not $Update) {
    throw "目标已存在：$destination。为避免覆盖，请显式添加 -Update。"
}

New-SafeDirectoryChain `
    -Boundary $boundary `
    -Directory $qoderRoot `
    -Context 'Qoder 根目录'
New-SafeDirectoryChain `
    -Boundary $boundary `
    -Directory $skillsRoot `
    -Context 'Qoder skills 目录'
New-SafeDirectoryChain `
    -Boundary $boundary `
    -Directory $stagingRoot `
    -Context 'Qoder staging 目录'
if ($destinationExists) {
    New-SafeDirectoryChain `
        -Boundary $boundary `
        -Directory $backupRoot `
        -Context 'Qoder backup 目录'
}

Assert-SameVolume `
    -First $destination `
    -Second $staging `
    -Context 'Qoder 原子安装'
if ($destinationExists) {
    Assert-SameVolume `
        -First $destination `
        -Second $backup `
        -Context 'Qoder 原子备份'
}
if (Test-Path -LiteralPath $staging) {
    throw "staging 目标已存在：$staging"
}
if (Test-Path -LiteralPath $backup) {
    throw "备份目标已存在：$backup"
}

$stageExists = $false
$oldMovedToBackup = $false
$newMovedToLive = $false
$preservedNames = New-Object System.Collections.Generic.List[string]

try {
    New-SafeDirectoryChain `
        -Boundary $boundary `
        -Directory $staging `
        -Context 'Qoder staging 目录'
    $stageExists = $true

    foreach ($file in $filesToCopy) {
        $target = Join-Path $staging (
            $file.RelativePath.Replace(
                '/',
                [System.IO.Path]::DirectorySeparatorChar
            )
        )
        $targetParent = Split-Path -Parent $target
        New-SafeDirectoryChain `
            -Boundary $staging `
            -Directory $targetParent `
            -Context 'Qoder staging 文件目录'
        if (Test-Path -LiteralPath $target) {
            throw "Qoder staging 文件意外存在：$target"
        }
        Copy-Item -LiteralPath $file.Source -Destination $target
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "Qoder staging 复制失败：$($file.RelativePath)"
        }
        Assert-NoReparsePoint -Path $target -Context 'Qoder staging 文件'
    }

    Invoke-InstalledValidation -InstallRoot $staging

    if ($destinationExists) {
        Assert-SafePathChain `
            -Boundary $boundary `
            -Candidate $destination `
            -Context 'Qoder 更新目标'
        Move-Item -LiteralPath $destination -Destination $backup
        $oldMovedToBackup = $true
        Assert-SafePathChain `
            -Boundary $boundary `
            -Candidate $backup `
            -Context 'Qoder 旧版本备份'

        # Preserve only these runtime directories. Moving within one volume is
        # mandatory: multi-GB models/environments are never copied.
        foreach ($name in $preservedRuntimeDirectories) {
            $oldRuntime = Join-Path $backup $name
            if (-not (Test-Path -LiteralPath $oldRuntime)) {
                continue
            }
            if (-not (Test-Path -LiteralPath $oldRuntime -PathType Container)) {
                continue
            }
            Assert-SafePathChain `
                -Boundary $boundary `
                -Candidate $oldRuntime `
                -Context 'Qoder 运行时保留源'
            Assert-NoReparsePoint `
                -Path $oldRuntime `
                -Context 'Qoder 运行时保留源'
            $newRuntime = Join-Path $staging $name
            if (Test-Path -LiteralPath $newRuntime) {
                throw "新版本已包含运行时目录，拒绝覆盖：$newRuntime"
            }
            Assert-SameVolume `
                -First $oldRuntime `
                -Second $newRuntime `
                -Context 'Qoder 运行时保留'
            Move-Item -LiteralPath $oldRuntime -Destination $newRuntime
            $preservedNames.Add($name)
            Assert-SafePathChain `
                -Boundary $boundary `
                -Candidate $newRuntime `
                -Context 'Qoder 已保留运行时'
        }

        # Runtime moves must not change which product modules are importable.
        Invoke-InstalledValidation -InstallRoot $staging
    }

    if (Test-Path -LiteralPath $destination) {
        throw "切换新版本前安装目标意外存在：$destination"
    }
    Assert-SafePathChain `
        -Boundary $boundary `
        -Candidate $skillsRoot `
        -Context 'Qoder skills 切换'
    Move-Item -LiteralPath $staging -Destination $destination
    $stageExists = $false
    $newMovedToLive = $true
    Assert-SafePathChain `
        -Boundary $boundary `
        -Candidate $destination `
        -Context 'Qoder 新版本目标'

    # Verify the actual live installation, not merely the source or staging tree.
    Invoke-InstalledValidation -InstallRoot $destination
}
catch {
    $installFailure = $_
    $rollbackErrors = New-Object System.Collections.Generic.List[string]

    # Move a failed new live tree back out of the discovery root first.
    if ($newMovedToLive -and (Test-Path -LiteralPath $destination)) {
        try {
            if (Test-Path -LiteralPath $staging) {
                throw "回滚 staging 已存在：$staging"
            }
            Assert-SameVolume `
                -First $destination `
                -Second $staging `
                -Context 'Qoder 新版本回滚'
            Move-Item -LiteralPath $destination -Destination $staging
            $newMovedToLive = $false
            $stageExists = $true
        }
        catch {
            $rollbackErrors.Add("无法移出失败的新版本：$($_.Exception.Message)")
        }
    }

    # Put every preserved runtime back into the old backup before restoring it.
    if ($oldMovedToBackup) {
        foreach ($name in $preservedNames) {
            try {
                $runtimeInStage = Join-Path $staging $name
                $runtimeInBackup = Join-Path $backup $name
                if (Test-Path -LiteralPath $runtimeInStage) {
                    if (Test-Path -LiteralPath $runtimeInBackup) {
                        throw "旧备份中的运行时目标已存在：$runtimeInBackup"
                    }
                    Assert-SameVolume `
                        -First $runtimeInStage `
                        -Second $runtimeInBackup `
                        -Context 'Qoder 运行时回滚'
                    Move-Item `
                        -LiteralPath $runtimeInStage `
                        -Destination $runtimeInBackup
                }
            }
            catch {
                $rollbackErrors.Add(
                    "无法回滚运行时目录 $name：$($_.Exception.Message)"
                )
            }
        }

        try {
            if (Test-Path -LiteralPath $destination) {
                throw "恢复旧版本时目标仍存在：$destination"
            }
            Assert-SameVolume `
                -First $backup `
                -Second $destination `
                -Context 'Qoder 旧版本回滚'
            Move-Item -LiteralPath $backup -Destination $destination
            $oldMovedToBackup = $false
            Assert-SafePathChain `
                -Boundary $boundary `
                -Candidate $destination `
                -Context 'Qoder 已回滚旧版本'
        }
        catch {
            $rollbackErrors.Add("无法恢复旧版本：$($_.Exception.Message)")
        }
    }

    if ($stageExists -and (Test-Path -LiteralPath $staging)) {
        try {
            Remove-SafeStagingTree `
                -Path $staging `
                -StagingRoot $stagingRoot `
                -Boundary $boundary
            $stageExists = $false
        }
        catch {
            $rollbackErrors.Add("无法清理 staging：$($_.Exception.Message)")
        }
    }

    $message = "Qoder 安装失败：$($installFailure.Exception.Message)"
    if ($rollbackErrors.Count -gt 0) {
        $message += "`n回滚告警：$($rollbackErrors -join '；')"
    }
    throw $message
}

Write-Host "Skill 已安装并验证：$destination"
Write-Host (
    "已从显式白名单复制 $($filesToCopy.Count) 个 tracked/发布文件；" +
    '模型、缓存、日志、输出、凭据及任意未跟踪文件均不会从源仓库安装。'
)
if ($oldMovedToBackup) {
    Write-Host "旧版本已备份到 skills discovery root 之外：$backup"
    if ($preservedNames.Count -gt 0) {
        Write-Host (
            '已通过同卷移动保留运行时目录：' +
            ($preservedNames -join '、')
        )
    }
}
Write-Host (
    '请重启 Qoder，或在 Qoder CLI 中运行 /skills reload，' +
    '然后输入 /local-product-evidence-guard 验证。'
)
