# Prove state, replay keys and event links survive an actual disposable DB restart.
[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0',[switch]$ControlCheckpoint)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$originalLocation=Get-Location
$priorResult=[Environment]::GetEnvironmentVariable('HB_RESULT_DIR','Process')
$priorImage=[Environment]::GetEnvironmentVariable('HB_APP_IMAGE','Process')
$docker=$null
$compose=$null
$exitCode=1
try {
    $root=Get-FreshLinkRoot
    Set-Location -LiteralPath $root
    $docker=Assert-FreshLinkDocker
    $runId='hb-storage-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+$PID
    if($runId -notmatch '^hb-storage-\d{14}-\d+$'){throw 'Invalid isolated project name'}
    $results=Join-Path $root ('work/'+$runId)
    [IO.Directory]::CreateDirectory($results)|Out-Null
    $env:HB_RESULT_DIR=$results
    $env:HB_APP_IMAGE=$AppImage
    $compose=@('compose','--project-name',$runId,'-f','compose.heartbeat-tests.yml')
    & $docker @compose config --quiet
    if($LASTEXITCODE -ne 0){throw 'Isolated Compose configuration failed'}
    & $docker @compose up -d --wait --pull never test-db
    if($LASTEXITCODE -ne 0){throw 'Disposable PostgreSQL failed to start'}
    & $docker @compose run --rm --no-deps --pull never --entrypoint alembic test-runner upgrade a01_device_registry
    if($LASTEXITCODE -ne 0){throw 'Empty database migration failed'}
    & $docker @compose run --rm --no-deps --pull never test-runner scripts/check-heartbeat-restart.py seed-a01
    if($LASTEXITCODE -ne 0){throw 'Legacy synthetic fixture setup failed'}
    & $docker @compose run --rm --no-deps --pull never --entrypoint alembic test-runner upgrade head
    if($LASTEXITCODE -ne 0){throw 'A01 to A02 upgrade failed'}
    & $docker @compose run --rm --no-deps --pull never --entrypoint alembic test-runner check
    if($LASTEXITCODE -ne 0){throw 'Migration/model metadata differs'}
    & $docker @compose run --rm --no-deps --pull never test-runner scripts/check-heartbeat-restart.py seed
    if($LASTEXITCODE -ne 0){throw 'Checkpoint setup failed'}
    if($ControlCheckpoint){
        & $docker @compose run --rm --no-deps --pull never test-runner scripts/check-control-restart.py seed
        if($LASTEXITCODE -ne 0){throw 'Control checkpoint setup failed'}
    }
    & $docker @compose restart test-db
    if($LASTEXITCODE -ne 0){throw 'Disposable database restart failed'}
    & $docker @compose up -d --wait --pull never test-db
    if($LASTEXITCODE -ne 0){throw 'Restarted database not healthy'}
    & $docker @compose run --rm --no-deps --pull never test-runner scripts/check-heartbeat-restart.py verify
    if($LASTEXITCODE -ne 0){throw 'Checkpoint after restart failed'}
    if($ControlCheckpoint){
        & $docker @compose run --rm --no-deps --pull never test-runner scripts/check-control-restart.py verify
        if($LASTEXITCODE -ne 0){throw 'Control checkpoint after restart failed'}
        $controlReport=Get-Content -LiteralPath (Join-Path $results 'control-restart.json') -Raw|ConvertFrom-Json
        if($controlReport.phase -ne 'verified_after_postgresql_and_runner_restart'){throw 'Unexpected control verifier result'}
        Write-Output 'PASS control command/lease/attempt survived restart; original payload/ACK recovered without repeat execution; late ACK retained timeout.'
    }
    $report=Get-Content -LiteralPath (Join-Path $results 'heartbeat-restart.json') -Raw|ConvertFrom-Json
    if($report.phase -ne 'verified_after_postgresql_container_and_runner_restart'){throw 'Unexpected verifier result'}
    Write-Output 'PASS A01 synthetic record preserved by A02; metadata matches; heartbeat state/replay/offline/recovery outbox survived PostgreSQL/container restart.'
    Write-Output ('Evidence: '+$results)
    Write-Output 'NOT CHECKED: production MQTT, B delivery, browser or E1.'
    $exitCode=0
} catch {
    Write-Output ('STOP: '+$_.Exception.Message)
} finally {
    if($docker -and $compose){
        # Only this unique owned test project and its synthetic database volume.
        & $docker @compose down --remove-orphans --volumes
        if($LASTEXITCODE -ne 0){$exitCode=1;Write-Output 'STOP: owned test-project cleanup failed'}
    }
    if($null -eq $priorResult){Remove-Item Env:HB_RESULT_DIR -ErrorAction SilentlyContinue}
    else{[Environment]::SetEnvironmentVariable('HB_RESULT_DIR',$priorResult,'Process')}
    if($null -eq $priorImage){Remove-Item Env:HB_APP_IMAGE -ErrorAction SilentlyContinue}
    else{[Environment]::SetEnvironmentVariable('HB_APP_IMAGE',$priorImage,'Process')}
    Set-Location -LiteralPath $originalLocation.Path
}
exit $exitCode
