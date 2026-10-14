# Uses the existing local app image. No downloads, database, credentials or push.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')
try {
    $projectRoot = Get-FreshLinkRoot
    $dockerExecutable = Assert-FreshLinkDocker
    & $dockerExecutable image inspect freshlink-app:p0 --format '{{.Id}}'
    if ($LASTEXITCODE -ne 0) { throw 'Existing freshlink-app:p0 image is required.' }
    $mount = 'type=bind,source=' + (Join-Path $projectRoot 'backend') + ',target=/workspace/backend'
    $baseArgs = @('run', '--rm', '--pull', 'never', '--network', 'none',
        '--mount', $mount, '--workdir', '/workspace/backend',
        '--entrypoint', 'python', 'freshlink-app:p0')
    $targets = @('app/modules/iot', 'tests/api/routes/test_devices.py', 'tests/api/routes/test_login.py',
        'scripts/check-device-empty-migration.py', 'scripts/check-device-schema.py')
    & $dockerExecutable @baseArgs -m ruff format @targets
    if ($LASTEXITCODE -ne 0) { throw 'Ruff formatter failed; no publication attempted.' }
    & $dockerExecutable @baseArgs -m ruff check @targets
    if ($LASTEXITCODE -ne 0) { throw 'Ruff checks failed; send the output to your Agent.' }
    & $dockerExecutable @baseArgs -m mypy app
    if ($LASTEXITCODE -ne 0) { throw 'Mypy failed; send the output to your Agent.' }
    & $dockerExecutable @baseArgs -m ty check app
    if ($LASTEXITCODE -ne 0) { throw 'Ty failed; send the output to your Agent.' }
    Write-Output 'PASS A01 format/lint and backend type checks.'
    Write-Output 'NOT TESTED: PostgreSQL pytest/coverage, frontend SDK, browser or remote CI.'
} catch {
    Write-Output ('STOP: ' + $_.Exception.Message)
    exit 1
}
