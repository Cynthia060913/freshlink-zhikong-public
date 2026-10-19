# Only cached Docker and an owned temporary project; never host Python or app DB.
[CmdletBinding()]
param(
    [string]$AppImage='freshlink-app:p0',
    [ValidateSet('candidate','published-main')][string]$Scope='candidate'
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$savedLocation=Get-Location
$names=@('BASELINE_APP_IMAGE','BASELINE_RESULT_DIR','BASELINE_SOURCE_COMMIT',
    'BASELINE_SOURCE_DIRTY','BASELINE_PROOF_SCOPE','BASELINE_PROOF_ACTOR')
$prior=@{}
foreach($name in $names){$prior[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$docker=$null
$compose=$null
$result=$null
$exitCode=1
try {
    $root=Get-FreshLinkRoot
    Set-Location -LiteralPath $root
    $source=(& git rev-parse HEAD).Trim()
    if($LASTEXITCODE -ne 0 -or $source -notmatch '^[0-9a-f]{40}$'){throw 'Invalid exact source'}
    $dirty=[bool](& git status --porcelain)
    if($Scope -eq 'published-main'){
        $main=(& git rev-parse refs/remotes/origin/main).Trim()
        if($LASTEXITCODE -ne 0 -or $main -ne $source -or $dirty){throw 'Published proof requires clean checked-out origin/main'}
        if($env:GITHUB_ACTIONS -eq 'true' -and $env:GITHUB_REF -ne 'refs/heads/main'){throw 'Manual published proof must run on main'}
    }
    $docker=Assert-FreshLinkDocker
    $run='baseline-http-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+$PID
    if($run -notmatch '^baseline-http-\d{14}-\d+$'){throw 'Invalid owned project'}
    $result=Join-Path $root ('work/'+$run)
    [IO.Directory]::CreateDirectory($result)|Out-Null
    $env:BASELINE_APP_IMAGE=$AppImage
    $env:BASELINE_RESULT_DIR=$result
    $env:BASELINE_SOURCE_COMMIT=$source
    $env:BASELINE_SOURCE_DIRTY=if($dirty){'1'}else{'0'}
    $env:BASELINE_PROOF_SCOPE=$Scope
    $env:BASELINE_PROOF_ACTOR=if($env:GITHUB_ACTIONS -eq 'true'){$env:GITHUB_ACTOR}else{'local'}
    $compose=@('compose','--project-name',$run,'-f','compose.baseline-http-tests.yml')
    & $docker @compose config --quiet
    if($LASTEXITCODE -ne 0){throw 'Invalid isolated configuration'}
    & $docker @compose up -d --wait --pull never test-db
    if($LASTEXITCODE -ne 0){throw 'Owned test database startup failed'}
    & $docker @compose run --rm --no-deps --pull never test-runner
    if($LASTEXITCODE -ne 0){throw 'Real HTTP baseline failed; retain evidence'}
    $proof=Get-Content -LiteralPath (Join-Path $result 'baseline-http.json') -Raw|ConvertFrom-Json
    if($proof.phase -ne 'isolated_baseline_real_http_passed' -or $proof.source_commit -ne $source -or $proof.scope -ne $Scope){throw 'Unexpected proof identity'}
    if((& git rev-parse HEAD).Trim() -ne $source -or [bool](& git status --porcelain) -ne $dirty){throw 'Source changed during proof'}
    Write-Output 'PASS actual server health/login/device APIs; no browser, teammate local machine, notifications or E1 claim.'
    Write-Output ('Evidence: '+$result)
    Get-Content -LiteralPath (Join-Path $result 'baseline-http.json')
    $exitCode=0
} catch {Write-Output ('STOP: '+$_.Exception.Message)}
finally {
    if($docker -and $compose){
        & $docker @compose down --remove-orphans
        $cleanupExit=$LASTEXITCODE
        if($cleanupExit -ne 0){$exitCode=1;Write-Output 'STOP: owned cleanup failed'}
        if($result){
            try {
                $receipt=@{project=$run;source_commit=$source;scope=$Scope;cleanup_exit=$cleanupExit;proof_exit=$exitCode}|ConvertTo-Json
                $stream=[IO.File]::Open((Join-Path $result 'baseline-cleanup.json'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
                $writer=New-Object System.IO.StreamWriter($stream,(New-Object System.Text.UTF8Encoding($false)))
                try {$writer.Write($receipt)} finally {$writer.Dispose()}
            } catch {$exitCode=1;Write-Output 'STOP: cleanup receipt unavailable'}
        }
    }
    foreach($name in $names){[Environment]::SetEnvironmentVariable($name,$prior[$name],'Process')}
    Set-Location -LiteralPath $savedLocation.Path
}
exit $exitCode
