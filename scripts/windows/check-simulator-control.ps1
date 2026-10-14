# Isolated file-bridge MQTT control test, NOT a continuous production listener.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Image,
    [string]$DockerExecutable
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')
if (-not $DockerExecutable) { $DockerExecutable = Assert-FreshLinkDocker }
if ($Image -notmatch '^eclipse-mosquitto@sha256:[a-f0-9]{64}$') { throw 'Use the verified official digest.' }
$projectRoot = Get-FreshLinkRoot
$fixtures = Join-Path $projectRoot 'scripts/mqtt-control'
$backend = Join-Path $projectRoot 'backend'
$runId = 'a-simulator-control-' + [guid]::NewGuid().ToString('N')
$evidence = Join-Path $projectRoot ('work/' + $runId)
[IO.Directory]::CreateDirectory($evidence) | Out-Null
$created = $false
function Invoke-SimulatorStep {
    param([string]$Mode)
    & $DockerExecutable run --rm --network none --mount "type=bind,source=$backend,target=/workspace/backend,readonly" --mount "type=bind,source=$evidence,target=/evidence" --env PYTHONPATH=/workspace/backend --env PYTHONDONTWRITEBYTECODE=1 --workdir /workspace/backend freshlink-app:p0 python scripts/check-simulator-control.py $Mode /evidence
    if ($LASTEXITCODE -ne 0) { throw ('Simulator step failed: ' + $Mode) }
}
function Invoke-Transport {
    param([string]$Mode)
    & $DockerExecutable exec $runId sh /fixtures/roundtrip.sh $Mode
    if ($LASTEXITCODE -ne 0) { throw ('MQTT transport failed: ' + $Mode) }
}
function Assert-CommandDenied {
    param([string]$User, [string]$Password, [string]$Topic)
    $ErrorActionPreference = 'Continue'
    $response = & $DockerExecutable exec $runId mosquitto_pub -h 127.0.0.1 -V mqttv5 -q 1 -u $User -P $Password -t $Topic -f /evidence/command.json -d 2>&1
    # This client may exit zero for a denied publish; inspect broker reason.
    if (($response -join "`n") -notmatch 'received PUBACK \(Mid: [0-9]+, RC:135\)') { throw 'Expected actual MQTT5 Not authorized (135) response.' }
}
try {
    foreach ($required in @($Image, 'freshlink-app:p0')) {
        & $DockerExecutable image inspect $required --format '{{.Id}}'
        if ($LASTEXITCODE -ne 0) { throw 'Required verified image is not local.' }
    }
    & $DockerExecutable run -d --name $runId --label 'freshlink.purpose=simulator-control-test' --network none --mount "type=bind,source=$fixtures,target=/fixtures,readonly" --mount "type=bind,source=$evidence,target=/evidence" --entrypoint sh $Image /fixtures/start.sh
    if ($LASTEXITCODE -ne 0) { throw 'Disposable broker could not start.' }
    $created = $true
    Invoke-SimulatorStep 'prepare'
    Assert-CommandDenied 'test-actuator' 'fixture-actuator-only' 'freshlink/v1/devices/66666666-6666-4666-8666-666666666666/commands'
    Assert-CommandDenied 'test-controller' 'fixture-controller-only' 'freshlink/v1/devices/44444444-4444-4444-8444-444444444444/commands'
    Write-Output 'PASS actuator command injection and controller cross-device publication denied with PUBACK 135'
    Invoke-Transport 'command'
    Invoke-SimulatorStep 'execute'
    Invoke-Transport 'ack'
    Invoke-Transport 'telemetry'
    Invoke-Transport 'command'
    # A new Python container reads the SAME persisted state and command bytes.
    Invoke-SimulatorStep 'duplicate'
    Invoke-SimulatorStep 'verify'
    Write-Output 'PASS MQTT command -> simulator execution -> actual ACK and later telemetry transport'
    Write-Output 'PASS restart and identical redelivery execute only once; original ACK preserved'
    Write-Output 'PASS temperature/power effects; broker receipt does not clear pending C delivery'
    Write-Output ('Evidence directory: ' + $evidence)
} finally {
    if ($created) {
        $inspection = & $DockerExecutable inspect $runId | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or $inspection[0].Config.Labels.'freshlink.purpose' -ne 'simulator-control-test') { throw 'Cleanup identity check failed; retain container for inspection.' }
        & $DockerExecutable rm -f $runId
        if ($LASTEXITCODE -ne 0) { throw 'Disposable broker cleanup failed.' }
        Write-Output 'PASS exact disposable broker removed; app database and host ports untouched'
    }
}
Write-Output 'NOT CHECKED: continuous listener, production credentials/TLS, public control API, C storage, B alerts, E1.'
