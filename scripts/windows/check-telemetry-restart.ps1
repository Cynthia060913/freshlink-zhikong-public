# Only a fresh, uniquely named synthetic project may be recreated/cleaned here.
[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'telemetry-restart-guards.ps1')
$originalLocation=Get-Location
$names=@('TELEMETRY_APP_IMAGE','TELEMETRY_C_ARCHIVE','TELEMETRY_RESULT_DIR','TELEMETRY_A_REVISION','TELEMETRY_A_DIRTY')
$prior=@{}
foreach($name in $names){$prior[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$docker=$null
$compose=$null
$ownedScope=$false
$expectedVolumes=@()
$exitCode=1

function Assert-OwnVolumes {
    foreach($volume in $expectedVolumes){
        $found=@(& $docker volume ls --filter ('name=^'+$volume+'$') --format '{{.Name}}')
        if($LASTEXITCODE -ne 0){throw 'Cannot establish current volume ownership'}
        if($found.Count -eq 0){continue}
        if($found.Count -ne 1 -or $found[0] -ne $volume){throw 'Unexpected volume match'}
        $raw=& $docker volume inspect $volume --format '{{json .}}'
        if($LASTEXITCODE -ne 0){throw 'Volume ownership read failed'}
        $item=$raw|ConvertFrom-Json
        Assert-FreshLinkRestartVolume $item $runId $volume
    }
}

function Get-OwnService([string]$service){
    $ids=@(& $docker @compose ps -q $service)
    if($LASTEXITCODE -ne 0 -or $ids.Count -ne 1){throw 'Expected one owned service container'}
    $raw=& $docker inspect $ids[0] --format '{{json .}}'
    if($LASTEXITCODE -ne 0){throw 'Cannot verify service ownership'}
    $item=$raw|ConvertFrom-Json
    if($item.Config.Labels.'com.docker.compose.project' -ne $runId -or
       $item.Config.Labels.'com.docker.compose.service' -ne $service){
        throw 'Unexpected service ownership'
    }
    return $item
}

try {
    $root=Get-FreshLinkRoot
    Set-Location -LiteralPath $root
    $docker=Assert-FreshLinkDocker
    foreach($image in @($AppImage,'postgres:18','eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408')){
        & $docker image inspect $image --format '{{.Id}}'|Out-Null
        if($LASTEXITCODE -ne 0){throw 'Required cached image missing; no pull or install attempted'}
    }
    $revision=& git rev-parse HEAD
    if($LASTEXITCODE -ne 0 -or $revision -notmatch '^[0-9a-f]{40}$'){throw 'Invalid A revision'}
    $dirty=@(& git status --porcelain)
    if($LASTEXITCODE -ne 0){throw 'Cannot verify worktree state'}
    $source='bc71b76368c69952657c318297452482f49881c0'
    & git cat-file -e ($source+':backend/app/modules/telemetry/storage_candidate.py')
    if($LASTEXITCODE -ne 0){throw 'Pinned public C object unavailable; fetch public C branch safely first'}
    $runId='telemetry-restart-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+$PID
    if($runId -notmatch '^telemetry-restart-\d{14}-\d+$'){throw 'Invalid owned project name'}
    $expectedVolumes=@(($runId+'_restart_pg_data'),($runId+'_restart_mqtt_data'))
    $results=Join-Path $root ('work/'+$runId)
    if(Test-Path -LiteralPath $results){throw 'Refusing existing evidence directory'}
    [IO.Directory]::CreateDirectory($results)|Out-Null
    $archive=Join-Path $results 'source-c.tar'
    & git archive --format=tar --output=$archive $source -- backend/app/modules/telemetry
    if($LASTEXITCODE -ne 0){throw 'Pinned C archive failed'}
    $env:TELEMETRY_APP_IMAGE=$AppImage
    $env:TELEMETRY_C_ARCHIVE=$archive
    $env:TELEMETRY_RESULT_DIR=$results
    $env:TELEMETRY_A_REVISION=$revision
    $env:TELEMETRY_A_DIRTY=if($dirty.Count -gt 0){'1'}else{'0'}
    $compose=@('compose','--project-name',$runId,'-f','compose.telemetry-restart-tests.yml')
    $raw=& $docker @compose config --format json
    if($LASTEXITCODE -ne 0){throw 'Invalid restart Compose'}
    $model=$raw|ConvertFrom-Json
    Assert-FreshLinkRestartScope $runId $model
    $existing=@(& $docker ps -aq --filter ('label=com.docker.compose.project='+$runId))
    if($LASTEXITCODE -ne 0 -or $existing.Count -gt 0){throw 'Refusing pre-existing project containers'}
    foreach($volume in $expectedVolumes){
        $existing=@(& $docker volume ls --filter ('name=^'+$volume+'$') --format '{{.Name}}')
        if($LASTEXITCODE -ne 0 -or $existing.Count -gt 0){throw 'Refusing pre-existing volume'}
    }
    $ownedScope=$true
    & $docker @compose up -d --wait --pull never test-db broker
    if($LASTEXITCODE -ne 0){throw 'Owned service startup failed'}
    Assert-OwnVolumes
    $beforeDb=Get-OwnService 'test-db'
    $beforeBroker=Get-OwnService 'broker'
    & $docker @compose run --rm --no-deps --pull never test-runner
    if($LASTEXITCODE -ne 0){throw 'Preparation/preceding MQTT proof failed'}
    $prepared=Get-Content -LiteralPath (Join-Path $results 'restart-prepared.json') -Raw|ConvertFrom-Json
    if($prepared.phase -ne 'restart_prepared' -or $prepared.a_revision -ne $revision){throw 'Preparation not established'}
    Assert-OwnVolumes
    Write-Output 'Recreating ONLY the two checked synthetic services, retaining their owned test volumes.'
    & $docker @compose up -d --force-recreate --wait --pull never test-db broker
    if($LASTEXITCODE -ne 0){throw 'Owned service recreation failed'}
    Assert-OwnVolumes
    $afterDb=Get-OwnService 'test-db'
    $afterBroker=Get-OwnService 'broker'
    if($beforeDb.Id -eq $afterDb.Id -or $beforeBroker.Id -eq $afterBroker.Id){throw 'Container replacement not proved'}
    foreach($pair in @(@($beforeDb,$afterDb,'/var/lib/postgresql',$expectedVolumes[0]),@($beforeBroker,$afterBroker,'/tmp/runtime-store',$expectedVolumes[1]))){
        foreach($container in @($pair[0],$pair[1])){
            $mount=@($container.Mounts|Where-Object {$_.Destination -eq $pair[2]})
            if($mount.Count -ne 1 -or $mount[0].Type -ne 'volume' -or $mount[0].Name -ne $pair[3]){throw 'Persisted mount identity mismatch'}
        }
    }
    & $docker @compose run --rm --no-deps --pull never -e TELEMETRY_RESTART_STAGE=resume test-runner
    if($LASTEXITCODE -ne 0){throw 'Recovery proof failed'}
    $final=Get-Content -LiteralPath (Join-Path $results 'restart-resumed.json') -Raw|ConvertFrom-Json
    if($final.phase -ne 'pg_broker_container_recreation_recovery_passed' -or $final.a_revision -ne $revision -or $final.c_revision -ne $source){throw 'Wrong recovery evidence phase/source'}
    $proof=@{phase='owned_container_recreation_verified';a_revision=$revision;db_container_changed=$true;broker_container_changed=$true;same_owned_volume_names=$true}
    [IO.File]::WriteAllText((Join-Path $results 'restart-container-scope.json'),($proof|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    Write-Output 'PASS PG original inbox/raw/lease/attempt budget and offline MQTT session survived real container replacement.'
    Write-Output ('Evidence: '+$results)
    Write-Output 'NOT CHECKED: abrupt power loss, disk loss, session expiry, formal deployment, real notifications or full E1.'
    $exitCode=0
} catch {Write-Output ('STOP: '+$_.Exception.Message)}
finally {
    if($docker -and $compose -and $ownedScope){
        try {
            Assert-OwnVolumes
            & $docker @compose down --volumes --remove-orphans
            if($LASTEXITCODE -ne 0){throw 'Owned cleanup failed'}
            foreach($volume in $expectedVolumes){
                $left=@(& $docker volume ls --filter ('name=^'+$volume+'$') --format '{{.Name}}')
                if($LASTEXITCODE -ne 0 -or $left.Count -gt 0){throw 'Owned synthetic volume remains'}
            }
            Write-Output 'PASS only this fresh synthetic project/volumes removed; evidence retained.'
        } catch {
            $exitCode=1
            Write-Output ('STOP cleanup: '+$_.Exception.Message+'; retain project '+$runId+' for inspection')
        }
    }
    foreach($name in $names){[Environment]::SetEnvironmentVariable($name,$prior[$name],'Process')}
    Set-Location -LiteralPath $originalLocation.Path
}
exit $exitCode
