$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$dockerTool = Assert-FreshLinkDocker
$projectRoot = Get-FreshLinkRoot
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env.freshlink'))) {
    throw 'No local environment exists. Nothing was stopped.'
}
Push-Location $projectRoot
try {
    & $dockerTool compose --env-file .env.freshlink -f compose.freshlink.yml stop
    if ($LASTEXITCODE -ne 0) { throw 'Stopping containers failed.' }
    Write-Output 'Stopped. Database volume and local configuration were preserved.'
} finally { Pop-Location }
