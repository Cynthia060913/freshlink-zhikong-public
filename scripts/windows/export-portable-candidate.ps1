# Offline transport only: no load, run, migration, database export or deployment.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$BuildReport,
      [Parameter(Mandatory=$true)][string]$SmokeReport)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'portable-candidate-guards.ps1')
function Write-NewJson([string]$Path,$Value) {
    $stream=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
    try{$bytes=[Text.Encoding]::UTF8.GetBytes(($Value|ConvertTo-Json -Depth 10));$stream.Write($bytes,0,$bytes.Length)}finally{$stream.Dispose()}
}
$root=Get-FreshLinkRoot
$buildPath=Assert-FreshLinkOwnedPortableInput $BuildReport (Join-Path $root 'work/source-candidates') 'application-build.json'
$smokePath=Assert-FreshLinkOwnedPortableInput $SmokeReport (Join-Path $root 'work') 'application-http.json'
$cleanupPath=Assert-FreshLinkOwnedPortableInput (Join-Path (Split-Path -Parent $smokePath) 'image-cleanup.json') (Join-Path $root 'work') 'image-cleanup.json'
$build=Get-Content -LiteralPath $buildPath -Raw|ConvertFrom-Json
$smoke=Get-Content -LiteralPath $smokePath -Raw|ConvertFrom-Json
$cleanup=Get-Content -LiteralPath $cleanupPath -Raw|ConvertFrom-Json
Assert-FreshLinkPortableEvidence $build $smoke $cleanup
$sourceFiles=@(Get-ChildItem -LiteralPath (Split-Path -Parent $buildPath) -Filter 'FreshLink-source-*-candidate.zip' -File)
if($sourceFiles.Count -ne 1){throw 'Exactly one source archive required'}
$sourcePath=Assert-FreshLinkOwnedPortableInput $sourceFiles[0].FullName (Join-Path $root 'work/source-candidates') $sourceFiles[0].Name
if((Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $build.source_zip_sha256){throw 'Source archive hash changed'}
$docker=Assert-FreshLinkDocker
$refs=@($build.image,'postgres:18','eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408')
$images=@();$size=0L
foreach($ref in $refs){
    $raw=& $docker image inspect $ref
    if($LASTEXITCODE){throw 'Required cached image missing; no automatic pull'}
    $image=(($raw -join "`n")|ConvertFrom-Json)[0]
    if($image.Id -notmatch '^sha256:[0-9a-f]{64}$' -or $image.Os -ne 'linux' -or $image.Architecture -ne 'amd64'){throw 'Unsupported image identity or platform'}
    if($ref -eq $build.image -and ($image.Id -ne $build.image_id -or $image.Config.Labels.'org.opencontainers.image.revision' -ne $build.source)){throw 'Application image changed'}
    $images+=@{role=(@('application','postgres','mqtt')[$images.Count]);id=$image.Id;reference=$ref;platform='linux/amd64'}
    $size+=[long]$image.Size
}
$disk=Get-PSDrive -Name ([IO.Path]::GetPathRoot($root).Substring(0,1))
if($disk.Free -lt (3*$size+1GB)){throw 'Insufficient conservative export space; nothing exported'}
$out=Join-Path $root ('work/portable-candidates/'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+[guid]::NewGuid().ToString('N'))
if(Test-Path -LiteralPath $out){throw 'Refusing existing output directory'}
[IO.Directory]::CreateDirectory($out)|Out-Null
$status='failed_incomplete_do_not_import'
try {
    Copy-Item -LiteralPath $sourcePath -Destination (Join-Path $out 'source.zip') -ErrorAction Stop
    $tar=Join-Path $out 'images.tar'
    # IDs only: loading this archive does not deliberately replace shared tags.
    $ids=@($images|ForEach-Object { $_.id })
    & $docker image save --output $tar @ids
    if($LASTEXITCODE){throw 'Image save failed; retained incomplete candidate'}
    if((Get-Item -LiteralPath $tar).Length -le 0){throw 'Empty image archive'}
    $files=@()
    foreach($name in @('source.zip','images.tar')){
        $path=Join-Path $out $name
        $files+=@{name=$name;bytes=(Get-Item -LiteralPath $path).Length;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}
    }
    if(($files|Where-Object {$_.name -eq 'source.zip'}).sha256 -ne $build.source_zip_sha256){throw 'Copied source archive hash differs'}
    Write-NewJson (Join-Path $out 'manifest.json') @{
        status='offline_transport_exported_not_second_machine_or_E1';source=$build.source;images=$images;files=$files;
        not_verified=@('archive reload','second computer','full application workers','formal C migration','E1','notifications','final submission');
        privacy='No containers, database volumes, runtime configuration or private logs exported; image history/binaries are not a complete security audit'
    }
    $status='offline_transport_exported_not_second_machine_or_E1'
    Write-Output ('PASS offline image/source transport candidate: '+$out)
    Write-Output 'NOT ACCEPTED: reload, second-machine operation, full E1, real notifications or final submission.'
} finally {
    Write-NewJson (Join-Path $out 'export-status.json') @{status=$status;source=$build.source;no_containers_started=$true;no_original_database_used=$true}
}
