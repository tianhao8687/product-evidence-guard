$ErrorActionPreference = 'Stop'

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepositoryRoot = Split-Path -Parent $ScriptRoot
$ClientScript = Join-Path $ScriptRoot 'client.py'
$RequestedOperation = if ($args.Count -gt 0) { [string]$args[0] } else { 'client' }

function Write-StableErrorJson {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Code,
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $Response = [ordered]@{
        protocol_version = 1
        request_id = 'powershell-entry'
        operation = $RequestedOperation
        ok = $false
        status = 'error'
        exit_code = 1
        result = [ordered]@{}
        error = [ordered]@{
            code = $Code
            message = $Message
        }
    }
    [Console]::Out.WriteLine(($Response | ConvertTo-Json -Compress -Depth 5))
}

if (-not (Test-Path -LiteralPath $ClientScript -PathType Leaf)) {
    Write-StableErrorJson -Code 'client_missing' -Message '找不到本地客户端脚本。'
    exit 1
}

$Candidates = [System.Collections.Generic.List[object]]::new()
$VirtualPython = Join-Path $RepositoryRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $VirtualPython -PathType Leaf) {
    $Candidates.Add([pscustomobject]@{ Path = $VirtualPython; Prefix = @() })
}

$PythonCommand = Get-Command 'python.exe' -ErrorAction SilentlyContinue
if ($null -ne $PythonCommand) {
    $Candidates.Add([pscustomobject]@{ Path = $PythonCommand.Source; Prefix = @() })
}

$LauncherCommand = Get-Command 'py.exe' -ErrorAction SilentlyContinue
if ($null -ne $LauncherCommand) {
    $Candidates.Add([pscustomobject]@{ Path = $LauncherCommand.Source; Prefix = @('-3.11') })
}

$Selected = $null
foreach ($Candidate in $Candidates) {
    try {
        & $Candidate.Path @($Candidate.Prefix) -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' *> $null
        if ($LASTEXITCODE -eq 0) {
            $Selected = $Candidate
            break
        }
    }
    catch {
        continue
    }
}

if ($null -eq $Selected) {
    Write-StableErrorJson `
        -Code 'python_unavailable' `
        -Message '找不到可用的 Python 3.11 环境，请先运行 scripts\install-env.ps1。'
    exit 1
}

Push-Location -LiteralPath $RepositoryRoot
try {
    & $Selected.Path @($Selected.Prefix) -B $ClientScript @args
    $ClientExitCode = $LASTEXITCODE
}
catch {
    Write-StableErrorJson `
        -Code 'client_launch_failed' `
        -Message ('无法启动本地客户端：' + $_.Exception.Message)
    $ClientExitCode = 1
}
finally {
    Pop-Location
}

if ($null -eq $ClientExitCode) {
    $ClientExitCode = 1
}
exit $ClientExitCode
