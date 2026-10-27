# Cached images only; a random owned Compose project, no host ports or sends.
[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$root=Get-FreshLinkRoot
$location=Get-Location
$keys=@('GATED_APP_IMAGE','GATED_RESULT_DIR','GATED_SOURCE_REVISION','GATED_SOURCE_DIRTY')
$prior=@{}
foreach($key in $keys){$prior[$key]=[Environment]::GetEnvironmentVariable($key,'Process')}
$compose=$null;$docker=$null;$exitCode=1;$cleanup=$false
try{
    Set-Location -LiteralPath $root
    $docker=Assert-FreshLinkDocker
    foreach($image in @($AppImage,'postgres:18','eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408')){
        $null=& $docker image inspect $image --format '{{.Id}}'
        if($LASTEXITCODE){throw 'Required cached image missing'}
    }
    $source=(& git rev-parse HEAD).Trim()
    if($LASTEXITCODE -or $source -notmatch '^[0-9a-f]{40}$'){throw 'Missing source identity'}
    $project='sustained-'+[guid]::NewGuid().ToString('N')
    $out=Join-Path $root ('work/'+$project)
    [IO.Directory]::CreateDirectory($out)|Out-Null
    $env:GATED_APP_IMAGE=$AppImage
    $env:GATED_RESULT_DIR=$out
    $env:GATED_SOURCE_REVISION=$source
    $env:GATED_SOURCE_DIRTY=if(@(& git status --porcelain).Count){'1'}else{'0'}
    $command="import subprocess,sys; subprocess.run([sys.executable,'-m','alembic','upgrade','head'],check=True); subprocess.run([sys.executable,'-m','alembic','check'],check=True); subprocess.run([sys.executable,'app/initial_data.py'],check=True); subprocess.run([sys.executable,'scripts/check-sustained-telemetry.py'],check=True)"
    $overlay=Join-Path $out 'overlay.json'
    @{services=@{'test-runner'=@{command=@($command)}}}|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $overlay -Encoding utf8
    $compose=@('compose','--project-name',$project,'-f','compose.gated-telemetry-tests.yml','-f',$overlay)
    & $docker @compose config --quiet
    if($LASTEXITCODE){throw 'Invalid owned Compose configuration'}
    & $docker @compose up -d --wait --pull never test-db broker
    if($LASTEXITCODE){throw 'Owned dependencies failed'}
    $runner=$project+'-runner'
    & $docker @compose run --name $runner --no-deps --pull never test-runner
    $runExit=$LASTEXITCODE
    & $docker logs $runner 2>&1|Set-Content -LiteralPath (Join-Path $out 'runner.log')
    if($runExit){throw 'Sustained telemetry proof failed'}
    $report=Get-Content -LiteralPath (Join-Path $out 'sustained-telemetry.json') -Raw|ConvertFrom-Json
    if($report.phase -ne 'sustained_real_mqtt_pg_passed' -or $report.source_revision -ne $source -or $report.complete_e1 -or $report.notifications_sent -ne 0 -or -not $report.frontier_caught_up){throw 'Invalid proof result or identity'}
    $exitCode=0
}catch{Write-Output ('STOP: '+$_.Exception.Message)}
finally{
    if($docker -and $compose){
        & $docker @compose down --remove-orphans
        $cleanup=($LASTEXITCODE -eq 0)
        $left=@(& $docker ps -aq --filter ('label=com.docker.compose.project='+$project))
        $net=@(& $docker network ls -q --filter ('label=com.docker.compose.project='+$project))
        if($LASTEXITCODE -or $left.Count -or $net.Count){$cleanup=$false}
        if(-not $cleanup){$exitCode=1}
        @{test_exit=$exitCode;cleanup_verified=$cleanup;project=$project}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $out 'cleanup.json')
        Write-Output ('Evidence: '+$out)
    }
    foreach($key in $keys){[Environment]::SetEnvironmentVariable($key,$prior[$key],'Process')}
    Set-Location -LiteralPath $location.Path
}
if($exitCode -ne 0){exit $exitCode}
Write-Output 'PASS sustained real MQTT/PG, original raw preserved; not complete E1 or second-machine acceptance.'
