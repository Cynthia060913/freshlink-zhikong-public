# Only this random no-port/no-volume test project is started and removed.
[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$priorLocation=Get-Location
$names=@('FLEET_APP_IMAGE','FLEET_RESULT_DIR','FLEET_SOURCE','FLEET_SOURCE_DIRTY')
$saved=@{}
foreach($name in $names){$saved[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$docker=$null
$compose=$null
$results=$null
$testExit=1
$cleanupExit=1
try {
    $root=Get-FreshLinkRoot
    Set-Location -LiteralPath $root
    $docker=Assert-FreshLinkDocker
    foreach($image in @($AppImage,'eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408')){
        & $docker image inspect $image --format '{{.Id}}' | Out-Null
        if($LASTEXITCODE -ne 0){throw 'Required cached image missing; no pull or install attempted'}
    }
    $source=(& git rev-parse HEAD).Trim()
    if($LASTEXITCODE -ne 0 -or $source -notmatch '^[0-9a-f]{40}$'){throw 'Missing source identity'}
    $beforeStatus=(& git status --porcelain=v1 | Out-String)
    $runId='fleet-proof-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+$PID
    if($runId -notmatch '^fleet-proof-\d{14}-\d+$'){throw 'Invalid owned project'}
    $results=Join-Path $root ('work/'+$runId)
    if(Test-Path -LiteralPath $results){throw 'Refusing to overwrite evidence'}
    [IO.Directory]::CreateDirectory($results)|Out-Null
    $env:FLEET_APP_IMAGE=$AppImage
    $env:FLEET_RESULT_DIR=$results
    $env:FLEET_SOURCE=$source
    $env:FLEET_SOURCE_DIRTY=if($beforeStatus.Trim()){'1'}else{'0'}
    $compose=@('compose','--project-name',$runId,'-f','compose.fleet-tests.yml')
    $model=(& $docker @compose config --format json) | ConvertFrom-Json
    if($LASTEXITCODE -ne 0){throw 'Invalid owned test configuration'}
    foreach($service in $model.services.PSObject.Properties){
        if($service.Value.PSObject.Properties.Name -contains 'ports' -and $service.Value.ports){throw 'Fleet proof must publish no host ports'}
    }
    if($model.networks.default.internal -ne $true -or $model.PSObject.Properties.Name -contains 'volumes'){throw 'Fleet proof requires private network and no named volumes'}
    & $docker @compose up -d --pull never broker
    if($LASTEXITCODE -ne 0){throw 'Owned broker did not start'}
    & $docker @compose run --rm --no-deps --pull never test-runner
    if($LASTEXITCODE -ne 0){throw 'Actual fleet proof failed'}
    $report=Get-Content -LiteralPath (Join-Path $results 'fleet.json') -Raw | ConvertFrom-Json
    if($report.phase -ne 'actual_fourteen_device_mqtt_fleet_passed_not_E1' -or $report.source -ne $source){throw 'Wrong fleet evidence identity'}
    if((& git rev-parse HEAD).Trim() -ne $source -or (& git status --porcelain=v1 | Out-String) -ne $beforeStatus){throw 'Checkout changed during proof'}
    $testExit=0
    Write-Output 'PASS actual 14-device producer/control proof. NOT the full shared application/E1.'
} catch {Write-Output ('STOP: '+$_.Exception.Message)}
finally {
    if($docker -and $compose){
        & $docker @compose down --remove-orphans
        $cleanupExit=$LASTEXITCODE
    }
    if($results){
        $receipt=@{source=$source;project=$runId;test_exit=$testExit;cleanup_exit=$cleanupExit}
        [IO.File]::WriteAllText((Join-Path $results 'host-cleanup.json'),($receipt|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
        Write-Output ('Evidence: '+$results)
    }
    foreach($name in $names){[Environment]::SetEnvironmentVariable($name,$saved[$name],'Process')}
    Set-Location -LiteralPath $priorLocation
}
if($testExit -ne 0 -or $cleanupExit -ne 0){exit 1}
