# Live MQTT -> A persistent event -> B ingestion, isolated test only.
[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$originalLocation=Get-Location
$priorResult=[Environment]::GetEnvironmentVariable('DEVICE_EVENT_RESULT_DIR','Process')
$priorImage=[Environment]::GetEnvironmentVariable('DEVICE_EVENT_APP_IMAGE','Process')
$docker=$null
$compose=$null
$exitCode=1
try {
    $root=Get-FreshLinkRoot
    Set-Location -LiteralPath $root
    $docker=Assert-FreshLinkDocker
    $runId='device-events-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+$PID
    if($runId -notmatch '^device-events-\d{14}-\d+$'){throw 'Invalid owned test project'}
    $results=Join-Path $root ('work/'+$runId)
    [IO.Directory]::CreateDirectory($results)|Out-Null
    $env:DEVICE_EVENT_RESULT_DIR=$results
    $env:DEVICE_EVENT_APP_IMAGE=$AppImage
    $compose=@('compose','--project-name',$runId,'-f','compose.device-event-tests.yml')
    & $docker @compose config --quiet
    if($LASTEXITCODE -ne 0){throw 'Isolated Compose invalid'}
    & $docker @compose up -d --wait --pull never test-db broker
    if($LASTEXITCODE -ne 0){throw 'Disposable services failed'}
    & $docker @compose run --rm --no-deps --pull never test-runner
    if($LASTEXITCODE -ne 0){throw 'Device-event chain failed'}
    $report=Get-Content -LiteralPath (Join-Path $results 'mqtt-device-events.json') -Raw|ConvertFrom-Json
    if($report.phase -ne 'live_mqtt_a_outbox_b_ingestion_passed'){throw 'Wrong evidence phase'}
    Write-Output 'PASS actual authenticated heartbeat/offline/recovery -> A durable event -> B real ingestion; lost A checkpoint replay, B transient failure, original source/reason and recovery reference.'
    Write-Output ('Evidence: '+$results)
    Write-Output 'NOT CHECKED: formal B migration, automatic retry scheduling, notification dispatch/terminal receipts, C persistence/CSV, browser or full E1.'
    $exitCode=0
} catch {Write-Output ('STOP: '+$_.Exception.Message)}
finally {
    if($docker -and $compose){
        & $docker @compose down --remove-orphans
        if($LASTEXITCODE -ne 0){$exitCode=1;Write-Output 'STOP: owned test cleanup failed'}
    }
    [Environment]::SetEnvironmentVariable('DEVICE_EVENT_RESULT_DIR',$priorResult,'Process')
    [Environment]::SetEnvironmentVariable('DEVICE_EVENT_APP_IMAGE',$priorImage,'Process')
    Set-Location -LiteralPath $originalLocation.Path
}
exit $exitCode
