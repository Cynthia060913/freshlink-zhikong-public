$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$projectRoot = Get-FreshLinkRoot
$baseUrl = 'http://localhost:18000'
$work = Join-Path $projectRoot 'work'
if (-not (Test-Path -LiteralPath $work)) { New-Item -ItemType Directory -Path $work | Out-Null }
$resultPath = Join-Path $work ('a01-backend-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID + '.json')
$result = @{stage='A01 backend acceptance';status='failed';checks=@();browser_verified=$false;mqtt_verified=$false;control_verified=$false;error=$null}
Push-Location $projectRoot
try {
    $dockerTool = Assert-FreshLinkDocker
    $composeArgs = @('compose','--env-file','.env.freshlink','-f','compose.freshlink.yml')
    & $dockerTool @composeArgs exec -T app python scripts/check-device-schema.py
    if ($LASTEXITCODE -ne 0) { throw 'Container schema tests failed.' }
    $result.checks += 'container_schema_tests'
    # Stream the new check into the existing container; no rebuild required.
    Get-Content -LiteralPath 'backend/scripts/check-device-empty-migration.py' -Raw | & $dockerTool @composeArgs exec -T app python -
    if ($LASTEXITCODE -ne 0) { throw 'Disposable empty-database migration check failed.' }
    $result.checks += 'empty_postgresql_migration_and_cleanup'
    & (Join-Path $PSScriptRoot 'check-device-permissions.ps1')
    $result.checks += 'live_ordinary_user_permissions_and_cleanup'
    $passwordLine = Get-Content -LiteralPath '.env.freshlink' | Where-Object { $_ -match '^FIRST_SUPERUSER_PASSWORD=' } | Select-Object -First 1
    if (-not $passwordLine) { throw 'Local admin password missing.' }
    $token = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/login/access-token" -ContentType 'application/x-www-form-urlencoded' -Body @{username='admin@example.com';password=$passwordLine.Substring($passwordLine.IndexOf('=')+1)} -TimeoutSec 20
    $headers = @{Authorization=('Bearer ' + $token.access_token)}
    $before = Invoke-RestMethod -Uri "$baseUrl/api/v1/devices/?limit=100" -Headers $headers -TimeoutSec 20
    if ($before.data.Count -lt 2) { throw 'Need existing device records for persistence check.' }
    $beforeSnapshots = @($before.data | ForEach-Object { @{id=$_.id;code=$_.code;name=$_.name;kind=$_.kind;created_at=$_.created_at;is_simulated=$_.is_simulated} })
    Write-Output 'Restarting app and database containers briefly; persistent volume is retained.'
    & $dockerTool @composeArgs restart db app
    if ($LASTEXITCODE -ne 0) { throw 'Container restart failed; no volume removal requested.' }
    $ready = $false
    $deadline = [DateTime]::UtcNow.AddSeconds(180)
    while ([DateTime]::UtcNow -lt $deadline) {
        try { $health=Invoke-RestMethod -Uri "$baseUrl/api/v1/utils/health-check/" -TimeoutSec 5; if ($health -eq $true) { $ready=$true; break } } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw 'App did not recover after restart.' }
    $after = Invoke-RestMethod -Uri "$baseUrl/api/v1/devices/?limit=100" -Headers $headers -TimeoutSec 20
    if ($after.count -ne $before.count) { throw 'Device count changed after restart.' }
    foreach ($snapshot in $beforeSnapshots) {
        $detail = Invoke-RestMethod -Uri "$baseUrl/api/v1/devices/$($snapshot.id)" -Headers $headers -TimeoutSec 20
        foreach ($field in @('id','code','name','kind','created_at','is_simulated')) { if ($detail.$field -ne $snapshot[$field]) { throw "Device field $field changed after restart." } }
        if ($detail.connectivity -ne 'unknown' -or $null -ne $detail.last_seen_at) { throw 'Unexpected connectivity after restart.' }
    }
    $result.checks += 'app_and_database_restart_device_persistence'
    $result.existing_devices_verified = $beforeSnapshots.Count
    Write-Output 'PASS device records and fields survived app/database restart'
    & (Join-Path $PSScriptRoot 'smoke-test.ps1')
    $result.checks += 'base_login_and_html_regression'
    $result.status='passed'
} catch {
    $result.error='Backend acceptance failed; see this terminal for the failing stage.'
    throw
} finally {
    $result.checked_at=[DateTimeOffset]::UtcNow.ToString('o')
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-Output "Result: $resultPath"
    Pop-Location
}
Write-Output 'PASS A01 backend acceptance supplement. Frontend, heartbeat, MQTT and control are separate tasks.'
