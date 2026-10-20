$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'application-image-guards.ps1')
$count=0
foreach($valid in @([pscustomobject]@{},[pscustomobject]@{command=$null;entrypoint=$null})){
    Assert-FreshLinkDefaultImageCommand $valid
    $count++
}
foreach($invalid in @([pscustomobject]@{command=@()},[pscustomobject]@{entrypoint=@()},[pscustomobject]@{command=''},[pscustomobject]@{command=@('fastapi','run')})){
    $rejected=$false
    try {Assert-FreshLinkDefaultImageCommand $invalid}catch{if($_.Exception.Message -ne 'Default application CMD must not be overridden'){throw};$rejected=$true}
    if(-not $rejected){throw 'An explicit command override unexpectedly passed'}
    $count++
}
$image=[pscustomobject]@{Id='synthetic-image';Config=[pscustomobject]@{Cmd=@('fastapi','run','--workers','4');Entrypoint=$null}}
$same=[pscustomobject]@{Image=$image.Id;Config=$image.Config}
Assert-FreshLinkRunningImageIdentity $image $same
$count++
foreach($invalid in @([pscustomobject]@{Image='wrong-image';Config=$image.Config},[pscustomobject]@{Image=$image.Id;Config=[pscustomobject]@{Cmd=@('echo','fake');Entrypoint=$null}},[pscustomobject]@{Image=$image.Id;Config=[pscustomobject]@{Cmd=$image.Config.Cmd;Entrypoint=@()}})){
    $rejected=$false
    try{Assert-FreshLinkRunningImageIdentity $image $invalid}catch{if($_.Exception.Message -notmatch '^Running container '){throw};$rejected=$true}
    if(-not $rejected){throw 'Unexpected runtime image/command accepted'}
    $count++
}
Write-Output ('PASS '+$count+' synthetic image command/identity guard cases; not live application acceptance.')
