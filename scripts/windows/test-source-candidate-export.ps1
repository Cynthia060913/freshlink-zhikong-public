[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$repo=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$exporter=Join-Path $PSScriptRoot 'export-source-candidate.ps1'
$run=Join-Path $repo ('work/source-export-tests-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+[guid]::NewGuid().ToString('N'))
$fixture=Join-Path $run '中文 空格 source'
[IO.Directory]::CreateDirectory((Join-Path $fixture 'docs/中文 路径'))|Out-Null
[IO.Directory]::CreateDirectory((Join-Path $fixture 'assets'))|Out-Null
[IO.Directory]::CreateDirectory((Join-Path $fixture 'frontend'))|Out-Null
$utf8=[Text.UTF8Encoding]::new($false)
function Write-Fixture([string]$Name,[string]$Value){[IO.File]::WriteAllText((Join-Path $fixture $Name),$Value,$utf8)}
function Fixture-Git([string[]]$Arguments){
    $out=& git -C $fixture @Arguments 2>&1
    if($LASTEXITCODE -ne 0){throw 'Fixture Git command failed'}
    return $out
}
function Commit-Fixture([string[]]$Names){
    [void](Fixture-Git (@('add','--')+$Names))
    [void](Fixture-Git @('-c','user.name=Source Export Test','-c','user.email=source-export@example.invalid','commit','-m','synthetic export fixture'))
    return ((Fixture-Git @('rev-parse','HEAD')) -join '').Trim()
}
function Expect-Rejection([string]$Commit,[string]$Message){
    $caught=$false
    try{[void](& $exporter -Repository $fixture -Revision $Commit)}catch{$caught=$true;if($_.Exception.Message -notmatch $Message){throw 'Unexpected export rejection'}}
    if(-not $caught){throw 'Unsafe candidate unexpectedly accepted'}
}
Write-Fixture 'README.md' "# Synthetic fixture`nNo real configuration.`n"
Write-Fixture 'docs/中文 路径/说明.txt' "中文路径按字节核对。`n第二行。`n"
[IO.File]::WriteAllBytes((Join-Path $fixture 'assets/sample.bin'),[byte[]](0,1,255,10,13,0))
[void](Fixture-Git @('init','--quiet'))
[void](Fixture-Git @('config','core.autocrlf','false'))
[void](Fixture-Git @('config','core.eol','crlf'))
Write-Fixture '.gitattributes' "* text=auto`n*.bin -text`n"
$revision=Commit-Fixture @('README.md','docs/中文 路径/说明.txt','assets/sample.bin','.gitattributes')
Write-Fixture '.env.freshlink' 'UNTRACKED_SYNTHETIC_VALUE_ONLY'
$candidate=(& $exporter -Repository $fixture -Revision $revision)|ConvertFrom-Json
$manifest=Get-Content -LiteralPath (Join-Path (Split-Path -Parent $candidate.zip) 'manifest.json') -Raw -Encoding UTF8|ConvertFrom-Json
if($candidate.files -ne 4 -or $candidate.unicode_paths -ne 1 -or $manifest.binary_files_not_text_scanned -ne 1){throw 'Fixture path/binary count mismatch'}
if(@($manifest.files|Where-Object path -eq '.env.freshlink').Count -ne 0){throw 'Untracked private file exported'}
if(((Fixture-Git @('config','--get','core.eol')) -join '').Trim() -ne 'crlf'){throw 'Exporter changed Git settings'}
$zipHash=(Get-FileHash -LiteralPath $candidate.zip -Algorithm SHA256).Hash
$repeat=(& $exporter -Repository $fixture -Revision $revision)|ConvertFrom-Json
if($repeat.zip -eq $candidate.zip -or (Get-FileHash -LiteralPath $candidate.zip -Algorithm SHA256).Hash -ne $zipHash){throw 'Exporter overwrote prior evidence'}
Expect-Rejection ('0'*40) 'Git read/archive failed'
$privateRevision=Commit-Fixture @('.env.freshlink')
Expect-Rejection $privateRevision 'Private path'
[void](Fixture-Git @('rm','--','.env.freshlink'))
Write-Fixture '.env' 'UNREVIEWED_SYNTHETIC_CONFIG_ONLY'
$unreviewedRevision=Commit-Fixture @('.env')
Expect-Rejection $unreviewedRevision 'Unreviewed environment template'
[void](Fixture-Git @('rm','--','.env'))
Write-Fixture 'credential.txt' ('ghp_' + ('x'*36))
$credentialRevision=Commit-Fixture @('credential.txt')
Expect-Rejection $credentialRevision 'Credential pattern detected'
$report=[ordered]@{status='passed';powershell=$PSVersionTable.PSVersion.ToString();checks=@('unicode/binary exact-byte ZIP','host CRLF preference unchanged','untracked private config excluded','repeated export preserves prior evidence','unknown SHA refused','tracked private config refused','changed environment template refused','constructed credential pattern refused');source_revision=$revision;fixture=$fixture}
[IO.File]::WriteAllText((Join-Path $run 'result.json'),($report|ConvertTo-Json -Depth 4),$utf8)
$report|ConvertTo-Json -Depth 4
