# Owned isolated control test only. Never uses the running application database.
[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$originalLocation=Get-Location
$priorResult=[Environment]::GetEnvironmentVariable('CONTROL_MQTT_RESULT_DIR','Process')
$priorImage=[Environment]::GetEnvironmentVariable('CONTROL_MQTT_APP_IMAGE','Process')
$docker=$null
$compose=$null
$exitCode=1
try {
    $root=Get-FreshLinkRoot
    Set-Location -LiteralPath $root
    $docker=Assert-FreshLinkDocker
    $runId='control-mqtt-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+$PID
    if($runId -notmatch '^control-mqtt-\d{14}-\d+$'){throw 'Invalid owned test project'}
    $results=Join-Path $root ('work/'+$runId)
    [IO.Directory]::CreateDirectory($results)|Out-Null
    $env:CONTROL_MQTT_RESULT_DIR=$results
    $env:CONTROL_MQTT_APP_IMAGE=$AppImage
    $compose=@('compose','--project-name',$runId,'-f','compose.control-mqtt-tests.yml')
    & $docker @compose config --quiet
    if($LASTEXITCODE -ne 0){throw 'Isolated Compose invalid'}
    & $docker @compose up -d --wait --pull never test-db broker
    if($LASTEXITCODE -ne 0){throw 'Disposable services failed'}
    & $docker @compose run --rm --no-deps --pull never test-runner
    if($LASTEXITCODE -ne 0){throw 'Live control chain failed'}
    $report=Get-Content -LiteralPath (Join-Path $results 'mqtt-control.json') -Raw|ConvertFrom-Json
    if($report.phase -ne 'live_api_queue_mqtt_device_ack_postgresql_passed'){throw 'Invalid evidence phase'}
    Write-Output 'PASS authenticated API -> PG queue -> MQTT -> software device -> ACK -> PG/API; replay, denied forged ACK, timeout and late ACK.'
    Write-Output ('Evidence: '+$results)
    Write-Output 'NOT CHECKED: actual B/C integration, browser, production TLS or real notifications/E1.'
    $exitCode=0
} catch {
    Write-Output ('STOP: '+$_.Exception.Message)
} finally {
    if($docker -and $compose){
        & $docker @compose down --remove-orphans
        if($LASTEXITCODE -ne 0){$exitCode=1;Write-Output 'STOP: owned test cleanup failed'}
    }
    if($null -eq $priorResult){Remove-Item Env:CONTROL_MQTT_RESULT_DIR -ErrorAction SilentlyContinue}
    else{[Environment]::SetEnvironmentVariable('CONTROL_MQTT_RESULT_DIR',$priorResult,'Process')}
    if($null -eq $priorImage){Remove-Item Env:CONTROL_MQTT_APP_IMAGE -ErrorAction SilentlyContinue}
    else{[Environment]::SetEnvironmentVariable('CONTROL_MQTT_APP_IMAGE',$priorImage,'Process')}
    Set-Location -LiteralPath $originalLocation.Path
}
exit $exitCode
