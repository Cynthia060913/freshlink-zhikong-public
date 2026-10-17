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
    $source='6d05d75d58dc23de48872ba2985a8f129c35ae49'
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
    Write-Output 'PASS actual MQTT -> actual A registration/B binding -> pinned C PostgreSQL transaction and fixed-snapshot/CSV helpers.'
    Write-Output ('Evidence: '+$results)
    Write-Output 'NOT CHECKED: durable production MQTT inbox, formal C migration/HTTP, B evaluation/notifications, browser or full E1.'
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
