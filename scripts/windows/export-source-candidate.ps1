[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$Revision,
    [string]$Repository=(Join-Path $PSScriptRoot '../..')
)
$ErrorActionPreference='Stop'
$repo=(Resolve-Path -LiteralPath $Repository).Path
$git=(Get-Command git -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source

function Quote-ProcessArgument([string]$Value) {
    # Windows PowerShell 5.1 lacks ProcessStartInfo.ArgumentList. No shell is used.
    $escaped=[regex]::Replace($Value,'(\\*)"','$1$1\"')
    $escaped=[regex]::Replace($escaped,'(\\+)$','$1$1')
    return '"'+$escaped+'"'
}
function Read-SourceGit([string[]]$Arguments) {
    $start=[Diagnostics.ProcessStartInfo]::new()
    $start.FileName=$git
    $start.UseShellExecute=$false
    $start.RedirectStandardOutput=$true
    $start.RedirectStandardError=$true
    $start.StandardOutputEncoding=[Text.UTF8Encoding]::new($false)
    $allArguments=@('-c','core.longpaths=true','-C',$repo)+$Arguments
    if($start.PSObject.Properties['ArgumentList']) {
        foreach($argument in $allArguments){$start.ArgumentList.Add($argument)}
    } else {
        $start.Arguments=(@($allArguments|ForEach-Object {Quote-ProcessArgument $_}) -join ' ')
    }
    $process=[Diagnostics.Process]::Start($start)
    try {
        $result=$process.StandardOutput.ReadToEnd()
        $diagnostic=$process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if($process.ExitCode -ne 0){throw ('Git read/archive failed: operation='+$Arguments[0]+' exit='+$process.ExitCode+'; diagnostics are not printed.')}
        return $result
    } finally {$process.Dispose()}
}
if((Read-SourceGit @('rev-parse','--verify',($Revision+'^{commit}'))).Trim() -ne $Revision){throw 'Exact commit unavailable'}
if((Read-SourceGit @('rev-parse','--show-object-format')).Trim() -ne 'sha1'){throw 'Unsupported Git object format'}
$tree=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
$windowsNames=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$windowsPrefixes=[Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
# These are the reviewed template blobs from the shared e235 baseline, not secrets.
$templateBlobs=@{'.env'='e16784221faff9125e09691c28286a4031420dca';'frontend/.env'='35d97648c6b870c64e1d39709d2269a33c18f509'}
$total=[long]0
foreach($record in (Read-SourceGit @('ls-tree','--full-tree','-r','-l','-z',$Revision)).Split([char]0,[StringSplitOptions]::RemoveEmptyEntries)) {
    if($record -notmatch '^(?<mode>[0-9]+) (?<kind>\w+) (?<oid>[0-9a-f]{40}) +(?<size>[0-9]+|-)\t(?<path>[\s\S]+)$'){throw 'Unsupported tree entry'}
    $path=$Matches.path;$mode=$Matches.mode;$oid=$Matches.oid;$size=$Matches.size
    if($Matches.kind -ne 'blob' -or $mode -notin @('100644','100755')){throw 'Non-regular entry needs separate packaging review'}
    $parts=$path.Split('/');$leaf=$parts[-1]
    $example=$leaf -match '\.(example|sample|template)$'
    if($path -match '(?i)(^work/|(^|/)\.git(/|$)|(^|/)__pycache__(/|$)|(^|/)id_(rsa|ed25519)$|\.(db|sqlite|sqlite3|pyc|key)$)' -or
        ($leaf.StartsWith('.env.') -and -not $example) -or ($leaf -eq '.env' -and $path -notin @('.env','frontend/.env'))){throw ('Private path in source: '+$path)}
    if($templateBlobs.ContainsKey($path) -and $oid -ne $templateBlobs[$path]){throw ('Unreviewed environment template: '+$path+'; review contents before updating the approved blob')}
    if($path -match '[\\:\x00-\x1f]' -or $path.StartsWith('/') -or @($parts|Where-Object {$_ -in @('.','..','') -or $_ -match '[. ]$|(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)'}).Count){throw 'Unsafe or Windows-incompatible source path'}
    if(-not $windowsNames.Add($path)){throw 'Case-colliding Windows source paths'}
    for($i=0;$i -lt $parts.Count;$i++){
        $partPath=$parts[0..$i] -join '/'
        if($windowsPrefixes.ContainsKey($partPath)){
            if($windowsPrefixes[$partPath] -cne $partPath){throw 'Case-colliding Windows directory paths'}
        }else{$windowsPrefixes.Add($partPath,$partPath)}
    }
    $total+=[long]$size
    if([long]$size -gt 20MB -or $total -gt 100MB -or $tree.Count -ge 10000){throw 'Source exceeds bounded export scope'}
    $tree.Add($path,[pscustomobject]@{mode=$mode;blob=$oid;bytes=[long]$size})
}
if($tree.Count -eq 0){throw 'Empty source tree'}
$base=Join-Path $repo 'work/source-candidates'
$root=Join-Path $base ((Get-Date -Format 'yyyyMMddHHmmss')+'-'+[guid]::NewGuid().ToString('N'))
if(Test-Path -LiteralPath $root){throw 'Refusing existing output directory'}
[IO.Directory]::CreateDirectory($root)|Out-Null
$zip=Join-Path $root ('FreshLink-source-'+$Revision.Substring(0,12)+'-candidate.zip')
# Per-command conversion only: never change global or repository Git settings.
[void](Read-SourceGit @('-c','core.autocrlf=false','-c','core.eol=lf','archive','--format=zip',('--output='+$zip),'--prefix=FreshLink/',$Revision))
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive=[IO.Compression.ZipFile]::OpenRead($zip)
$seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
try {
    foreach($entry in $archive.Entries) {
        if($entry.FullName.EndsWith('/')){continue}
        if(-not $entry.FullName.StartsWith('FreshLink/',[StringComparison]::Ordinal)){throw 'Unexpected ZIP root'}
        $relative=$entry.FullName.Substring('FreshLink/'.Length)
        if(-not $tree.ContainsKey($relative) -or -not $seen.Add($relative) -or $entry.Length -ne $tree[$relative].bytes){throw 'ZIP path/size mismatch'}
    }
    if($seen.Count -ne $tree.Count){throw 'ZIP omits tracked files; export attributes need review'}
} finally {$archive.Dispose()}
$extract=Join-Path $root 'windows-extraction'
[IO.Compression.ZipFile]::ExtractToDirectory($zip,$extract)
$prefix=[IO.Path]::GetFullPath($extract)+[IO.Path]::DirectorySeparatorChar
$rows=[Collections.Generic.List[object]]::new()
$unicodeCount=0;$binaryCount=0
$credentialPattern='\b(?:github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{35}|xox[baprs]-[A-Za-z0-9-]{20,})\b|-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'
foreach($relative in $tree.Keys) {
    $actual=[IO.Path]::GetFullPath((Join-Path $extract ('FreshLink/'+$relative)))
    if(-not $actual.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)){throw 'Extraction escaped owned directory'}
    $bytes=[IO.File]::ReadAllBytes($actual)
    $stream=[IO.MemoryStream]::new();$sha1=[Security.Cryptography.SHA1]::Create();$sha256=[Security.Cryptography.SHA256]::Create()
    try {
        $header=[Text.Encoding]::ASCII.GetBytes(('blob '+$bytes.LongLength+[char]0))
        $stream.Write($header,0,$header.Length);$stream.Write($bytes,0,$bytes.Length)
        $blob=[BitConverter]::ToString($sha1.ComputeHash($stream.ToArray())).Replace('-','').ToLowerInvariant()
        if($blob -ne $tree[$relative].blob){throw ('Source byte mismatch: '+$relative)}
        $digest=[BitConverter]::ToString($sha256.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()
    } finally {$stream.Dispose();$sha1.Dispose();$sha256.Dispose()}
    if($bytes -contains [byte]0){$binaryCount++}
    elseif([Text.Encoding]::UTF8.GetString($bytes) -match $credentialPattern){throw ('Credential pattern detected: '+$relative+'; output retained privately, NOT approved for sharing')}
    if($relative -match '[^\x00-\x7f]'){$unicodeCount++}
    $rows.Add([pscustomobject]@{path=$relative;bytes=$bytes.LongLength;git_mode=$tree[$relative].mode;git_blob=$blob;sha256=$digest})
}
$manifest=[ordered]@{
    status='verified_source_candidate_not_final_submission';revision=$Revision
    zip_name=[IO.Path]::GetFileName($zip);zip_sha256=(Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    file_count=$rows.Count;non_ascii_paths_verified=$unicodeCount;binary_files_not_text_scanned=$binaryCount
    verification='Actual ZIP extraction; every tracked path and byte matches pinned Git tree'
    security_scope='Finite path/credential patterns only; not a complete privacy/security/license audit'
    not_verified=@('E1','final application package','second computer','notification receipts','design/PPT/video','final submission')
    files=@($rows.ToArray()|Sort-Object path)
}
[IO.File]::WriteAllText((Join-Path $root 'manifest.json'),($manifest|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
[pscustomobject]@{status=$manifest.status;revision=$Revision;files=$rows.Count;unicode_paths=$unicodeCount;zip=$zip;zip_sha256=$manifest.zip_sha256}|ConvertTo-Json -Compress
