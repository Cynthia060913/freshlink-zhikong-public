# No host Python, dependency installation, production connection or real sending.
[CmdletBinding()]
param([string]$AppImage='freshlink-app:p0')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$originalLocation=Get-Location
$priorImage=[Environment]::GetEnvironmentVariable('C_CANDIDATE_APP_IMAGE','Process')
$docker=$null
$compose=$null
$exitCode=1
try {
    $root=Get-FreshLinkRoot
    Set-Location -LiteralPath $root
    if(-not(Test-Path -LiteralPath 'backend/tests/modules/test_telemetry_storage_postgres_candidate.py')){
        throw 'C candidate test source absent in this branch; no PG test was run.'
    }
    $docker=Assert-FreshLinkDocker
    $runId='c-candidate-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+$PID
    if($runId -notmatch '^c-candidate-\d{14}-\d+$'){throw 'Invalid owned test project'}
    $env:C_CANDIDATE_APP_IMAGE=$AppImage
    $compose=@('compose','--project-name',$runId,'-f','compose.c-candidate-tests.yml')
    & $docker @compose config --quiet
    if($LASTEXITCODE -ne 0){throw 'Disposable compose invalid'}
    & $docker @compose up -d --wait --pull never test-db
    if($LASTEXITCODE -ne 0){throw 'Owned test DB failed to start'}
    & $docker @compose exec -T test-db psql -U freshlink_test -d freshlink_test -Atc 'SELECT version();'
    if($LASTEXITCODE -ne 0){throw 'Test DB version check failed'}
    & $docker @compose run --rm --no-deps --pull never test-runner
    if($LASTEXITCODE -ne 0){throw 'C guarded PG tests failed'}
    $schemaCount=& $docker @compose exec -T test-db psql -U freshlink_test -d freshlink_test -Atc "SELECT count(*) FROM information_schema.schemata WHERE schema_name LIKE 'c_test_%';"
    if($LASTEXITCODE -ne 0 -or ($schemaCount|Out-String).Trim() -ne '0'){
        throw 'C test schema cleanup not verified'
    }
    Write-Output 'PASS C opt-in PG candidate tests; unique test schemas removed.'
    Write-Output 'NOT CHECKED: formal migration/app routing, authenticated HTTP/CSV, actual B delivery, DB process restart, real notifications or E1.'
    $exitCode=0
} catch {Write-Output ('STOP: '+$_.Exception.Message)}
finally {
    if($docker -and $compose){
        & $docker @compose down --remove-orphans
        if($LASTEXITCODE -ne 0){$exitCode=1;Write-Output 'STOP: owned test cleanup failed'}
    }
    [Environment]::SetEnvironmentVariable('C_CANDIDATE_APP_IMAGE',$priorImage,'Process')
    Set-Location -LiteralPath $originalLocation.Path
}
exit $exitCode
