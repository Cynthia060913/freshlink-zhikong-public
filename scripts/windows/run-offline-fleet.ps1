# Verify first; -RunFleet explicitly opts into a disposable, internal-only proof.
[CmdletBinding()]
param(
    [string]$Package='',
    [Parameter(Mandatory=$true)][string]$ExpectedManifestSha256,
    [switch]$RunFleet
)
$ErrorActionPreference='Stop'
if([string]::IsNullOrWhiteSpace($Package)){$Package=$PSScriptRoot}
. (Join-Path $PSScriptRoot 'offline-kit-guards.ps1')
$manifest=Assert-OfflineKit $Package $ExpectedManifestSha256
if(-not $RunFleet){Write-Output 'PASS transport integrity only; no Docker operation, not E1 or second-machine acceptance.';return}
$docker=Get-OfflineLocalDocker
$root=(Resolve-Path -LiteralPath $Package).Path
$run='offline-fleet-'+[guid]::NewGuid().ToString('N')
$out=Join-Path (Split-Path $root -Parent) $run
if(Test-Path -LiteralPath $out){throw 'No existing evidence directory may be reused'}
[IO.Directory]::CreateDirectory($out)|Out-Null
$prior=Get-Location
$names=@('OFFLINE_APP','OFFLINE_PG','OFFLINE_MQTT','OFFLINE_SOURCE','OFFLINE_FIXTURES','OFFLINE_RESULTS')
$saved=@{};foreach($name in $names){$saved[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$owned=$false;$compose=$null;$code=1;$cleanup=-1
try {
    # No tags are transported: loading content-addressed IDs cannot retarget tags.
    & $docker image load --quiet --input (Join-Path $root 'images.tar')
    if($LASTEXITCODE){throw 'Image archive load failed'}
    foreach($item in $manifest.images){
        $value=& $docker image inspect $item.id
        if($LASTEXITCODE){throw 'An exact bundled image is unavailable after load'}
        $image=($value -join "`n"|ConvertFrom-Json)[0]
        if($image.Id -cne $item.id -or ($image.Os+'/'+$image.Architecture) -cne $item.platform){throw 'Loaded image identity mismatch'}
        if($item.role -ceq 'application' -and $image.Config.Labels.'org.opencontainers.image.revision' -cne $manifest.source){throw 'Application source label mismatch'}
    }
    $env:OFFLINE_APP=($manifest.images|Where-Object role -eq 'application').id
    $env:OFFLINE_PG=($manifest.images|Where-Object role -eq 'postgres').id
    $env:OFFLINE_MQTT=($manifest.images|Where-Object role -eq 'mqtt').id
    $env:OFFLINE_SOURCE=$manifest.source
    $env:OFFLINE_FIXTURES=$root
    $env:OFFLINE_RESULTS=$out
    $composePath=Join-Path $out 'owned-compose.yml'
    $yaml=@'
services:
  test-db:
    image: ${OFFLINE_PG:?}
    environment:
      POSTGRES_USER: freshlink_test
      POSTGRES_PASSWORD: gated_fleet_disposable_only
      POSTGRES_DB: freshlink_test
    tmpfs: [/var/lib/postgresql]
    healthcheck:
      test: [CMD-SHELL, pg_isready -U freshlink_test -d freshlink_test]
      interval: 2s
      timeout: 5s
      retries: 30
  broker:
    image: ${OFFLINE_MQTT:?}
    entrypoint: [sh, /fixtures/start.sh]
    volumes:
      - ${OFFLINE_FIXTURES:?}:/fixtures:ro
  test-runner:
    image: ${OFFLINE_APP:?}
    working_dir: /app/backend
    environment:
      PROJECT_NAME: FreshLink-test
      FASTAPI_ENV: development
      SECRET_KEY: gated-fleet-disposable-test-key-only
      FIRST_SUPERUSER: admin@example.com
      FIRST_SUPERUSER_PASSWORD: gated-fleet-disposable-admin-only
      DATABASE_URL: postgresql://freshlink_test:gated_fleet_disposable_only@test-db:5432/freshlink_test
      SMTP_HOST: ""
      SMTP_PASSWORD: ""
      EMAILS_FROM_EMAIL: ""
      SENTRY_DSN: ""
      PYTHONDONTWRITEBYTECODE: "1"
      PYTHONPATH: /app/backend
      FRESHLINK_GATED_FLEET_PROOF: "1"
      GATED_FLEET_SOURCE: ${OFFLINE_SOURCE:?}
      GATED_FLEET_DIRTY: "0"
    volumes:
      - ${OFFLINE_RESULTS:?}:/results
    entrypoint: [/app/.venv/bin/python, -B, -c]
    command:
      - >-
        import subprocess,sys;
        subprocess.run([sys.executable,'scripts/check-gated-fleet.py','--preflight'],check=True);
        subprocess.run([sys.executable,'-m','alembic','upgrade','head'],check=True);
        subprocess.run([sys.executable,'-m','alembic','check'],check=True);
        subprocess.run([sys.executable,'app/initial_data.py'],check=True);
        subprocess.run([sys.executable,'scripts/check-gated-fleet.py'],check=True)
networks:
  default:
    internal: true
'@
    [IO.File]::WriteAllText($composePath,$yaml,[Text.UTF8Encoding]::new($false))
    Set-Location -LiteralPath $out
    # Ignore any ambient project .env; all six substitutions are explicitly set.
    $compose=@('compose','--env-file','NUL','--project-name',$run,'-f',$composePath)
    $config=& $docker @compose config --format json
    if($LASTEXITCODE){throw 'Invalid owned Compose'}
    $model=($config -join "`n"|ConvertFrom-Json)
    if($model.name -cne $run -or -not $model.networks.default.internal -or $model.PSObject.Properties['volumes']){throw 'Internal-only disposable resources required'}
    foreach($service in $model.services.PSObject.Properties){
        if($service.Value.PSObject.Properties['ports'] -or $service.Value.PSObject.Properties['privileged'] -or $service.Value.PSObject.Properties['network_mode']){throw 'Unsafe service model'}
    }
    $mounts=@($model.services.'test-runner'.volumes)
    if($mounts.Count -ne 1 -or $mounts[0].target -cne '/results' -or
        -not [string]::Equals($mounts[0].source,$out,[StringComparison]::OrdinalIgnoreCase)){throw 'No application source/data mount is allowed'}
    $existing=@(& $docker ps -aq --filter ('label=com.docker.compose.project='+$run))
    if($LASTEXITCODE -or $existing.Count){throw 'Refusing existing project containers'}
    $existingNetworks=@(& $docker network ls -q --filter ('label=com.docker.compose.project='+$run))
    if($LASTEXITCODE -or $existingNetworks.Count){throw 'Refusing existing project networks'}
    $owned=$true
    & $docker @compose up -d --wait --pull never test-db broker
    if($LASTEXITCODE){throw 'Disposable services failed'}
    $runner=$run+'-runner'
    & $docker @compose run --name $runner --no-deps --pull never test-runner
    $testExit=$LASTEXITCODE
    # Windows PowerShell 5.1 wraps native stderr log lines as ErrorRecords.
    # Preserve them without treating Alembic INFO as failure; still check the
    # actual logs command exit code and the fleet process exit code separately.
    $logPreference=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        & $docker logs $runner 2>&1|ForEach-Object {$_.ToString()}|Out-File -LiteralPath (Join-Path $out 'runner.log') -Encoding utf8
        $logExit=$LASTEXITCODE
    } finally {$ErrorActionPreference=$logPreference}
    if($logExit){throw 'Cannot collect runner logs; evidence incomplete'}
    if($testExit){throw 'Fleet proof failed; retained evidence must be reviewed'}
    $result=Get-Content -LiteralPath (Join-Path $out 'gated-fleet.json') -Raw -Encoding UTF8|ConvertFrom-Json
    if($result.source -cne $manifest.source -or $result.dirty -or $result.complete_e1 -or $result.notifications_sent -ne 0 -or
        $result.logical_devices -ne 14 -or $result.offline_recovered_trace_devices -ne 14 -or
        $result.actuators_with_real_execution_ack_and_later_cooling_sample -ne 7){throw 'Wrong proof source/result/scope'}
    $code=0
} finally {
    if($owned){
        & $docker @compose down --remove-orphans
        $cleanup=$LASTEXITCODE
        $remaining=@(& $docker ps -aq --filter ('label=com.docker.compose.project='+$run))
        if($LASTEXITCODE -or $remaining.Count){$cleanup=1}
        $remainingNetworks=@(& $docker network ls -q --filter ('label=com.docker.compose.project='+$run))
        if($LASTEXITCODE -or $remainingNetworks.Count){$cleanup=1}
    }
    $record=@{source=$manifest.source;manifest_sha256=$ExpectedManifestSha256;project=$run;proof_exit=$code;cleanup_exit=$cleanup;backend_source_mount=$false;complete_e1=$false;second_machine_operator_confirmation_required=$true}
    [IO.File]::WriteAllText((Join-Path $out 'offline-cleanup.json'),($record|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    foreach($name in $names){[Environment]::SetEnvironmentVariable($name,$saved[$name],'Process')}
    Set-Location -LiteralPath $prior.Path
    Write-Output ('Evidence: '+$out)
}
if($code -or $cleanup){throw 'Offline proof/cleanup incomplete'}
Write-Output 'PASS offline image-only 14-device segment. NOT full E1, notification receipts, final delivery or independently confirmed second machine.'
