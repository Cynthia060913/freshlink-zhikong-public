# Live isolated MQTT device process, not the running application or E1.
[CmdletBinding()]
param([string]$AppImage = 'freshlink-app:p0')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')
$originalLocation = Get-Location
$priorImage = [Environment]::GetEnvironmentVariable('A_RUNTIME_IMAGE','Process')
$priorResults = [Environment]::GetEnvironmentVariable('A_RUNTIME_RESULTS','Process')
$dockerExecutable = $null
$composeArgs = $null
$exitCode = 1
try {
    $projectRoot = Get-FreshLinkRoot
    Set-Location -LiteralPath $projectRoot
    $dockerExecutable = Assert-FreshLinkDocker
    foreach ($image in @($AppImage,'eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408')) {
        & $dockerExecutable image inspect $image --format '{{.Id}}'
        if ($LASTEXITCODE -ne 0) { throw 'Required verified local image is missing.' }
    }
    & $dockerExecutable run --rm --pull never --network none $AppImage python -c 'import paho.mqtt.client'
    if ($LASTEXITCODE -ne 0) { throw 'Build the current locked app image including paho-mqtt first.' }
    $runId = 'a-runtime-' + [guid]::NewGuid().ToString('N')
    $resultDir = Join-Path $projectRoot ('work/' + $runId)
    [IO.Directory]::CreateDirectory($resultDir) | Out-Null
    [Environment]::SetEnvironmentVariable('A_RUNTIME_IMAGE',$AppImage,'Process')
    [Environment]::SetEnvironmentVariable('A_RUNTIME_RESULTS',$resultDir,'Process')
    $composeArgs = @('compose','--project-name',$runId,'-f','compose.simulator-tests.yml')
    & $dockerExecutable @composeArgs config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Isolated runtime test configuration invalid.' }
    & $dockerExecutable @composeArgs up -d --pull never broker
    if ($LASTEXITCODE -ne 0) { throw 'Disposable broker startup failed.' }
    & $dockerExecutable @composeArgs run --rm --no-deps --pull never probe
    if ($LASTEXITCODE -ne 0) { throw 'Live runtime verification failed; inspect private evidence.' }
    $result = Get-Content -LiteralPath (Join-Path $resultDir 'result.json') -Raw | ConvertFrom-Json
    if ($result.status -ne 'passed') { throw 'No passed runtime result.' }
    Write-Output 'PASS continuous sensor/actuator process, live MQTT command/ACK/telemetry and fresh heartbeats'
    Write-Output 'PASS heartbeat emission stops/resumes; same command across process restart executes once'
    Write-Output 'PASS retained/QoS0/expired commands and cross-device publication handled as tested'
    Write-Output ('Evidence directory: ' + $resultDir)
    $exitCode = 0
} catch {
    Write-Output ('STOP: ' + $_.Exception.Message)
} finally {
    if ($dockerExecutable -and $composeArgs) {
        & $dockerExecutable @composeArgs down --remove-orphans
        if ($LASTEXITCODE -ne 0) { $exitCode=1; Write-Output 'STOP: disposable test cleanup failed.' }
    }
    [Environment]::SetEnvironmentVariable('A_RUNTIME_IMAGE',$priorImage,'Process')
    [Environment]::SetEnvironmentVariable('A_RUNTIME_RESULTS',$priorResults,'Process')
    Set-Location -LiteralPath $originalLocation.Path
}
Write-Output 'NOT CHECKED: server online/offline events, production TLS/credentials, registered binding, C storage, B alerts/notifications, public control API, E1.'
exit $exitCode
