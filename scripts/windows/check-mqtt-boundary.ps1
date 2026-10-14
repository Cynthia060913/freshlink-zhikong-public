# Isolated broker transport/ACL check; not C persistence, heartbeat or E1.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Image,
    [string]$DockerExecutable,
    [switch]$SaveReceipt
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')
if (-not $DockerExecutable) { $DockerExecutable = Assert-FreshLinkDocker }
if ($Image -notmatch '^eclipse-mosquitto@sha256:[a-f0-9]{64}$') {
    throw 'Pass the verified official image digest, not a mutable tag.'
}
$projectRoot = Get-FreshLinkRoot
$fixtures = Join-Path $projectRoot 'scripts/mqtt-smoke'
$containerName = 'freshlink-mqtt-smoke-' + [guid]::NewGuid().ToString('N')
$created = $false
$passed = $false
function Invoke-MqttProbe {
    param([string[]]$Arguments)
    # Windows PowerShell turns native stderr into ErrorRecord; expected MQTT
    # denials must be checked by exit code rather than abort before cleanup.
    $ErrorActionPreference = 'Continue'
    $probeOutput = & $DockerExecutable @Arguments 2>&1
    $probeCode = $LASTEXITCODE
    return @{ Code = $probeCode; Output = $probeOutput }
}
try {
    & $DockerExecutable image inspect $Image --format '{{.Id}}'
    if ($LASTEXITCODE -ne 0) { throw 'Required digest not local; pull the official image first.' }
    & $DockerExecutable run -d --name $containerName --label 'freshlink.purpose=mqtt-boundary-test' --network none --mount "type=bind,source=$fixtures,target=/fixtures,readonly" --entrypoint sh $Image /fixtures/start.sh
    if ($LASTEXITCODE -ne 0) { throw 'Disposable broker did not start.' }
    $created = $true
    $topic = 'freshlink/v1/devices/11111111-1111-4111-8111-111111111111/telemetry'
    $publisher = @('exec', $containerName, 'mosquitto_pub', '-h', '127.0.0.1', '-V', 'mqttv5', '-q', '1', '-u', 'smoke-device', '-P', 'disposable-device-only')
    # Retain is used ONLY to make the smoke-test subscriber ordering deterministic.
    # Production telemetry retain/session/outbox policy is not decided by this test.
    $ready = $false
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        $probe = Invoke-MqttProbe -Arguments ($publisher + @('-t', $topic, '-f', '/fixtures/telemetry.json', '-r'))
        if ($probe.Code -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'Authenticated fixture publish failed.' }
    # Preserve payload bytes, including trailing newlines; PowerShell's text
    # pipeline otherwise drops line endings before comparison.
    $subscriber = 'mosquitto_sub -h 127.0.0.1 -V mqttv5 -u smoke-observer -P disposable-observer-only -t ' + $topic + ' -C 1 -W 5 -N > /tmp/smoke-receipt'
    & $DockerExecutable exec $containerName sh -c $subscriber
    if ($LASTEXITCODE -ne 0) { throw 'Authenticated observer did not receive fixture.' }
    & $DockerExecutable exec $containerName cmp /fixtures/telemetry.json /tmp/smoke-receipt
    if ($LASTEXITCODE -ne 0) { throw 'Broker receipt bytes differ from the actual fixture.' }
    Write-Output 'PASS actual authenticated MQTT publish and byte-exact subscriber receipt'

    foreach ($forbidden in @($topic.Replace('11111111-1111-4111-8111-111111111111', '44444444-4444-4444-8444-444444444444'), $topic.Replace('/telemetry', '/commands'))) {
        $probe = Invoke-MqttProbe -Arguments ($publisher + @('-t', $forbidden, '-m', 'forbidden', '-d'))
        # This pinned client can exit 0 despite a rejected PUBLISH. Assert the
        # actual broker PUBACK reason, not merely process completion.
        if (($probe.Output -join "`n") -notmatch 'received PUBACK \(Mid: [0-9]+, RC:135\)') {
            throw 'Broker PUBACK Not authorized (135) was not observed.'
        }
    }
    Write-Output 'PASS cross-device telemetry and device command publication rejected with MQTT5 Not authorized'
    $probe = Invoke-MqttProbe -Arguments @('exec', $containerName, 'mosquitto_pub', '-h', '127.0.0.1', '-V', 'mqttv5', '-q', '1', '-t', $topic, '-m', 'anonymous')
    if ($probe.Code -ne 135) { throw ('Anonymous authentication rejection not verified: ' + $probe.Code) }
    Write-Output 'PASS anonymous client rejected'
    $probe = Invoke-MqttProbe -Arguments @('exec', $containerName, 'mosquitto_pub', '-h', '127.0.0.1', '-V', 'mqttv5', '-q', '1', '-u', 'smoke-device', '-P', 'deliberately-wrong', '-t', $topic, '-m', 'wrong')
    if ($probe.Code -notin @(134, 135)) { throw ('Wrong-password rejection not verified: ' + $probe.Code) }
    Write-Output 'PASS wrong password rejected'
    if ($SaveReceipt) {
        $receiptDir = Join-Path $projectRoot ('work/a-mqtt-receipt-' + [guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($receiptDir) | Out-Null
        $receiptFile = Join-Path $receiptDir 'mqtt-receipt.json'
        & $DockerExecutable cp ($containerName + ':/tmp/smoke-receipt') $receiptFile
        if ($LASTEXITCODE -ne 0) { throw 'Actual MQTT receipt export failed.' }
        Write-Output ('Receipt file: ' + $receiptFile)
    }
    $passed = $true
} finally {
    if ($created) {
        # Unique container created by this invocation only; no host path deletion.
        $inspection = & $DockerExecutable inspect $containerName | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or $inspection[0].Config.Labels.'freshlink.purpose' -ne 'mqtt-boundary-test') { throw 'Cleanup identity check failed; container retained for inspection.' }
        & $DockerExecutable rm -f $containerName
        if ($LASTEXITCODE -ne 0) { throw 'Disposable broker cleanup failed.' }
        Write-Output 'PASS disposable broker removed; no host ports or app database used'
    }
}
if (-not $passed) { throw 'MQTT boundary checks did not complete.' }
Write-Output ('Image: ' + $Image)
Write-Output 'NOT CHECKED: production credentials/TLS, registration adapter, C persistence, heartbeat, control execution or E1.'
