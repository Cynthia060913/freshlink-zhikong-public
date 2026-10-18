# Pure checks only: importing this file never touches Docker or the filesystem.
function Assert-FreshLinkRestartScope([string]$RunId,[object]$Model) {
    if($RunId -notmatch '^telemetry-restart-\d{14}-\d+$'){throw 'Invalid owned project name'}
    $expected=@(($RunId+'_restart_pg_data'),($RunId+'_restart_mqtt_data'))
    $actual=@($Model.volumes.PSObject.Properties|ForEach-Object {$_.Value.name})
    if($Model.name -ne $RunId -or $actual.Count -ne 2 -or
       @(Compare-Object $expected $actual).Count -ne 0){throw 'Unexpected volume scope'}
    if(-not $Model.networks.default.internal){throw 'Proof must use internal network'}
    $services=@($Model.services.PSObject.Properties.Name)
    if($services.Count -ne 3 -or
       @($services|Where-Object {$_ -notin @('test-db','broker','test-runner')}).Count -gt 0){throw 'Unexpected service scope'}
    foreach($service in $Model.services.PSObject.Properties){
        if($service.Value.PSObject.Properties['ports'] -and $service.Value.ports.Count -gt 0){throw 'Host ports forbidden'}
    }
    foreach($definition in $Model.volumes.PSObject.Properties){
        if($definition.Value.PSObject.Properties['external'] -and $definition.Value.external){throw 'External volume forbidden'}
        if($definition.Value.labels.'org.freshlink.proof' -ne 'telemetry-restart'){throw 'Missing proof volume label'}
    }
    foreach($pair in @(@('test-db','restart_pg_data','/var/lib/postgresql'),@('broker','restart_mqtt_data','/tmp/runtime-store'))){
        $mount=@($Model.services.($pair[0]).volumes|Where-Object {$_.target -eq $pair[2]})
        if($mount.Count -ne 1 -or $mount[0].type -ne 'volume' -or $mount[0].source -ne $pair[1]){throw 'Unexpected service data mount'}
    }
}

function Assert-FreshLinkRestartVolume([object]$Item,[string]$RunId,[string]$ExpectedName){
    if($RunId -notmatch '^telemetry-restart-\d{14}-\d+$' -or
       $ExpectedName -notin @(($RunId+'_restart_pg_data'),($RunId+'_restart_mqtt_data')) -or
       $Item.Name -ne $ExpectedName -or
       $Item.Labels.'com.docker.compose.project' -ne $RunId -or
       $Item.Labels.'org.freshlink.proof' -ne 'telemetry-restart'){
        throw 'Refusing operation on unowned volume'
    }
}
