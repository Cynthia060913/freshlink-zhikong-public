# Real broker bytes -> disposable PostgreSQL registered-device adapter.
[CmdletBinding()]
param([string]$DockerExecutable)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')
if (-not $DockerExecutable) { $DockerExecutable = Assert-FreshLinkDocker }
$priorResultDir = [Environment]::GetEnvironmentVariable('A01_RESULT_DIR', 'Process')
$composeArgs = $null
$originalLocation = Get-Location
try {
    $projectRoot = Get-FreshLinkRoot
    Set-Location -LiteralPath $projectRoot
    $image = 'eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408'
    $mqttOutput = @(& (Join-Path $PSScriptRoot 'check-mqtt-boundary.ps1') -Image $image -DockerExecutable $DockerExecutable -SaveReceipt)
    $mqttOutput | Write-Output
    $receipts = @($mqttOutput | Where-Object { $_ -is [string] -and $_.StartsWith('Receipt file: ') })
    if ($receipts.Count -ne 1) { throw 'Missing unique verified broker receipt.' }
    $receipt = $receipts[0].Substring('Receipt file: '.Length)
    $resultDir = Split-Path -Parent $receipt
    [Environment]::SetEnvironmentVariable('A01_RESULT_DIR', $resultDir, 'Process')
    $runId = 'a12-mqtt-registry-' + [guid]::NewGuid().ToString('N')
    $composeArgs = @('compose', '--project-name', $runId, '-f', 'compose.a01-tests.yml')
    & $DockerExecutable @composeArgs config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Disposable test configuration is invalid.' }
    & $DockerExecutable @composeArgs up -d --wait --pull never test-db
    if ($LASTEXITCODE -ne 0) { throw 'Disposable PostgreSQL did not start.' }
    & $DockerExecutable @composeArgs run --rm --no-deps --pull never test-runner 'alembic upgrade head && python app/initial_data.py && python scripts/check-mqtt-registry.py'
    if ($LASTEXITCODE -ne 0) { throw 'Actual broker receipt -> registered-device check failed.' }
    Write-Output 'PASS actual MQTT receipt -> PostgreSQL registration/binding -> validated raw envelope'
    Write-Output ('Evidence receipt: ' + $receipt)
    Write-Output 'WAREHOUSE CHECK: explicit substitute; NOT CHECKED: B service, C persistence, production MQTT adapter, heartbeat/control, or E1.'
} finally {
    try {
        if ($composeArgs) {
            & $DockerExecutable @composeArgs down --remove-orphans
            if ($LASTEXITCODE -ne 0) { throw 'Disposable test project cleanup failed.' }
        }
    } finally {
        [Environment]::SetEnvironmentVariable('A01_RESULT_DIR', $priorResultDir, 'Process')
        Set-Location -LiteralPath $originalLocation.Path
    }
}
