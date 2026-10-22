# Full backend tests against an isolated, temporary PostgreSQL database.
[CmdletBinding()]
param([string]$AppImage = 'freshlink-app:p0')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')
$originalLocation = Get-Location
$priorResultDir = [Environment]::GetEnvironmentVariable('A01_RESULT_DIR', 'Process')
$priorAppImage = [Environment]::GetEnvironmentVariable('A01_APP_IMAGE', 'Process')
$dockerExecutable = $null
$composeArgs = $null
$testExit = 1
try {
    $projectRoot = Get-FreshLinkRoot
    Set-Location -LiteralPath $projectRoot
    $dockerExecutable = Assert-FreshLinkDocker
    foreach ($image in @($AppImage, 'postgres:18')) {
        & $dockerExecutable image inspect $image --format '{{.Id}}'
        if ($LASTEXITCODE -ne 0) { throw ('Required local image missing: ' + $image) }
    }
    $runId = 'a01-tests-' + (Get-Date -Format 'yyyyMMddHHmmss') + '-' + $PID
    $resultDir = Join-Path $projectRoot ('work/' + $runId)
    [IO.Directory]::CreateDirectory($resultDir) | Out-Null
    [Environment]::SetEnvironmentVariable('A01_RESULT_DIR', $resultDir, 'Process')
    [Environment]::SetEnvironmentVariable('A01_APP_IMAGE', $AppImage, 'Process')
    $composeArgs = @('compose', '--project-name', $runId, '-f', 'compose.a01-tests.yml')
    & $dockerExecutable @composeArgs config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Isolated test Compose configuration is invalid.' }
    Write-Output 'Starting disposable PostgreSQL; existing app database is not used.'
    & $dockerExecutable @composeArgs up -d --wait --pull never test-db
    if ($LASTEXITCODE -ne 0) { throw 'Disposable test database failed to start.' }
    & $dockerExecutable @composeArgs run --rm --no-deps --pull never test-runner
    $testExit = $LASTEXITCODE
    if ($testExit -ne 0) { throw 'Backend tests or 90% coverage check failed; keep the output for your Agent.' }
    Write-Output 'PASS isolated backend pytest and coverage >=90%.'
    Write-Output 'NOT CHECKED: frontend SDK generation, browser, remote CI or complete cross-module E1.'
    Write-Output ('Evidence directory: ' + $resultDir)
} catch {
    $testExit = 1
    Write-Output ('STOP: ' + $_.Exception.Message)
} finally {
    if ($dockerExecutable -and $composeArgs) {
        # Exact unique test project only; no volumes and no running-app project.
        & $dockerExecutable @composeArgs down --remove-orphans
        if ($LASTEXITCODE -ne 0) {
            $testExit = 1
            Write-Output 'STOP: disposable test cleanup failed; retain the project name above.'
        }
    }
    [Environment]::SetEnvironmentVariable('A01_RESULT_DIR', $priorResultDir, 'Process')
    [Environment]::SetEnvironmentVariable('A01_APP_IMAGE', $priorAppImage, 'Process')
    Set-Location -LiteralPath $originalLocation.Path
}
exit $testExit
