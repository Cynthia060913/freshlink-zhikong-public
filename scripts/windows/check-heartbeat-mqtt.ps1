# Real broker -> database -> API, isolated from the application project.
[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$originalLocation=Get-Location
$priorResult=[Environment]::GetEnvironmentVariable('HB_MQTT_RESULT_DIR','Process')
$priorImage=[Environment]::GetEnvironmentVariable('HB_MQTT_APP_IMAGE','Process')
$docker=$null
$compose=$null
$exitCode=1
try {
    $root=Get-FreshLinkRoot
    Set-Location -LiteralPath $root
    $docker=Assert-FreshLinkDocker
    $runId='hb-mqtt-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+$PID
    if($runId -notmatch '^hb-mqtt-\d{14}-\d+$'){throw 'Invalid test project'}
    $results=Join-Path $root ('work/'+$runId)
    [IO.Directory]::CreateDirectory($results)|Out-Null
    $env:HB_MQTT_RESULT_DIR=$results
    $env:HB_MQTT_APP_IMAGE=$AppImage
    $compose=@('compose','--project-name',$runId,'-f','compose.heartbeat-mqtt-tests.yml')
    & $docker @compose config --quiet
    if($LASTEXITCODE -ne 0){throw 'Isolated Compose invalid'}
    & $docker @compose up -d --wait --pull never test-db broker
    if($LASTEXITCODE -ne 0){throw 'Disposable services failed'}
    & $docker @compose run --rm --no-deps --pull never test-runner
    if($LASTEXITCODE -ne 0){throw 'Live heartbeat chain failed'}
    $report=Get-Content -LiteralPath (Join-Path $results 'mqtt-heartbeat.json') -Raw|ConvertFrom-Json
    if($report.phase -ne 'live_authenticated_mqtt_postgresql_api_passed'){throw 'Invalid evidence phase'}
    Write-Output 'PASS live authenticated MQTT -> registered device -> PostgreSQL -> authenticated API; unknown/online/offline/recovery, replay, retain and ACL rejection.'
    Write-Output ('Evidence: '+$results)
    Write-Output 'NOT CHECKED: production TLS provisioning, browser, B/C/E1 or notifications.'
    $exitCode=0
} catch {
    Write-Output ('STOP: '+$_.Exception.Message)
} finally {
    if($docker -and $compose){
        & $docker @compose down --remove-orphans
        if($LASTEXITCODE -ne 0){$exitCode=1;Write-Output 'STOP: owned test cleanup failed'}
    }
    if($null -eq $priorResult){Remove-Item Env:HB_MQTT_RESULT_DIR -ErrorAction SilentlyContinue}
    else{[Environment]::SetEnvironmentVariable('HB_MQTT_RESULT_DIR',$priorResult,'Process')}
    if($null -eq $priorImage){Remove-Item Env:HB_MQTT_APP_IMAGE -ErrorAction SilentlyContinue}
    else{[Environment]::SetEnvironmentVariable('HB_MQTT_APP_IMAGE',$priorImage,'Process')}
    Set-Location -LiteralPath $originalLocation.Path
}
exit $exitCode
