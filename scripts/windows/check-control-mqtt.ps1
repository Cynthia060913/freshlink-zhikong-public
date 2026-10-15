# Owned isolated control test only. Never uses the running application database.
[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0', [switch]$WithWarehouses)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$originalLocation=Get-Location
$priorResult=[Environment]::GetEnvironmentVariable('CONTROL_MQTT_RESULT_DIR','Process')
$priorImage=[Environment]::GetEnvironmentVariable('CONTROL_MQTT_APP_IMAGE','Process')
$priorWarehouseMode=[Environment]::GetEnvironmentVariable('CONTROL_MQTT_WAREHOUSE_MODE','Process')
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
    $env:CONTROL_MQTT_WAREHOUSE_MODE=if($WithWarehouses){'real'}else{'fixture'}
    $compose=@('compose','--project-name',$runId,'-f','compose.control-mqtt-tests.yml')
    & $docker @compose config --quiet
    if($LASTEXITCODE -ne 0){throw 'Isolated Compose invalid'}
    & $docker @compose up -d --wait --pull never test-db broker
    if($LASTEXITCODE -ne 0){throw 'Disposable services failed'}
    & $docker @compose run --rm --no-deps --pull never test-runner
    if($LASTEXITCODE -ne 0){throw 'Live control chain failed'}
    $report=Get-Content -LiteralPath (Join-Path $results 'mqtt-control.json') -Raw|ConvertFrom-Json
    if($report.phase -ne 'live_api_queue_mqtt_device_ack_postgresql_passed'){throw 'Invalid evidence phase'}
    if($report.warehouse_mode -ne $env:CONTROL_MQTT_WAREHOUSE_MODE){throw 'Wrong warehouse evidence mode'}
    if($WithWarehouses -and (-not $report.binding_proof.same_binding_retry_safe)){throw 'Missing actual warehouse binding proof'}
    Write-Output 'PASS authenticated API -> PG queue -> MQTT -> software device -> ACK -> PG/API; replay, denied forged ACK, timeout and late ACK.'
    Write-Output ('Evidence: '+$results)
    if($WithWarehouses){Write-Output 'PASS actual B warehouse HTTP create -> A one-time binding -> actual B lookup, no lookup override. Warehouse DDL was TEST ONLY, not a formal B migration.'}
    Write-Output 'NOT CHECKED: formal B migration/alerts, C persistence, browser, production TLS or real notifications/E1.'
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
    if($null -eq $priorWarehouseMode){Remove-Item Env:CONTROL_MQTT_WAREHOUSE_MODE -ErrorAction SilentlyContinue}
    else{[Environment]::SetEnvironmentVariable('CONTROL_MQTT_WAREHOUSE_MODE',$priorWarehouseMode,'Process')}
    Set-Location -LiteralPath $originalLocation.Path
}
exit $exitCode
