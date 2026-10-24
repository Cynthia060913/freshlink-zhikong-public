[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$BuildReport)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'offline-kit-guards.ps1')
$repo=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$toolSource=(& git -C $repo rev-parse HEAD|Out-String).Trim()
if($LASTEXITCODE -or $toolSource -notmatch '^[0-9a-f]{40}$' -or (& git -C $repo status --porcelain|Out-String).Trim()){throw 'Clean committed helper source required'}
$reportPath=(Resolve-Path -LiteralPath $BuildReport).Path
$build=Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8|ConvertFrom-Json
if($build.status -ne 'candidate_image_built_source_verified_not_application_acceptance' -or $build.source -notmatch '^[0-9a-f]{40}$' -or $build.image_id -notmatch '^sha256:[0-9a-f]{64}$'){throw 'Verified build report required'}
$inputRoot=Split-Path $reportPath -Parent
$source=Get-Content -LiteralPath (Join-Path $inputRoot 'manifest.json') -Raw -Encoding UTF8|ConvertFrom-Json
if($source.revision -cne $build.source -or $source.zip_sha256 -cne $build.source_zip_sha256 -or $source.zip_name -notmatch '^FreshLink-source-[0-9a-f]{12}-candidate\.zip$'){throw 'Build/source manifest mismatch'}
$zip=Join-Path $inputRoot $source.zip_name
if((Get-OfflineDigest $zip) -cne $source.zip_sha256){throw 'Source archive hash mismatch'}
$docker=Get-OfflineLocalDocker
$images=@()
foreach($pair in @(@('application',$build.image_id),@('postgres','postgres:18'),@('mqtt','eclipse-mosquitto@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408'))){
    $data=& $docker image inspect $pair[1]
    if($LASTEXITCODE){throw 'Required image not cached; no automatic pull'}
    $image=($data -join "`n"|ConvertFrom-Json)[0]
    if(($image.Os+'/'+$image.Architecture) -cne 'linux/amd64'){throw 'Only the verified Linux amd64 platform is supported'}
    if($pair[0] -eq 'application' -and ($image.Id -cne $build.image_id -or $image.Config.Labels.'org.opencontainers.image.revision' -cne $build.source)){throw 'Application source identity mismatch'}
    $images+=@{role=$pair[0];id=$image.Id;platform='linux/amd64'}
}
# Revalidate actual application files against the source manifest, not only a
# caller-supplied build report or mutable local image tag.
$audit=& $docker run --rm --pull never --network none --mount ('type=bind,source='+(Join-Path $inputRoot 'manifest.json')+',target=/proof/source-manifest.json,readonly') --env ('FRESHLINK_EXPECTED_SOURCE='+$build.source) --entrypoint python $build.image_id -B scripts/check-image-source.py
if($LASTEXITCODE){throw 'Actual image/source audit failed'}
$auditResult=($audit -join "`n"|ConvertFrom-Json)
if($auditResult.status -ne 'verified_image_source_and_frontend_presence_not_E1' -or $auditResult.source -cne $build.source){throw 'Wrong image audit result'}
$out=Join-Path $repo ('work/offline-kits/'+[guid]::NewGuid().ToString('N'))
if(Test-Path -LiteralPath $out){throw 'Never overwrite an existing package'}
[IO.Directory]::CreateDirectory($out)|Out-Null
Copy-Item -LiteralPath $zip -Destination (Join-Path $out 'source.zip')
Copy-Item -LiteralPath (Join-Path $inputRoot 'manifest.json') -Destination (Join-Path $out 'source-manifest.json')
foreach($name in @('run-offline-fleet.ps1','offline-kit-guards.ps1')){Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $out $name)}
Copy-Item -LiteralPath (Join-Path $repo 'docs/submission/OFFLINE-FLEET-KIT.md') -Destination (Join-Path $out 'README.md')
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive=[IO.Compression.ZipFile]::OpenRead($zip)
try {
    foreach($name in @('start.sh','mosquitto.conf','acl')){
        $entry=$archive.GetEntry(('FreshLink/scripts/mqtt-fleet/'+$name))
        if(-not $entry -or $entry.Length -gt 64KB){throw 'Pinned MQTT fixture missing or oversized'}
        $inputStream=$entry.Open();$outputStream=[IO.File]::Open((Join-Path $out $name),[IO.FileMode]::CreateNew)
        try {$inputStream.CopyTo($outputStream)}finally{$inputStream.Dispose();$outputStream.Dispose()}
    }
} finally {$archive.Dispose()}
# IDs, not tags: the receiving Docker load will not overwrite unrelated tags.
& $docker image save --output (Join-Path $out 'images.tar') @($images|ForEach-Object {$_.id})
if($LASTEXITCODE){throw 'Image archive export failed; incomplete private directory retained'}
$files=@(Get-ChildItem -LiteralPath $out -File|Sort-Object Name|ForEach-Object {@{name=$_.Name;bytes=$_.Length;sha256=(Get-OfflineDigest $_.FullName)}})
$manifest=@{format='freshlink-offline-fleet-kit-v1';status='transport_candidate_not_E1_or_second_machine_acceptance';source=$build.source;tool_source=$toolSource;images=$images;files=$files}
$manifestPath=Join-Path $out 'offline-manifest.json'
[IO.File]::WriteAllText($manifestPath,($manifest|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
$digest=Get-OfflineDigest $manifestPath
[void](Assert-OfflineKit $out $digest)
Write-Output (@{package=$out;manifest_sha256=$digest;source=$build.source;tool_source=$toolSource;image_archive_bytes=(Get-Item -LiteralPath (Join-Path $out 'images.tar')).Length}|ConvertTo-Json -Compress)
