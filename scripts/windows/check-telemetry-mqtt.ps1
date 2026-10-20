# Pinned public C source is overlaid ONLY inside a disposable container directory.
[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$originalLocation=Get-Location
$names=@('TELEMETRY_APP_IMAGE','TELEMETRY_C_ARCHIVE','TELEMETRY_RESULT_DIR','TELEMETRY_A_REVISION','TELEMETRY_A_DIRTY')
$prior=@{}
foreach($name in $names){$prior[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$docker=$null
$compose=$null
$exitCode=1
try {
    $root=Get-FreshLinkRoot
    Set-Location -LiteralPath $root
    $docker=Assert-FreshLinkDocker
    $source='e6bb8ef25974cdc6c709811c6424302caa68cb99'
    # The recovery proof's B revision claim must match the actual mounted code.
    & git diff --quiet '8561ce1ec0ea4e996bccbdc5f46d473bb170fc09' -- backend/app/modules/alerts backend/tests/modules/alerts backend/app/alembic/versions/b01_alerts_storage.py
    if($LASTEXITCODE -ne 0){throw 'B proof source changed; review and repin it before running this combination.'}
    $revision=& git rev-parse HEAD
    if($LASTEXITCODE -ne 0 -or $revision -notmatch '^[0-9a-f]{40}$'){throw 'A revision cannot be verified'}
    $dirty=@(& git status --porcelain)
    if($LASTEXITCODE -ne 0){throw 'A worktree status cannot be verified'}
    $env:TELEMETRY_A_REVISION=$revision
    $env:TELEMETRY_A_DIRTY=if($dirty.Count -gt 0){'1'}else{'0'}
    & git cat-file -e ($source+':backend/app/modules/telemetry/storage_candidate.py')
    if($LASTEXITCODE -ne 0){throw 'Pinned public C commit is missing locally; safely fetch the public C branch first.'}
    $runId='telemetry-proof-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+$PID
    if($runId -notmatch '^telemetry-proof-\d{14}-\d+$'){throw 'Invalid owned test project'}
    $results=Join-Path $root ('work/'+$runId)
    [IO.Directory]::CreateDirectory($results)|Out-Null
    $archive=Join-Path $results 'source-c.tar'
    & git archive --format=tar --output=$archive $source -- backend/app/modules/telemetry
    if($LASTEXITCODE -ne 0){throw 'Pinned C module archive failed'}
    $env:TELEMETRY_APP_IMAGE=$AppImage
    $env:TELEMETRY_C_ARCHIVE=$archive
    $env:TELEMETRY_RESULT_DIR=$results
    $compose=@('compose','--project-name',$runId,'-f','compose.telemetry-integration-tests.yml')
    & $docker @compose config --quiet
    if($LASTEXITCODE -ne 0){throw 'Disposable Compose invalid'}
    & $docker @compose up -d --wait --pull never test-db broker
    if($LASTEXITCODE -ne 0){throw 'Owned services failed'}
    & $docker @compose run --rm --no-deps --pull never test-runner
    if($LASTEXITCODE -ne 0){throw 'MQTT/A/C proof failed'}
    $report=Get-Content -LiteralPath (Join-Path $results 'mqtt-telemetry.json') -Raw|ConvertFrom-Json
    if($report.phase -ne 'live_mqtt_a_registry_c_transaction_snapshot_passed' -or $report.c_revision -ne $source){throw 'Wrong evidence phase/source'}
    $inbox=Get-Content -LiteralPath (Join-Path $results 'mqtt-telemetry-inbox.json') -Raw|ConvertFrom-Json
    if($inbox.phase -ne 'durable_receiver_a_queue_c_recovery_passed' -or $inbox.c_revision -ne $source){throw 'Wrong inbox evidence phase/source'}
    $recovery=Get-Content -LiteralPath (Join-Path $results 'pg-b-c-recovery.json') -Raw|ConvertFrom-Json
    if($recovery.phase -ne 'pg_actual_b_c_saved_recovery_passed' -or $recovery.c_revision -ne $source){throw 'Wrong recovery evidence phase/source'}
    Write-Output 'PASS actual MQTT -> actual A registration/B binding -> pinned C PostgreSQL transaction and fixed-snapshot/CSV helpers.'
    Write-Output 'PASS persistent A admission -> receiver restart -> C retry -> lost A checkpoint recovery after real lease expiry.'
    Write-Output 'PASS actual PostgreSQL saved samples -> historical B rule -> frozen C recovery -> authenticated B ASGI resolved readback (not TCP HTTP).'
    Write-Output ('Evidence: '+$results)
    Write-Output 'NOT CHECKED: formal C migration/deployment, DB/broker restart, TCP HTTP, continuous evaluation/recovery scheduling, real notifications, browser or full E1.'
    $exitCode=0
} catch {Write-Output ('STOP: '+$_.Exception.Message)}
finally {
    if($docker -and $compose){
        & $docker @compose down --remove-orphans
        if($LASTEXITCODE -ne 0){$exitCode=1;Write-Output 'STOP: owned cleanup failed'}
    }
    foreach($name in $names){[Environment]::SetEnvironmentVariable($name,$prior[$name],'Process')}
    Set-Location -LiteralPath $originalLocation.Path
}
exit $exitCode
