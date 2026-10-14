param([string]$BaseUrl = 'http://localhost:18000')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$endpoint = [Uri]$BaseUrl
if (-not $endpoint.IsLoopback -or $endpoint.Scheme -notin @('http','https')) {
    throw 'This check only sends local development credentials to a loopback address.'
}
$projectRoot = Get-FreshLinkRoot
$privateEnvironment = Join-Path $projectRoot '.env.freshlink'
if (-not (Test-Path -LiteralPath $privateEnvironment)) { throw 'Start the local environment first.' }
$passwordLine = Get-Content -LiteralPath $privateEnvironment | Where-Object { $_ -match '^FIRST_SUPERUSER_PASSWORD=' } | Select-Object -First 1
if (-not $passwordLine) { throw 'Local admin password is missing.' }
$localPassword = $passwordLine.Substring($passwordLine.IndexOf('=') + 1)

$health = Invoke-RestMethod -Uri "$BaseUrl/api/v1/utils/health-check/" -TimeoutSec 15
if ($health -ne $true) { throw 'API health check failed.' }
Write-Output 'PASS API health'
$page = Invoke-WebRequest -Uri "$BaseUrl/" -UseBasicParsing -TimeoutSec 15
if ($page.StatusCode -ne 200 -or $page.Content -notmatch 'id="root"') { throw 'Frontend HTML was not served.' }
Write-Output 'PASS frontend HTML (not a browser interaction test)'
$token = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/login/access-token" -ContentType 'application/x-www-form-urlencoded' -Body @{username='admin@example.com';password=$localPassword} -TimeoutSec 15
$currentUser = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/login/test-token" -Headers @{Authorization=('Bearer ' + $token.access_token)} -TimeoutSec 15
if ($currentUser.email -ne 'admin@example.com' -or -not $currentUser.is_superuser) { throw 'Authenticated user check failed.' }
Write-Output 'PASS login and authenticated user'
Write-Output 'P0 API smoke checks passed. Browser login and cold-chain requirements are separate checks.'
