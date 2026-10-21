function Assert-FreshLinkPortableEvidence($Build,$Smoke,$Cleanup) {
    if($Build.status -ne 'candidate_image_built_source_verified_not_application_acceptance' -or
       $Build.source -notmatch '^[0-9a-f]{40}$' -or
       $Build.image_id -notmatch '^sha256:[0-9a-f]{64}$' -or
       $Build.image -notmatch '^freshlink-application-candidate:[0-9a-f]{12}-[0-9a-f]{12}$' -or
       $Build.source_zip_sha256 -notmatch '^[0-9a-f]{64}$') {throw 'Invalid verified build identity'}
    if($Build.image_source_audit.status -ne 'verified_image_source_and_frontend_presence_not_E1' -or
       $Build.image_source_audit.source -ne $Build.source -or
       -not $Build.image.Contains($Build.source.Substring(0,12))) {throw 'Image source audit mismatch'}
    if($Smoke.status -ne 'archive_image_default_cmd_http_smoke_passed_not_E1' -or
       $Smoke.source -ne $Build.source -or $Cleanup.source -ne $Build.source -or
       $Cleanup.image_id -ne $Build.image_id -or $Cleanup.proof_exit -ne 0 -or
       $Cleanup.cleanup_exit -ne 0) {throw 'Image smoke/cleanup evidence mismatch'}
}

function Assert-FreshLinkOwnedPortableInput([string]$Path,[string]$Root,[string]$Leaf) {
    $resolved=(Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    $allowed=[IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar)+[IO.Path]::DirectorySeparatorChar
    if(-not $resolved.StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase) -or
       [IO.Path]::GetFileName($resolved) -ne $Leaf) {throw 'Input is outside the owned evidence scope'}
    $item=Get-Item -LiteralPath $resolved
    while($null -ne $item -and $item.FullName.Length -ge $allowed.TrimEnd([IO.Path]::DirectorySeparatorChar).Length) {
        if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {throw 'Linked evidence paths are forbidden'}
        $item=if($item -is [IO.DirectoryInfo]){$item.Parent}else{$item.Directory}
    }
    return $resolved
}
