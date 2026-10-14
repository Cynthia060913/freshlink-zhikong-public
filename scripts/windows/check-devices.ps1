param([string]$BaseUrl = 'http://localhost:18000')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$endpoint = [Uri]$BaseUrl
if (-not $endpoint.IsLoopback -or $endpoint.Scheme -notin @('http','https')) { throw 'Loopback only.' }
$projectRoot = Get-FreshLinkRoot
$passwordLine = Get-Content -LiteralPath (Join-Path $projectRoot '.env.freshlink') | Where-Object { $_ -match '^FIRST_SUPERUSER_PASSWORD=' } | Select-Object -First 1
if (-not $passwordLine) { throw 'Local admin password is missing.' }
$token = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/login/access-token" -ContentType 'application/x-www-form-urlencoded' -Body @{username='admin@example.com';password=$passwordLine.Substring($passwordLine.IndexOf('=') + 1)} -TimeoutSec 20
$headers = @{Authorization=('Bearer ' + $token.access_token)}
function Assert-DeviceStatus {
    param([string]$Path, [string]$Method, [object]$Body, [int[]]$Expected, [hashtable]$RequestHeaders)
    $request = @{Uri=($BaseUrl + $Path); Method=$Method; Headers=$RequestHeaders; UseBasicParsing=$true; TimeoutSec=20}
    if ($null -ne $Body) { $request.ContentType='application/json'; $request.Body=($Body | ConvertTo-Json -Compress) }
    $statusCode = 0
    try { $response = Invoke-WebRequest @request; $statusCode=[int]$response.StatusCode }
    catch { if ($null -eq $_.Exception.Response) { throw }; $statusCode=[int]$_.Exception.Response.StatusCode }
    if ($statusCode -notin $Expected) { throw "Unexpected HTTP $statusCode for $Method $Path" }
    Write-Output "PASS $Method $Path HTTP $statusCode"
}
$suffix = [Guid]::NewGuid().ToString('N').Substring(0,12)
$createdIds = @()
foreach ($kind in @('temperature_sensor','cooling_actuator')) {
    $body = @{code="check-$kind-$suffix";name="A01 test $kind";kind=$kind}
    $device = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/devices/" -Headers $headers -ContentType 'application/json' -Body ($body | ConvertTo-Json -Compress) -TimeoutSec 20
    if ($device.connectivity -ne 'unknown' -or $null -ne $device.last_seen_at -or -not $device.is_simulated) { throw 'Registration falsely implies connectivity.' }
    if ($device.created_at -notmatch '(Z|\+00:00)$') { throw 'UTC timezone missing.' }
    $createdIds += $device.id
    $detail = Invoke-RestMethod -Uri "$BaseUrl/api/v1/devices/$($device.id)" -Headers $headers -TimeoutSec 20
    if ($detail.code -ne $body.code) { throw 'Detail differs from registration.' }
    Assert-DeviceStatus -Path '/api/v1/devices/' -Method 'Post' -Body $body -Expected @(409) -RequestHeaders $headers
}
$page = Invoke-RestMethod -Uri "$BaseUrl/api/v1/devices/?limit=100" -Headers $headers -TimeoutSec 20
if ($page.count -lt 2) { throw 'Device count incorrect.' }
Assert-DeviceStatus -Path '/api/v1/devices/' -Method 'Get' -Body $null -Expected @(401,403) -RequestHeaders @{}
Assert-DeviceStatus -Path '/api/v1/devices/' -Method 'Post' -Body @{code='invalid';name=' ';kind='unknown'} -Expected @(422) -RequestHeaders $headers
Assert-DeviceStatus -Path '/api/v1/devices/?limit=101' -Method 'Get' -Body $null -Expected @(422) -RequestHeaders $headers
Assert-DeviceStatus -Path '/api/v1/devices/not-a-uuid' -Method 'Get' -Body $null -Expected @(422) -RequestHeaders $headers
Assert-DeviceStatus -Path ('/api/v1/devices/' + [Guid]::NewGuid()) -Method 'Get' -Body $null -Expected @(404) -RequestHeaders $headers
Assert-DeviceStatus -Path '/api/v1/devices/' -Method 'Post' -Body @{code='unbound-check';name='Unbound';kind='temperature_sensor';warehouse_id=[Guid]::NewGuid().ToString()} -Expected @(422) -RequestHeaders $headers
$workPath = Join-Path $projectRoot 'work'
if (-not (Test-Path -LiteralPath $workPath)) { New-Item -ItemType Directory -Path $workPath | Out-Null }
$resultPath = Join-Path $workPath ('a01-devices-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID + '.json')
@{stage='A01 HTTP checks';status='passed';created_device_ids=$createdIds;checked_at=[DateTimeOffset]::UtcNow.ToString('o');browser_verified=$false;ordinary_user_permissions_verified=$false;restart_persistence_verified=$false;mqtt_verified=$false} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
Write-Output "PASS A01 HTTP subset; two labelled test devices remain in the local database. Result: $resultPath"
Write-Output 'Not checked here: ordinary-user permissions, restart persistence, empty-database migration, frontend, MQTT or control.'
