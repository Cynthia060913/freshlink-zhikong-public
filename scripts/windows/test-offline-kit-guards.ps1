# Synthetic corruption tests only. No Docker, external network, host Python or deletion.
[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'offline-kit-guards.ps1')
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$repo=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$run=Join-Path $repo ('work/offline-kit-tests/'+[guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($run)|Out-Null
$utf8=[Text.UTF8Encoding]::new($false)
$sourceId='a'*40
function Write-TestJson([string]$Path,$Value){[IO.File]::WriteAllText($Path,($Value|ConvertTo-Json -Depth 12),$utf8)}
function Save-TestManifest([string]$Root,$Value){
    foreach($file in $Value.files){
        $path=Join-Path $Root $file.name
        if(Test-Path -LiteralPath $path -PathType Leaf){$file.bytes=(Get-Item -LiteralPath $path).Length;$file.sha256=Get-OfflineDigest $path}
    }
    Write-TestJson (Join-Path $Root 'offline-manifest.json') $Value
    return (Get-OfflineDigest (Join-Path $Root 'offline-manifest.json'))
}
function New-TestKit([string]$Case){
    $root=Join-Path $run $Case
    [IO.Directory]::CreateDirectory($root)|Out-Null
    foreach($name in @('images.tar','start.sh','mosquitto.conf','acl','run-offline-fleet.ps1','offline-kit-guards.ps1','README.md')){
        [IO.File]::WriteAllText((Join-Path $root $name),'synthetic test content',$utf8)
    }
    $zip=Join-Path $root 'source.zip'
    $archive=[IO.Compression.ZipFile]::Open($zip,[IO.Compression.ZipArchiveMode]::Create)
    try {
        $entry=$archive.CreateEntry('FreshLink/docs/'+[char]0x4e2d+[char]0x6587+'.txt')
        $stream=$entry.Open();$bytes=$utf8.GetBytes('synthetic source')
        try {$stream.Write($bytes,0,$bytes.Length)}finally{$stream.Dispose()}
    } finally {$archive.Dispose()}
    $sha=[Security.Cryptography.SHA256]::Create()
    try {$digest=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
    $source=@{revision=$sourceId;status='verified_source_candidate_not_final_submission';zip_sha256=(Get-OfflineDigest $zip);file_count=1;files=@(@{path=('docs/'+[char]0x4e2d+[char]0x6587+'.txt');bytes=$bytes.Length;sha256=$digest})}
    Write-TestJson (Join-Path $root 'source-manifest.json') $source
    $images=@();$index=1
    foreach($role in @('application','postgres','mqtt')){$images+=@{role=$role;id=('sha256:'+([string]$index)*64);platform='linux/amd64'};$index++}
    $files=@(Get-ChildItem -LiteralPath $root -File|ForEach-Object {@{name=$_.Name;bytes=$_.Length;sha256=(Get-OfflineDigest $_.FullName)}})
    $manifest=@{format='freshlink-offline-fleet-kit-v1';status='transport_candidate_not_E1_or_second_machine_acceptance';source=$sourceId;tool_source=('b'*40);images=$images;files=$files}
    $hash=Save-TestManifest $root $manifest
    return @{root=$root;manifest=$manifest;hash=$hash;source=$source}
}
function Require-Rejection($Fixture,[string]$Hash){
    $rejected=$false
    try {[void](Assert-OfflineKit $Fixture.root $Hash)}catch{$rejected=$true}
    if(-not $rejected){throw 'Corrupt candidate incorrectly accepted'}
}
$good=New-TestKit 'valid-unicode'
foreach($name in @('run-offline-fleet.ps1','offline-kit-guards.ps1')){
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $good.root $name)
}
$good.hash=Save-TestManifest $good.root $good.manifest
[void](Assert-OfflineKit $good.root $good.hash)
# Exercise the actual standalone default-package parameter path, not only a
# function call. Verify-only must work without Git, Python or Docker calls.
& (Join-Path $good.root 'run-offline-fleet.ps1') -ExpectedManifestSha256 $good.hash
$cases=@('manifest-hash','payload-byte','missing-file','extra-file','path-traversal','duplicate-file','missing-image','wrong-platform','wrong-source','zip-byte','zip-omission','zip-escape')
foreach($case in $cases){
    $fixture=New-TestKit $case
    $hash=$fixture.hash
    switch($case){
        'manifest-hash' {$hash='0'*64}
        'payload-byte' {[IO.File]::AppendAllText((Join-Path $fixture.root 'images.tar'),'X',$utf8)}
        'missing-file' {$fixture.manifest.files=@($fixture.manifest.files|Where-Object name -ne 'acl');$hash=Save-TestManifest $fixture.root $fixture.manifest}
        'extra-file' {[IO.File]::WriteAllText((Join-Path $fixture.root '.env.local'),'synthetic only',$utf8)}
        'path-traversal' {$fixture.manifest.files[0].name='../outside';Write-TestJson (Join-Path $fixture.root 'offline-manifest.json') $fixture.manifest;$hash=Get-OfflineDigest (Join-Path $fixture.root 'offline-manifest.json')}
        'duplicate-file' {$fixture.manifest.files+=@($fixture.manifest.files[0]);$hash=Save-TestManifest $fixture.root $fixture.manifest}
        'missing-image' {$fixture.manifest.images=@($fixture.manifest.images|Where-Object role -ne 'mqtt');$hash=Save-TestManifest $fixture.root $fixture.manifest}
        'wrong-platform' {$fixture.manifest.images[0].platform='linux/arm64';$hash=Save-TestManifest $fixture.root $fixture.manifest}
        'wrong-source' {$fixture.source.revision='c'*40;Write-TestJson (Join-Path $fixture.root 'source-manifest.json') $fixture.source;$hash=Save-TestManifest $fixture.root $fixture.manifest}
        'zip-byte' {$fixture.source.files[0].sha256='0'*64;Write-TestJson (Join-Path $fixture.root 'source-manifest.json') $fixture.source;$hash=Save-TestManifest $fixture.root $fixture.manifest}
        'zip-omission' {$fixture.source.file_count=2;Write-TestJson (Join-Path $fixture.root 'source-manifest.json') $fixture.source;$hash=Save-TestManifest $fixture.root $fixture.manifest}
        'zip-escape' {
            $zip=Join-Path $fixture.root 'source.zip'
            $archive=[IO.Compression.ZipFile]::Open($zip,[IO.Compression.ZipArchiveMode]::Update)
            try {[void]$archive.CreateEntry('FreshLink/../escape.txt')}finally{$archive.Dispose()}
            $fixture.source.zip_sha256=Get-OfflineDigest $zip
            Write-TestJson (Join-Path $fixture.root 'source-manifest.json') $fixture.source
            $hash=Save-TestManifest $fixture.root $fixture.manifest
        }
    }
    Require-Rejection $fixture $hash
}
Write-Output ('PASS valid Unicode ZIP and '+$cases.Count+' corruption cases rejected without Docker; evidence='+$run)
