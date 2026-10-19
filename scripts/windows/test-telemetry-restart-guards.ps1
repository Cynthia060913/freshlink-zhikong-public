# Pure negative tests: no Docker, Python, package install or environment change.
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'telemetry-restart-guards.ps1')
$script:passed=0
$runId='telemetry-restart-20261018000000-12345'

function New-FixtureModel {
    return @{
        name=$runId
        networks=@{default=@{internal=$true}}
        services=@{
            'test-db'=@{volumes=@(@{type='volume';source='restart_pg_data';target='/var/lib/postgresql'})}
            broker=@{volumes=@(@{type='volume';source='restart_mqtt_data';target='/tmp/runtime-store'})}
            'test-runner'=@{}
        }
        volumes=@{
            restart_pg_data=@{name=($runId+'_restart_pg_data');labels=@{'org.freshlink.proof'='telemetry-restart'}}
            restart_mqtt_data=@{name=($runId+'_restart_mqtt_data');labels=@{'org.freshlink.proof'='telemetry-restart'}}
        }
    }|ConvertTo-Json -Depth 10|ConvertFrom-Json
}

function Expect-Refusal([scriptblock]$Case){
    $refused=$false
    try {& $Case} catch {$refused=$true}
    if(-not $refused){throw 'Safety guard accepted an unsafe test fixture'}
    $script:passed++
}

Assert-FreshLinkRestartScope $runId (New-FixtureModel)
$script:passed++
Expect-Refusal {Assert-FreshLinkRestartScope 'freshlink' (New-FixtureModel)}
Expect-Refusal {$m=New-FixtureModel;$m.name='freshlink';Assert-FreshLinkRestartScope $runId $m}
Expect-Refusal {$m=New-FixtureModel;$m.volumes.restart_pg_data.name='freshlink_db-data';Assert-FreshLinkRestartScope $runId $m}
Expect-Refusal {$m=New-FixtureModel;$m.networks.default.internal=$false;Assert-FreshLinkRestartScope $runId $m}
Expect-Refusal {$m=New-FixtureModel;$m.services.broker|Add-Member ports @(1883);Assert-FreshLinkRestartScope $runId $m}
Expect-Refusal {$m=New-FixtureModel;$m.volumes.restart_pg_data|Add-Member external $true;Assert-FreshLinkRestartScope $runId $m}
Expect-Refusal {$m=New-FixtureModel;$m.volumes.restart_pg_data.labels.'org.freshlink.proof'='other';Assert-FreshLinkRestartScope $runId $m}
Expect-Refusal {$m=New-FixtureModel;$m.services.'test-db'.volumes[0].source='other';Assert-FreshLinkRestartScope $runId $m}
Expect-Refusal {$m=New-FixtureModel;$m.services.broker.volumes[0].type='bind';Assert-FreshLinkRestartScope $runId $m}
Expect-Refusal {$m=New-FixtureModel;$m.services|Add-Member app @{};Assert-FreshLinkRestartScope $runId $m}
$volume=@{Name=($runId+'_restart_pg_data');Labels=@{'com.docker.compose.project'=$runId;'org.freshlink.proof'='telemetry-restart'}}|ConvertTo-Json|ConvertFrom-Json
Assert-FreshLinkRestartVolume $volume $runId ($runId+'_restart_pg_data')
$script:passed++
Expect-Refusal {Assert-FreshLinkRestartVolume $volume $runId 'freshlink_db-data'}
$volume.Labels.'com.docker.compose.project'='freshlink'
Expect-Refusal {Assert-FreshLinkRestartVolume $volume $runId ($runId+'_restart_pg_data')}
if($script:passed -ne 14){throw 'Unexpected guard test count'}
Write-Output 'PASS 14 restart-proof ownership/model guards (pure fixtures; no resources touched).'
exit 0
