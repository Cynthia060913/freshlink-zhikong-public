# Owned, temporary PostgreSQL proof; no original data or real notifications.
[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$root=Get-FreshLinkRoot
$docker=Assert-FreshLinkDocker
$project='forecast-preview-'+[guid]::NewGuid().ToString('N')
$results=Join-Path $root ('work/'+$project)
[IO.Directory]::CreateDirectory($results)|Out-Null
$source=(& git -C $root rev-parse HEAD).Trim()
if($LASTEXITCODE -or $source -notmatch '^[0-9a-f]{40}$'){throw 'Source identity unavailable'}
$dirty=[bool](& git -C $root status --porcelain)
$savedImage=$env:A01_APP_IMAGE; $savedResults=$env:A01_RESULT_DIR
$env:A01_APP_IMAGE=$AppImage; $env:A01_RESULT_DIR=$results
$override=Join-Path $results 'override.yml'
$overrideText=@'
services:
  test-runner:
    environment:
      MYPY_CACHE_DIR: /tmp/forecast-mypy
    command:
      - >-
        set -e;
        alembic upgrade head;
        alembic check;
        python -m unittest discover -s tests/modules -p test_forecast_candidate.py -v;
        coverage run --source=app.modules.iot.forecast_window,app.modules.iot.forecast_observations,app.modules.iot.forecast_preview,app.modules.iot.forecast_routes -m pytest -p no:cacheprovider --confcutdir=tests/modules/iot tests/modules/iot --junitxml=/results/pytest.xml;
        coverage report --fail-under=90;
        coverage json -o /results/coverage.json
'@
[IO.File]::WriteAllText($override,$overrideText,[Text.UTF8Encoding]::new($false))
$compose=@('compose','--project-name',$project,'-f',(Join-Path $root 'compose.a01-tests.yml'),'-f',$override)
$code=1; $clean=$false
try {
    & $docker @compose config --quiet
    if($LASTEXITCODE){throw 'Invalid owned forecast test configuration'}
    & $docker @compose up -d --wait --pull never test-db
    if($LASTEXITCODE){throw 'Owned PostgreSQL failed'}
    & $docker @compose run --rm --no-deps --pull never test-runner 2>&1 | Tee-Object -FilePath (Join-Path $results 'runner.log')
    if($LASTEXITCODE){throw 'Forecast proof failed; retain results'}
    if((& git -C $root rev-parse HEAD).Trim() -ne $source){throw 'Source changed during proof'}
    $code=0
} catch { Write-Output $_.Exception.Message }
finally {
    & $docker @compose down --remove-orphans
    $clean=($LASTEXITCODE -eq 0)
    if(@(& $docker ps -aq --filter ('label=com.docker.compose.project='+$project)).Count -or @(& $docker network ls -q --filter ('label=com.docker.compose.project='+$project)).Count){$clean=$false}
    $report=[pscustomobject]@{source=$source;source_dirty=$dirty;test_exit=$code;owned_cleanup_ok=$clean;project=$project;scope='C pure computation and A input/PG/HTTP; not E1 or model-effect acceptance';real_notifications=0;business_warning_accepted=$false}
    [IO.File]::WriteAllText((Join-Path $results 'scope.json'),($report|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    $env:A01_APP_IMAGE=$savedImage; $env:A01_RESULT_DIR=$savedResults
}
Write-Output ('Evidence: '+$results)
if(-not $clean){exit 1}
exit $code
