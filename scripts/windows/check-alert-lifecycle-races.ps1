# Opt-in A probe: existing cached images, owned tmpfs PG, no host Python.
[CmdletBinding()]
param([string]$AppImage = 'freshlink-application-candidate:a3716527c7b1-9974fc677e0e')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$priorLocation = Get-Location
$savedResult = [Environment]::GetEnvironmentVariable('A01_RESULT_DIR', 'Process')
$savedImage = [Environment]::GetEnvironmentVariable('A01_APP_IMAGE', 'Process')
$docker = $null
$compose = $null
$resultDir = $null
$probeExit = 2
$cleanupExit = $null
try {
    $root = Get-FreshLinkRoot
    Set-Location -LiteralPath $root
    $docker = Assert-FreshLinkDocker
    foreach ($image in @($AppImage, 'postgres:18')) {
        & $docker image inspect $image --format '{{.Id}}'
        if ($LASTEXITCODE -ne 0) { throw 'Required cached image missing; no pull attempted' }
    }
    $source = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $source -notmatch '^[0-9a-f]{40}$') { throw 'Missing source identity' }
    $beforeStatus = (& git status --porcelain=v1 | Out-String)
    $runId = 'alert-race-' + (Get-Date -Format 'yyyyMMddHHmmss') + '-' + $PID
    $resultDir = Join-Path $root ('work/' + $runId)
    [IO.Directory]::CreateDirectory($resultDir) | Out-Null
    $env:A01_RESULT_DIR = $resultDir
    $env:A01_APP_IMAGE = $AppImage
    $compose = @('compose', '--project-name', $runId, '-f', 'compose.a01-tests.yml')
    & $docker @compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Invalid isolated Compose' }
    & $docker @compose up -d --wait --pull never test-db
    if ($LASTEXITCODE -ne 0) { throw 'Disposable PG did not start' }
    & $docker @compose run --rm --no-deps --pull never test-runner 'python scripts/probe-alert-lifecycle-races.py --report /results/race.json'
    $probeExit = $LASTEXITCODE
    if ((& git rev-parse HEAD).Trim() -ne $source -or (& git status --porcelain=v1 | Out-String) -ne $beforeStatus) {
        throw 'Checkout changed during probe; evidence source is not stable'
    }
    if ($probeExit -eq 0) { Write-Output 'PASS three real PG lifecycle interleavings.' }
    elseif ($probeExit -eq 1 -and (Test-Path -LiteralPath (Join-Path $resultDir 'race.json'))) {
        Write-Output 'FAIL lifecycle safety: real race evidence saved. This is NOT a successful acceptance.'
    } else { throw 'Probe failed without complete evidence; do not call this a race result' }
} catch {
    $probeExit = 2
    Write-Output ('STOP: ' + $_.Exception.Message)
} finally {
    if ($docker -and $compose) {
        & $docker @compose down --remove-orphans
        $cleanupExit = $LASTEXITCODE
        if ($cleanupExit -ne 0) { $probeExit = 2 }
    }
    if ($resultDir) {
        $data = @{
            source = $source; probe_exit = $probeExit; cleanup_exit = $cleanupExit
            project = $runId
            dirty_before = ($beforeStatus.Trim().Length -gt 0)
        } | ConvertTo-Json
        [IO.File]::WriteAllText((Join-Path $resultDir 'host-cleanup.json'), $data, (New-Object Text.UTF8Encoding($false)))
        Write-Output ('Evidence: ' + $resultDir)
    }
    [Environment]::SetEnvironmentVariable('A01_RESULT_DIR', $savedResult, 'Process')
    [Environment]::SetEnvironmentVariable('A01_APP_IMAGE', $savedImage, 'Process')
    Set-Location -LiteralPath $priorLocation.Path
}
exit $probeExit
