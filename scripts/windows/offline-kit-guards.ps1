# Shared finite transport checks. No Docker, Git, Python or extraction is needed.
Set-StrictMode -Version Latest

function Get-OfflineDigest([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-OfflineRegularFile([string]$Root,[string]$Name) {
    if($Name -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._-]*$'){throw 'Flat package filename required'}
    $item=Get-Item -LiteralPath (Join-Path $Root $Name) -Force -ErrorAction Stop
    if($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Regular package files only'}
    return $item.FullName
}

function Assert-OfflineKit([string]$Directory,[string]$ExpectedManifestSha256) {
    if($ExpectedManifestSha256 -cnotmatch '^[0-9a-f]{64}$'){throw 'Expected manifest hash must be supplied separately'}
    $folder=Get-Item -LiteralPath $Directory -Force -ErrorAction Stop
    if(-not $folder.PSIsContainer -or ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Regular package directory required'}
    $root=$folder.FullName
    $manifestPath=Assert-OfflineRegularFile $root 'offline-manifest.json'
    if((Get-OfflineDigest $manifestPath) -cne $ExpectedManifestSha256){throw 'Manifest hash mismatch; no Docker operation permitted'}
    $manifest=Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8|ConvertFrom-Json
    if($manifest.format -ne 'freshlink-offline-fleet-kit-v1' -or
        $manifest.source -cnotmatch '^[0-9a-f]{40}$' -or
        $manifest.tool_source -cnotmatch '^[0-9a-f]{40}$' -or
        $manifest.status -ne 'transport_candidate_not_E1_or_second_machine_acceptance'){throw 'Wrong candidate identity/scope'}
    $allowed=@('source.zip','source-manifest.json','images.tar','start.sh','mosquitto.conf','acl','run-offline-fleet.ps1','offline-kit-guards.ps1','README.md')
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach($file in $manifest.files){
        if($file.name -cnotin $allowed -or -not $seen.Add($file.name) -or $file.sha256 -cnotmatch '^[0-9a-f]{64}$'){throw 'Unexpected or duplicate package entry'}
        $path=Assert-OfflineRegularFile $root $file.name
        if((Get-Item -LiteralPath $path).Length -ne $file.bytes -or (Get-OfflineDigest $path) -cne $file.sha256){throw ('Package byte/hash mismatch: '+$file.name)}
    }
    if($seen.Count -ne $allowed.Count){throw 'Package is incomplete'}
    foreach($entry in Get-ChildItem -LiteralPath $root -Force){
        if($entry.Name -cne 'offline-manifest.json' -and -not $seen.Contains($entry.Name)){throw 'Unlisted package file/directory; keep evidence outside package'}
    }
    $roles=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach($image in $manifest.images){
        if($image.role -cnotin @('application','postgres','mqtt') -or -not $roles.Add($image.role) -or
            $image.id -cnotmatch '^sha256:[0-9a-f]{64}$' -or $image.platform -cne 'linux/amd64'){throw 'Invalid image identity/platform'}
    }
    if($roles.Count -ne 3){throw 'All three dependency images are required'}
    $source=Get-Content -LiteralPath (Join-Path $root 'source-manifest.json') -Raw -Encoding UTF8|ConvertFrom-Json
    if($source.revision -cne $manifest.source -or $source.status -ne 'verified_source_candidate_not_final_submission' -or
        $source.zip_sha256 -cne (Get-OfflineDigest (Join-Path $root 'source.zip')) -or
        $source.file_count -lt 1 -or $source.file_count -gt 10000){throw 'Source archive identity mismatch'}
    # Check every ZIP file in place, including Chinese names. Never extract paths.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $sourceFiles=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    foreach($file in $source.files){
        if($sourceFiles.ContainsKey($file.path)){throw 'Duplicate source manifest path'}
        $sourceFiles.Add($file.path,$file)
    }
    $archive=[IO.Compression.ZipFile]::OpenRead((Join-Path $root 'source.zip'))
    $zipSeen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $zipBytes=[long]0
    try {
        foreach($entry in $archive.Entries){
            if($entry.FullName.EndsWith('/')){continue}
            if(-not $entry.FullName.StartsWith('FreshLink/',[StringComparison]::Ordinal)){throw 'Unexpected source ZIP root'}
            $relative=$entry.FullName.Substring(10)
            if($relative -match '(^|/)\.\.?(/|$)|[\\:\x00-\x1f]' -or
                -not $sourceFiles.ContainsKey($relative) -or -not $zipSeen.Add($relative)){throw 'Unsafe/unknown source ZIP entry'}
            $expected=$sourceFiles[$relative]
            $zipBytes+=$entry.Length
            if($entry.Length -gt 20MB -or $zipBytes -gt 100MB -or $entry.Length -ne $expected.bytes){throw 'Source ZIP size mismatch'}
            $stream=$entry.Open();$sha=[Security.Cryptography.SHA256]::Create()
            try {$digest=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}
            finally {$stream.Dispose();$sha.Dispose()}
            if($digest -cne $expected.sha256){throw 'Source ZIP byte mismatch'}
        }
        if($zipSeen.Count -ne $sourceFiles.Count -or $zipSeen.Count -ne $source.file_count){throw 'Source ZIP omits files'}
    } finally {$archive.Dispose()}
    return $manifest
}

function Get-OfflineLocalDocker {
    if($env:DOCKER_HOST -or $env:DOCKER_CONTEXT){throw 'Unset explicit Docker host/context overrides before local proof'}
    $docker=(Get-Command docker -CommandType Application -ErrorAction Stop|Select-Object -First 1).Source
    $endpoint=(& $docker context inspect --format '{{.Endpoints.docker.Host}}'|Out-String).Trim()
    if($LASTEXITCODE -or $endpoint -notmatch '^npipe://'){throw 'Only a local Windows named-pipe Docker Desktop engine is allowed'}
    $platform=(& $docker info --format '{{.OSType}}/{{.Architecture}}'|Out-String).Trim()
    if($LASTEXITCODE -or $platform -notin @('linux/x86_64','linux/amd64')){throw 'Start the local Linux amd64 Docker Desktop engine'}
    & $docker compose version|Out-Null
    if($LASTEXITCODE){throw 'Docker Compose unavailable'}
    return $docker
}
