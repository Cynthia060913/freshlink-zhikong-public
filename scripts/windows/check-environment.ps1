# Read-only: no installation, network calls, output files or system changes.
$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'common.ps1')

Write-Output 'FreshLink P0 environment check (read-only)'
Write-Output ('OS: ' + [Environment]::OSVersion.VersionString)
Write-Output ('PowerShell: ' + $PSVersionTable.PSVersion.ToString())

$gitTool = Get-Command git -ErrorAction SilentlyContinue
if ($null -ne $gitTool) { & $gitTool.Source --version } else { Write-Output 'Git: NOT FOUND (needed for team development)' }

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $pythonLauncher) {
    & $pythonLauncher.Source --version
    if ($LASTEXITCODE -ne 0) { Write-Output 'Python launcher: unavailable; container mode does not require host Python.' }
} else { Write-Output 'Python launcher: NOT FOUND (not required for container mode)' }

$dockerTool = Find-FreshLinkDocker
if (-not $dockerTool) {
    Write-Output 'Docker CLI: NOT FOUND'
    Write-Output 'Container startup: NOT READY. See START_HERE.md.'
    exit 2
}
& $dockerTool --version
& $dockerTool compose version
if ($LASTEXITCODE -ne 0) { Write-Output 'Compose: NOT READY'; exit 2 }
$engineOutput = & $dockerTool info --format '{{.OSType}}' 2>$null
if ($LASTEXITCODE -ne 0) { Write-Output 'Docker engine: NOT READY (CLI is installed)'; exit 2 }
if ($engineOutput -ne 'linux') { Write-Output 'Docker engine: Linux containers required'; exit 2 }
Write-Output 'Container startup prerequisites: READY (application build/login still unverified)'
exit 0
