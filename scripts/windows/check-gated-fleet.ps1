[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$root=Get-FreshLinkRoot
$saved=Get-Location
$keys=@('GATED_FLEET_APP_IMAGE','GATED_FLEET_SOURCE','GATED_FLEET_DIRTY','GATED_FLEET_RESULTS')
$prior=@{}
foreach($key in $keys){$prior[$key]=[Environment]::GetEnvironmentVariable($key,'Process')}
$docker=$null;$compose=$null;$out=$null;$code=1;$clean=1
try {
    Set-Location -LiteralPath $root
    if(git status --porcelain){throw 'Clean committed source required'}
    $source=(git rev-parse HEAD).Trim()
    if($LASTEXITCODE -or $source -notmatch '^[0-9a-f]{40}$'){throw 'Missing source identity'}
    $docker=Assert-FreshLinkDocker
    foreach($image in @($AppImage,'postgres:18','eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408')){
        & $docker image inspect $image --format '{{.Id}}'
        if($LASTEXITCODE){throw 'Required cached image missing; no automatic pull'}
    }
    $project='gated-fleet-'+[guid]::NewGuid().ToString('N')
    $out=Join-Path $root ('work/'+$project)
    if(Test-Path -LiteralPath $out){throw 'Refusing existing evidence directory'}
    [IO.Directory]::CreateDirectory($out)|Out-Null
    $env:GATED_FLEET_APP_IMAGE=$AppImage
    $env:GATED_FLEET_SOURCE=$source
    $env:GATED_FLEET_DIRTY='0'
    $env:GATED_FLEET_RESULTS=$out
    $compose=@('compose','--project-name',$project,'-f','compose.gated-fleet-tests.yml')
    $config=& $docker @compose config --format json
    if($LASTEXITCODE){throw 'Invalid owned compose'}
    $model=($config -join "`n")|ConvertFrom-Json
    if(-not $model.networks.default.internal -or $model.PSObject.Properties['volumes']){throw 'Internal-only/no named volumes required'}
    foreach($service in $model.services.PSObject.Properties){if($service.Value.PSObject.Properties['ports']){throw 'No host ports allowed'}}
    & $docker @compose up -d --wait --pull never test-db broker
    if($LASTEXITCODE){throw 'Owned services failed'}
    $runner=$project+'-runner'
    & $docker @compose run --name $runner --no-deps --pull never test-runner
    $runExit=$LASTEXITCODE
    & $docker logs $runner 2>&1|Tee-Object -FilePath (Join-Path $out 'runner.log')
    if($runExit){throw 'Actual fleet integration failed; evidence retained'}
    $result=Get-Content -LiteralPath (Join-Path $out 'gated-fleet.json') -Raw|ConvertFrom-Json
    if($result.source -ne $source -or $result.phase -ne 'gated_14_device_storage_control_segment_not_E1' -or $result.complete_e1 -or $result.notifications_sent -ne 0){throw 'Wrong proof source/scope'}
    if((git rev-parse HEAD).Trim() -ne $source -or (git status --porcelain)){throw 'Source changed during proof'}
    $code=0
    Write-Output ('PASS gated 2 warehouses/3 batches/14-device storage+control segment: '+$out)
    Write-Output 'NOT FULL E1: multi-batch normal/high evaluation and B ASGI HTTP included; no C recovery/two receipt/second-machine acceptance.'
} catch {Write-Output ('STOP: '+$_.Exception.Message)}
finally {
    if($docker -and $compose){& $docker @compose down --remove-orphans;if($LASTEXITCODE){$clean=0;$code=1}}
    if($out){@{test_exit=$code;owned_cleanup_ok=[bool]$clean;project=$project}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $out 'cleanup.json')}
    foreach($key in $keys){[Environment]::SetEnvironmentVariable($key,$prior[$key],'Process')}
    Set-Location -LiteralPath $saved.Path
}
exit $code
