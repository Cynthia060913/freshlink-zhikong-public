[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0',[switch]$ControlEntry,[switch]$RuntimeProcesses,[switch]$NotificationRunner,[switch]$RecoveryContinuation,[switch]$RecoveryPreparationWatch,[switch]$LinkedCorrection)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$root=Get-FreshLinkRoot
$original=Get-Location
$keys=@('GATED_APP_IMAGE','GATED_RESULT_DIR','GATED_SOURCE_REVISION','GATED_SOURCE_DIRTY','GATED_CONTROL_ENTRY')
$prior=@{}
foreach($key in $keys){$prior[$key]=[Environment]::GetEnvironmentVariable($key,'Process')}
$compose=$null
$docker=$null
$exitCode=1
$cleanup=1
try {
    if(@($ControlEntry,$RuntimeProcesses,$NotificationRunner,$RecoveryContinuation,$RecoveryPreparationWatch,$LinkedCorrection).Where({$_}).Count -gt 1){throw 'Select one proof mode; results must not be conflated'}
    if($LinkedCorrection){$RecoveryPreparationWatch=$true}
    if($RecoveryPreparationWatch){$RecoveryContinuation=$true}
    Set-Location -LiteralPath $root
    $docker=Assert-FreshLinkDocker
    foreach($image in @($AppImage,'postgres:18','eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408')){
        & $docker image inspect $image --format '{{.Id}}'
        if($LASTEXITCODE){throw 'Required cached image missing; no automatic pulls'}
    }
    # A62 integrates the exact C correction lease/wire/receipt port source.
    # Keep the identity gate, including the unchanged C01 proposal.
    & git diff --quiet 5b8018e19fda01f44f1c6f51ad83d7a95b33b045 -- backend/app/modules/telemetry backend/migration_candidates/c01_telemetry_storage.py
    if($LASTEXITCODE){throw 'C source changed; review this proof before repinning'}
    $revision=(& git rev-parse HEAD).Trim()
    if($LASTEXITCODE -or $revision -notmatch '^[0-9a-f]{40}$'){throw 'Source identity unavailable'}
    $dirty=@(& git status --porcelain)
    if($LASTEXITCODE){throw 'Worktree identity unavailable'}
    $project='gated-'+[guid]::NewGuid().ToString('N')
    $results=Join-Path $root ('work/'+$project)
    [IO.Directory]::CreateDirectory($results)|Out-Null
    $env:GATED_APP_IMAGE=$AppImage
    $env:GATED_RESULT_DIR=$results
    $env:GATED_SOURCE_REVISION=$revision
    $env:GATED_SOURCE_DIRTY=if($dirty.Count){'1'}else{'0'}
    $env:GATED_CONTROL_ENTRY=if($ControlEntry){'1'}else{'0'}
    $compose=@('compose','--project-name',$project,'-f','compose.gated-telemetry-tests.yml')
    if($RuntimeProcesses -or $RecoveryContinuation){$compose+=@('-f','compose.runtime-composition-tests.yml')}
    if($RecoveryContinuation){$compose+=@('-f','compose.recovery-continuation-tests.yml')}
    if($RecoveryPreparationWatch){$compose+=@('-f','compose.recovery-preparation-watch-tests.yml')}
    if($LinkedCorrection){$compose+=@('-f','compose.linked-correction-tests.yml')}
    if($NotificationRunner){$compose+=@('-f','compose.notification-runtime-tests.yml')}
    & $docker @compose config --quiet
    if($LASTEXITCODE){throw 'Owned compose configuration invalid'}
    & $docker @compose up -d --wait --pull never test-db broker
    if($LASTEXITCODE){throw 'Owned services failed'}
    $runner=$project+'-runner'
    & $docker @compose run --name $runner --no-deps --pull never test-runner
    $runExit=$LASTEXITCODE
    & $docker logs $runner 2>&1 | Tee-Object -FilePath (Join-Path $results 'runner.log')
    if($runExit){throw 'Actual gated MQTT/PG/HTTP proof failed'}
    $reportName=if($NotificationRunner){'notification-composition.json'}elseif($RuntimeProcesses -or $RecoveryContinuation){'runtime-composition.json'}else{'gated-telemetry.json'}
    $phase=if($NotificationRunner){'actual_runtime_to_persistent_synthetic_notifications_passed'}elseif($RuntimeProcesses -or $RecoveryContinuation){'actual_runtime_processes_tcp_b_csv_passed'}else{'actual_mqtt_gated_c_http_csv_passed'}
    $report=Get-Content -LiteralPath (Join-Path $results $reportName) -Raw|ConvertFrom-Json
    if($report.phase -ne $phase -or $report.source_revision -ne $revision -or $report.complete_e1 -or $report.notifications_sent -ne 0){throw 'Wrong proof identity/scope'}
    if(($RuntimeProcesses -or $RecoveryContinuation) -and (($report.actual_runtime_processes -join ',') -ne 'receive,store,evaluate,deliver-high' -or -not $report.actual_tcp_http_api)){throw 'Runtime process/TCP proof incomplete'}
    if($RecoveryContinuation -and (-not $report.new_mqtt_admission_refrozen_while_workers_live -or -not $report.actual_recovery_http_and_resolved_readback)){throw 'Live recovery continuation proof incomplete'}
    if($RecoveryPreparationWatch -and -not $report.actual_preparation_watch_restart){throw 'Preparation watch/restart proof incomplete'}
    if($LinkedCorrection -and -not $report.actual_late_mqtt_correction_background_tcp){throw 'Linked correction runtime proof incomplete'}
    if($NotificationRunner -and (-not $report.synthetic_sinks_only -or -not $report.actual_pg_dispatch_lease -or -not $report.restart_sends_only_missing_channel -or -not $report.third_delivery_process_no_resend -or $report.persistent_attempt_rows -ne 3)){throw 'Persistent synthetic notification proof incomplete'}
    if($ControlEntry -and -not $report.actual_control_and_subsequent_c_data.actual_control_process_entry){throw 'Control process entry was not exercised'}
    $exitCode=0
    $label=if($LinkedCorrection){'live MQTT/recovery/linked correction worker/TCP B traceability'}elseif($NotificationRunner){'runtime/C/B/persistent synthetic notification runner'}elseif($RecoveryContinuation){'live workers/new MQTT/refreeze/TCP B recovery'}elseif($RuntimeProcesses){'runtime processes/TCP B/raw/history/CSV'}else{'gated MQTT/C/JWT-HTTP/CSV'}
    Write-Output ('PASS '+$label+' segment; evidence: '+$results)
    if($LinkedCorrection){Write-Output 'NOT FULL E1: recovery sending remains explicit; no real notification receipts or final second-machine acceptance.'}
    else{Write-Output 'NOT FULL E1: no unattended recovery/correction scheduling, real notification receipts or final second-machine acceptance.'}
} catch {Write-Output ('STOP: '+$_.Exception.Message)}
finally {
    if($docker -and $compose){
        & $docker @compose down --remove-orphans
        if($LASTEXITCODE){$exitCode=1;$cleanup=0}
    }
    if($compose){@{test_exit=$exitCode;owned_cleanup_ok=[bool]$cleanup;project=$project}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $results 'cleanup.json')}
    foreach($key in $keys){[Environment]::SetEnvironmentVariable($key,$prior[$key],'Process')}
    Set-Location -LiteralPath $original.Path
}
exit $exitCode
