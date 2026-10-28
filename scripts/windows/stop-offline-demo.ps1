# Stop only this generated demo; never remove its database volume or user data.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$State)
$ErrorActionPreference='Stop'
$file=Get-Item -LiteralPath $State -Force
if($file.PSIsContainer -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $file.Name -cne 'demo-state.json'){throw 'Expected a regular generated demo-state.json'}
$record=Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8|ConvertFrom-Json
if($record.format -cne 'freshlink-interactive-demo-v1' -or $record.project -cnotmatch '^freshlink-demo-[0-9a-f]{32}$' -or $record.database_volume -cne ($record.project+'-db') -or $file.Directory.Name -cne $record.project){throw 'Not a scoped generated demo state'}
$command=Get-Command docker -CommandType Application -ErrorAction SilentlyContinue|Select-Object -First 1
$docker=if($command){$command.Source}else{$null}
if(-not $docker){
    $locations=@()
    if($env:ProgramFiles){$locations+=Join-Path $env:ProgramFiles 'Docker/Docker/resources/bin/docker.exe'}
    if($env:LOCALAPPDATA){
        $locations+=Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/resources/bin/docker.exe'
        $locations+=Join-Path $env:LOCALAPPDATA 'Docker/resources/bin/docker.exe'
    }
    $docker=$locations|Where-Object {Test-Path -LiteralPath $_ -PathType Leaf}|Select-Object -First 1
}
if(-not $docker){throw 'Local Docker Desktop CLI unavailable; no system change attempted'}
if($env:DOCKER_HOST -or $env:DOCKER_CONTEXT){throw 'Explicit remote Docker overrides are not allowed'}
$endpoint=(& $docker context inspect --format '{{.Endpoints.docker.Host}}'|Out-String).Trim()
if($LASTEXITCODE -or $endpoint -notmatch '^npipe://'){throw 'Only local Docker Desktop is supported'}
$ids=@(& $docker ps -aq --filter ('label=com.docker.compose.project='+$record.project))
if($LASTEXITCODE){throw 'Cannot inspect demo resources'}
# Validate every target BEFORE stopping the first one; no caller-supplied Compose.
foreach($id in $ids){
    $owner=(& $docker inspect $id --format '{{index .Config.Labels "freshlink.delivery.owner"}}'|Out-String).Trim()
    if($LASTEXITCODE -or $owner -cne $record.project){throw 'Foreign container in scope; nothing stopped'}
}
foreach($id in $ids){
    & $docker stop --time 20 $id|Out-Null
    if($LASTEXITCODE){throw 'Demo stop incomplete; do not broaden cleanup'}
    & $docker rm $id|Out-Null
    if($LASTEXITCODE){throw 'Stopped demo container could not be removed'}
}
$networks=@(& $docker network ls -q --filter ('label=com.docker.compose.project='+$record.project))
if($LASTEXITCODE){throw 'Cannot inspect demo networks'}
foreach($id in $networks){
    $name=(& $docker network inspect $id --format '{{.Name}}'|Out-String).Trim()
    if($LASTEXITCODE -or -not $name.StartsWith($record.project+'_',[StringComparison]::Ordinal)){throw 'Foreign network; not removed'}
    & $docker network rm $id|Out-Null
    if($LASTEXITCODE){throw 'Demo network still in use; not forcing removal'}
}
$record.status='stopped_database_volume_preserved'
$record|Add-Member -NotePropertyName stopped_at_utc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
[IO.File]::WriteAllText($file.FullName,($record|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
Write-Output ('Stopped owned demo. Database volume preserved: '+$record.database_volume)
