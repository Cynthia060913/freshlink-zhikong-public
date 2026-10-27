# Existing cached Mosquitto only. Random internal network, no host ports or DB.
[CmdletBinding()]
param()
$ErrorActionPreference="Stop"
$root=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "../..")).Path
$source=(& git -C $root rev-parse HEAD).Trim()
if($LASTEXITCODE -or $source -notmatch '^[0-9a-f]{40}$'){throw "Missing source"}
$image="eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408"
$null=& docker image inspect $image --format '{{.Id}}'
if($LASTEXITCODE){throw "Required cached image missing; do not pull automatically"}
$name="mqtt-restart-"+[guid]::NewGuid().ToString("N")
$network=$name+"-network"
$out=Join-Path $root ("work/"+$name)
New-Item -ItemType Directory -Path $out | Out-Null
$created=$false;$networkCreated=$false;$passed=$false;$rounds=0;$cleanup=$true
try{
    $null=& docker network create --internal --label ("freshlink.owner="+$name) $network
    if($LASTEXITCODE){throw "Internal network creation failed"};$networkCreated=$true
    $fixture=Join-Path $root "scripts/mqtt-runtime"
    $null=& docker run -d --name $name --label ("freshlink.owner="+$name) --pull never --network $network --mount "type=bind,source=$fixture,target=/fixtures,readonly" --entrypoint sh $image /fixtures/start.sh
    if($LASTEXITCODE){throw "Owned broker creation failed"};$created=$true
    function Wait-OwnedBroker{
        $until=(Get-Date).AddSeconds(15)
        do{
            $null=& docker exec $name sh -c "pidof mosquitto >/dev/null" 2>$null
            if($LASTEXITCODE -eq 0){Start-Sleep -Milliseconds 300;return}
            Start-Sleep -Milliseconds 250
        }while((Get-Date)-lt $until)
        throw "Owned broker readiness failed"
    }
    Wait-OwnedBroker
    $topic="freshlink/v1/devices/11111111-1111-4111-8111-111111111111/telemetry"
    $sentinel="SYNTHETIC_FIXTURE_RESTART_ONLY"
    $null=& docker exec $name mosquitto_pub -h 127.0.0.1 -p 1883 -u test-sensor -P fixture-sensor-only -t $topic -q 1 -r -m $sentinel
    if($LASTEXITCODE){throw "Fixture publish failed"}
    foreach($round in 1..2){
        $null=& docker restart --timeout 5 $name
        if($LASTEXITCODE){throw "Owned broker restart failed"}
        Wait-OwnedBroker
        $actual=& docker exec $name mosquitto_sub -h 127.0.0.1 -p 1883 -u test-controller -P fixture-controller-only -t $topic -q 1 -C 1 -W 5
        if($LASTEXITCODE -or ($actual|Out-String).Trim() -ne $sentinel){throw "Authenticated retained data lost or broker startup failed"}
        $rounds=$round
    }
    $passed=$true
}finally{
    if($created){
        $owner=(& docker inspect --format '{{index .Config.Labels "freshlink.owner"}}' $name).Trim()
        if($owner -ne $name){throw "Refusing cleanup of a different owner"}
        $null=& docker rm -f $name
        if($LASTEXITCODE){$cleanup=$false}
    }
    if($networkCreated){$null=& docker network rm $network;if($LASTEXITCODE){$cleanup=$false}}
    $left=@(& docker ps -aq --filter ("label=freshlink.owner="+$name))
    $net=@(& docker network ls -q --filter ("label=freshlink.owner="+$name))
    if($left.Count -or $net.Count){$cleanup=$false}
    @{source=$source;passed=$passed;authenticated_retained_restarts=$rounds;broker_state_preserved=$passed;cleanup_verified=$cleanup;notifications_sent=0;complete_e1=$false}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $out "mqtt-restart.json") -Encoding utf8
    Write-Output ("Evidence: "+$out)
}
if(-not $passed -or -not $cleanup){throw "Fixture restart proof or cleanup failed"}
Write-Output "PASS two broker restarts, authenticated retained message preserved, owned resources removed."
