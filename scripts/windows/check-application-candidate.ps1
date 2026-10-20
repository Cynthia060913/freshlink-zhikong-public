# Read-only built image plus an owned disposable DB; never deploy or host Python.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$BuildReport)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'application-image-guards.ps1')
$root=Get-FreshLinkRoot
$saved=Get-Location
$keys=@('IMAGE_PROOF_SOURCE','IMAGE_PROOF_IMAGE','IMAGE_PROOF_RESULTS')
$prior=@{}
foreach($key in $keys){$prior[$key]=[Environment]::GetEnvironmentVariable($key,'Process')}
$owned=$false;$cleanup=-1;$proofExit=1;$result=$null;$compose=$null;$docker=$null
try {
    Set-Location -LiteralPath $root
    $inputPath=(Resolve-Path -LiteralPath $BuildReport).Path
    $candidateRoot=[IO.Path]::GetFullPath((Join-Path $root 'work/source-candidates'))+[IO.Path]::DirectorySeparatorChar
    if(-not $inputPath.StartsWith($candidateRoot,[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($inputPath) -ne 'application-build.json'){throw 'Use an owned archive-build report under this workspace'}
    $build=Get-Content -LiteralPath $inputPath -Raw|ConvertFrom-Json
    if($build.status -ne 'candidate_image_built_source_verified_not_application_acceptance' -or $build.source -notmatch '^[0-9a-f]{40}$' -or $build.image_id -notmatch '^sha256:[0-9a-f]{64}$' -or $build.image -notmatch '^freshlink-application-candidate:[0-9a-f]{12}-[0-9a-f]{12}$'){throw 'Image has not passed exact archive/source audit'}
    if((& git rev-parse HEAD).Trim() -ne $build.source -or (& git status --porcelain=v1|Out-String).Trim()){throw 'Use the clean exact build source checkout'}
    $docker=Assert-FreshLinkDocker
    $image=& $docker image inspect $build.image
    if($LASTEXITCODE){throw 'Built image unavailable'}
    $image=($image -join "`n"|ConvertFrom-Json)[0]
    if($image.Id -ne $build.image_id -or $image.Config.Labels.'org.opencontainers.image.revision' -ne $build.source){throw 'Image ID/label changed'}
    $run='image-http-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,12)
    if($run -notmatch '^image-http-\d{14}-[0-9a-f]{12}$'){throw 'Invalid project scope'}
    $result=Join-Path $root ('work/'+$run)
    if(Test-Path -LiteralPath $result){throw 'Refusing existing evidence directory'}
    [IO.Directory]::CreateDirectory($result)|Out-Null
    $env:IMAGE_PROOF_SOURCE=$build.source
    $env:IMAGE_PROOF_IMAGE=$build.image_id
    $env:IMAGE_PROOF_RESULTS=$result
    $compose=@('compose','--project-name',$run,'-f','compose.application-candidate-tests.yml')
    $model=& $docker @compose config --format json
    if($LASTEXITCODE){throw 'Invalid image proof config'}
    $model=($model -join "`n")|ConvertFrom-Json
    if($model.name -ne $run -or -not $model.networks.default.internal -or $model.PSObject.Properties['volumes']){throw 'Uncontrolled network or durable volume'}
    foreach($service in $model.services.PSObject.Properties){
        if($service.Value.PSObject.Properties['ports']){throw 'Host ports forbidden'}
        if($service.Name -ne 'probe' -and $service.Value.PSObject.Properties['volumes']){throw 'Application source/data mounts forbidden'}
        if($service.Name -ne 'test-db' -and $service.Value.image -ne $build.image_id){throw 'Unexpected application image'}
    }
    Assert-FreshLinkDefaultImageCommand $model.services.application
    $existing=@(& $docker ps -aq --filter ('label=com.docker.compose.project='+$run))
    if($LASTEXITCODE -or $existing.Count){throw 'Refusing pre-existing test project'}
    $owned=$true
    & $docker @compose up -d --wait --pull never test-db
    if($LASTEXITCODE){throw 'Owned DB failed to start'}
    & $docker @compose run --rm --no-deps --pull never prestart
    if($LASTEXITCODE){throw 'Owned image migrations/seed failed'}
    & $docker @compose up -d --no-deps --pull never application
    if($LASTEXITCODE){throw 'Default image application failed to start'}
    $applicationId=(& $docker @compose ps --quiet application|Out-String).Trim()
    if($LASTEXITCODE -or $applicationId -notmatch '^[0-9a-f]{12,64}$'){throw 'Cannot identify exactly one running application'}
    $container=& $docker container inspect $applicationId
    if($LASTEXITCODE){throw 'Cannot inspect running application identity'}
    $container=(($container -join "`n")|ConvertFrom-Json)[0]
    Assert-FreshLinkRunningImageIdentity $image $container
    & $docker @compose run --rm --no-deps --pull never probe
    if($LASTEXITCODE){throw 'Image HTTP proof failed'}
    $report=Get-Content -LiteralPath (Join-Path $result 'application-http.json') -Raw|ConvertFrom-Json
    if($report.source -ne $build.source -or $report.status -ne 'archive_image_default_cmd_http_smoke_passed_not_E1'){throw 'Image proof source mismatch'}
    $proofExit=0
    Write-Output 'PASS image default CMD and real HTTP smoke without application source mounts; NOT E1/second-machine acceptance.'
} finally {
    if($owned){& $docker @compose down --remove-orphans;$cleanup=$LASTEXITCODE}
    if($result){
        $receipt=@{source=$build.source;image_id=$build.image_id;project=$run;proof_exit=$proofExit;cleanup_exit=$cleanup}|ConvertTo-Json
        $stream=[IO.File]::Open((Join-Path $result 'image-cleanup.json'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
        try{$bytes=[Text.Encoding]::UTF8.GetBytes($receipt);$stream.Write($bytes,0,$bytes.Length)}finally{$stream.Dispose()}
        Write-Output ('Evidence: '+$result)
    }
    foreach($key in $keys){[Environment]::SetEnvironmentVariable($key,$prior[$key],'Process')}
    Set-Location -LiteralPath $saved.Path
}
if($cleanup -ne 0 -or $proofExit -ne 0){throw 'Image proof or owned cleanup did not pass'}
