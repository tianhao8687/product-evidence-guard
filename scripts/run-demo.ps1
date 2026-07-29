$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
python -m product_evidence_guard analyze .\samples\demo --output .\demo-output
Write-Host "Open demo-output\evidence-report.html to inspect the evidence graph."
