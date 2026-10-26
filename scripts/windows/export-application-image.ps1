# Export one previously source-audited application image. Never load/tag/push/run it.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$BuildReport)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$root=Get-FreshLinkRoot
$reportPath=(Resolve-Path -LiteralPath $BuildReport).Path
$sourceRoot=[IO.Path]::GetFullPath((Join-Path $root 'work/source-candidates'))+[IO.Path]::DirectorySeparatorChar
if(-not $reportPath.StartsWith($sourceRoot,[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($reportPath) -ne 'application-build.json'){
    throw 'Use an owned source-candidate build report under this workspace'
}
$build=Get-Content -LiteralPath $reportPath -Raw|ConvertFrom-Json
if($build.status -ne 'candidate_image_built_source_verified_not_application_acceptance' -or
   $build.source -notmatch '^[0-9a-f]{40}$' -or
   $build.source_zip_sha256 -notmatch '^[0-9a-f]{64}$' -or
   $build.image_id -notmatch '^sha256:[0-9a-f]{64}$' -or
   $build.image_source_audit.status -ne 'verified_image_source_and_frontend_presence_not_E1'){
    throw 'A successful source-audited image build report is required'
}
$docker=Assert-FreshLinkDocker
$tar=(Get-Command tar.exe -CommandType Application -ErrorAction Stop|Select-Object -First 1).Source
# Address by immutable ID so moving a tag cannot change what is exported.
$id=(& $docker image inspect $build.image_id --format '{{.Id}}').Trim()
if($LASTEXITCODE -or $id -ne $build.image_id){throw 'Audited image ID unavailable'}
$label=(& $docker image inspect $id --format '{{index .Config.Labels "org.opencontainers.image.revision"}}').Trim()
if($LASTEXITCODE -or $label -ne $build.source){throw 'Image source label mismatch'}
$sizeText=(& $docker image inspect $id --format '{{.Size}}').Trim()
$size=[long]0
if($LASTEXITCODE -or -not [long]::TryParse($sizeText,[ref]$size) -or $size -le 0){throw 'Image size unavailable'}
$drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($root))
if($drive.AvailableFreeSpace -lt ($size*2+64MB)){throw 'Insufficient free space; nothing is deleted automatically'}
$name='image-archive-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,12)
$output=Join-Path $root ('work/'+$name)
if(Test-Path -LiteralPath $output){throw 'Refusing existing output directory'}
[IO.Directory]::CreateDirectory($output)|Out-Null
$archive=Join-Path $output 'application-image.tar'
if(Test-Path -LiteralPath $archive){throw 'Refusing existing archive'}
& $docker image save --output $archive $id
if($LASTEXITCODE){throw 'Image export failed; partial output retained, never reported as passed'}
# Read only tar metadata; do not extract files or import/tag an image.
$metadata=& $tar -xOf $archive manifest.json
if($LASTEXITCODE){throw 'Archive metadata unreadable'}
$entries=@(($metadata -join "`n")|ConvertFrom-Json)
if($entries.Count -ne 1){throw 'Expected exactly one exported image'}
$entry=$entries[0]
$configPath=[string]$entry.Config
$expected=$id.Substring(7)
if($configPath -notmatch '^(?:blobs/sha256/)?[0-9a-f]{64}(?:\.json)?$' -or @($entry.RepoTags).Where({$_}).Count){
    throw 'Unexpected config path or tagged archive; do not import'
}
function Read-ArchiveJson([string]$Member) {
    if($Member -notmatch '^(?:index\.json|blobs/sha256/[0-9a-f]{64}|[0-9a-f]{64}\.json)$'){throw 'Unexpected metadata member'}
    $text=& $tar -xOf $archive $Member
    if($LASTEXITCODE){throw 'Required archive metadata missing'}
    return (($text -join "`n")|ConvertFrom-Json)
}
if($configPath -notmatch ('^(?:blobs/sha256/)?'+$expected+'(?:\.json)?$')){
    # The containerd image store identifies an OCI index, not its config blob.
    # Verify the root descriptor and follow its manifest to THIS config.
    $index=Read-ArchiveJson 'index.json'
    if(@($index.manifests).Count -ne 1 -or $index.manifests[0].digest -ne $id){throw 'OCI root identity mismatch'}
    $rootIndex=Read-ArchiveJson ('blobs/sha256/'+$expected)
    $found=$false
    foreach($descriptor in @($rootIndex.manifests)){
        if($descriptor.mediaType -notin @('application/vnd.oci.image.manifest.v1+json','application/vnd.docker.distribution.manifest.v2+json')){continue}
        if($descriptor.digest -notmatch '^sha256:[0-9a-f]{64}$'){throw 'Invalid child manifest descriptor'}
        $child=Read-ArchiveJson ('blobs/sha256/'+$descriptor.digest.Substring(7))
        if($child.config.digest -eq ('sha256:'+($configPath -replace '^blobs/sha256/','' -replace '\.json$',''))){$found=$true}
    }
    if(-not $found){throw 'Config is not linked from the audited OCI root'}
}
$config=Read-ArchiveJson $configPath
if($config.config.Labels.'org.opencontainers.image.revision' -ne $build.source){throw 'Archived source label mismatch'}
$receipt=[ordered]@{
    status='image_archive_exported_identity_verified_not_runtime_acceptance'
    source=$build.source
    source_zip_sha256=$build.source_zip_sha256
    image_id=$id
    archive='application-image.tar'
    archive_bytes=(Get-Item -LiteralPath $archive).Length
    archive_sha256=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    imports_performed=0
    providers_contacted=0
    not_verified=@('import on another computer','runtime dependency availability','complete E1','notification receipt','final frozen version','competition submission')
}
$manifest=Join-Path $output 'image-archive.json'
$stream=[IO.File]::Open($manifest,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
try{$bytes=[Text.Encoding]::UTF8.GetBytes(($receipt|ConvertTo-Json -Depth 5));$stream.Write($bytes,0,$bytes.Length)}finally{$stream.Dispose()}
Write-Output ('PASS export: '+$manifest)
Write-Output 'NOT ACCEPTED: second-machine import/run, E1, notifications or final submission. Runtime dependencies are separate.'
