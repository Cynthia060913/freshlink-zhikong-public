$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'portable-candidate-guards.ps1')
$sha='a'*40;$id='sha256:'+('b'*64)
$good=@{
    build=@{status='candidate_image_built_source_verified_not_application_acceptance';source=$sha;image_id=$id;image='freshlink-application-candidate:'+('a'*12)+'-'+('c'*12);source_zip_sha256='d'*64;image_source_audit=@{status='verified_image_source_and_frontend_presence_not_E1';source=$sha}};
    smoke=@{status='archive_image_default_cmd_http_smoke_passed_not_E1';source=$sha};
    cleanup=@{source=$sha;image_id=$id;proof_exit=0;cleanup_exit=0}
}
$count=0
$case=$good|ConvertTo-Json -Depth 6|ConvertFrom-Json
Assert-FreshLinkPortableEvidence $case.build $case.smoke $case.cleanup
$count++
foreach($mutation in @(
    {param($c)$c.build.source='not-a-sha'},
    {param($c)$c.build.status='building'},
    {param($c)$c.build.image_id='arbitrary'},
    {param($c)$c.build.source_zip_sha256='bad'},
    {param($c)$c.build.image_source_audit.source='e'*40},
    {param($c)$c.build.image_source_audit.status='failed'},
    {param($c)$c.build.image='freshlink-application-candidate:'+('f'*12)+'-'+('c'*12)},
    {param($c)$c.smoke.source='e'*40},
    {param($c)$c.smoke.status='mock'},
    {param($c)$c.cleanup.source='e'*40},
    {param($c)$c.cleanup.image_id='sha256:'+('e'*64)},
    {param($c)$c.cleanup.proof_exit=1},
    {param($c)$c.cleanup.cleanup_exit=1}
)){
    $case=$good|ConvertTo-Json -Depth 6|ConvertFrom-Json
    & $mutation $case
    $rejected=$false
    try {Assert-FreshLinkPortableEvidence $case.build $case.smoke $case.cleanup}catch{$rejected=$true}
    if(-not $rejected){throw 'Invalid portable evidence accepted'}
    $count++
}
$here=(Resolve-Path -LiteralPath $PSScriptRoot).Path
Assert-FreshLinkOwnedPortableInput (Join-Path $here 'portable-candidate-guards.ps1') $here 'portable-candidate-guards.ps1' | Out-Null
$count++
foreach($bad in @(@($here,'wrong.ps1'),@((Join-Path $here 'nonexistent'),'portable-candidate-guards.ps1'))){
    $rejected=$false
    try {Assert-FreshLinkOwnedPortableInput (Join-Path $here 'portable-candidate-guards.ps1') $bad[0] $bad[1]|Out-Null}catch{$rejected=$true}
    if(-not $rejected){throw 'Wrong evidence scope accepted'}
    $count++
}
Write-Output ('PASS '+$count+' synthetic portable evidence/path cases; not archive reload or second-machine acceptance.')
