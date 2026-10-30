# Optional isolated proof. Never installs packages or runs host Python.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$BundleDirectory,
    [Parameter(Mandatory=$true)][string]$BundleSource,
    [Parameter(Mandatory=$true)][string]$NodeExecutable,
    [Parameter(Mandatory=$true)][string]$PlaywrightModules,
    [Parameter(Mandatory=$true)][string]$ChromiumExecutable,
    [string]$AppImage='freshlink-app:p0',
    [switch]$RecordVideo
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$priorLocation=Get-Location
$names=@('CONTROL_BROWSER_SOURCE','CONTROL_BROWSER_BUNDLE','CONTROL_BROWSER_RESULTS','CONTROL_BROWSER_APP_IMAGE','CONTROL_BROWSER_RECORD_VIDEO')
$saved=@{}
foreach($name in $names){$saved[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$docker=$null
$compose=$null
$results=$null
$proofExit=1
$cleanupExit=$null
try {
    $root=Get-FreshLinkRoot
    Set-Location -LiteralPath $root
    $source=(& git rev-parse HEAD).Trim()
    if($LASTEXITCODE -ne 0 -or $source -notmatch '^[0-9a-f]{40}$'){throw 'Missing source identity'}
    $beforeStatus=(& git status --porcelain=v1 | Out-String)
    $frontendTree=(& git rev-parse HEAD:frontend).Trim()
    $bundleTree=(& git rev-parse ($BundleSource+':frontend')).Trim()
    if($LASTEXITCODE -ne 0 -or $frontendTree -ne $bundleTree){throw 'Bundle source does not match current frontend tree'}
    $bundle=(Resolve-Path -LiteralPath $BundleDirectory).Path
    $workPrefix=(Join-Path $root 'work')+[IO.Path]::DirectorySeparatorChar
    if(-not $bundle.StartsWith($workPrefix,[StringComparison]::OrdinalIgnoreCase)){throw 'Bundle must be preserved under this checkout work directory'}
    foreach($file in @((Join-Path $bundle 'index.html'),$NodeExecutable,$ChromiumExecutable,(Join-Path $PlaywrightModules 'playwright/package.json'))){
        if(-not (Test-Path -LiteralPath $file -PathType Leaf)){throw 'A required pre-existing bundle/browser/runtime file is missing; do not install globally'}
    }
    $docker=Assert-FreshLinkDocker
    $runId='control-browser-'+(Get-Date -Format 'yyyyMMddHHmmss')+'-'+$PID
    if($runId -notmatch '^control-browser-\d{14}-\d+$'){throw 'Invalid owned project'}
    $results=Join-Path $root ('work/'+$runId)
    [IO.Directory]::CreateDirectory($results)|Out-Null
    $env:CONTROL_BROWSER_SOURCE=$source
    $env:CONTROL_BROWSER_BUNDLE=$bundle
    $env:CONTROL_BROWSER_RESULTS=$results
    $env:CONTROL_BROWSER_APP_IMAGE=$AppImage
    $env:CONTROL_BROWSER_RECORD_VIDEO=if($RecordVideo){'1'}else{'0'}
    $compose=@('compose','--project-name',$runId,'-f','compose.control-browser-tests.yml')
    & $docker @compose config --quiet
    if($LASTEXITCODE -ne 0){throw 'Invalid isolated Compose'}
    & $docker @compose up -d --wait --pull never test-db broker
    if($LASTEXITCODE -ne 0){throw 'Disposable services did not start'}
    & $docker @compose up -d --no-deps --pull never test-runner
    if($LASTEXITCODE -ne 0){throw 'Owned HTTP harness did not start'}
    $ready=Join-Path $results 'ready.json'
    $deadline=(Get-Date).AddSeconds(75)
    while(-not (Test-Path -LiteralPath $ready)){
        if((Get-Date) -gt $deadline){throw 'Owned HTTP harness readiness timed out'}
        Start-Sleep -Milliseconds 250
    }
    $address=(& $docker @compose port test-runner 8000 | Out-String).Trim()
    if($LASTEXITCODE -ne 0 -or $address -notmatch '^127\.0\.0\.1:[1-9]\d*$'){throw ('Refusing a non-loopback or unpublished browser target: '+$address)}
    & $NodeExecutable (Join-Path $root 'scripts/check-live-control-browser.cjs') ('http://'+$address) $results $PlaywrightModules $ChromiumExecutable
    if($LASTEXITCODE -ne 0){throw 'Real browser control check failed'}
    $serverReport=Join-Path $results 'server.json'
    $deadline=(Get-Date).AddSeconds(25)
    while(-not (Test-Path -LiteralPath $serverReport)){
        if((Get-Date) -gt $deadline){throw 'Owned worker verification/stop timed out'}
        Start-Sleep -Milliseconds 250
    }
    $report=Get-Content -LiteralPath $serverReport -Raw | ConvertFrom-Json
    if($report.phase -ne 'browser_to_real_http_pg_mqtt_device_ack_passed' -or $report.source -ne $source){throw 'Wrong browser proof identity'}
    if((& git rev-parse HEAD).Trim() -ne $source -or (& git status --porcelain=v1 | Out-String) -ne $beforeStatus){throw 'Tracked checkout changed during browser proof'}
    $proofExit=0
    Write-Output 'PASS real Chromium UI -> actual HTTP -> PostgreSQL -> authenticated MQTT -> simulated device -> saved ACK -> UI.'
    Write-Output 'PASS exact same-ID replay, reload receipt, ordinary-user read/write refusal, single device execution and sampled temperature decrease.'
    Write-Output 'PASS real alert UI ACK, idempotent HTTP retry, synthetic recovery, late ACK 409 and append-only transition history. Not C-triggered recovery.'
    Write-Output 'PASS continuous MQTT -> actual A inbox -> C PG -> actual history/raw page and same-filter fixed-snapshot CSV; anonymous HTTP 401.'
    Write-Output 'NOT E1: actual A04/A05/C01 migrations and gated C storage only; C-triggered B anomaly/recovery, two real notification receipts and second-machine acceptance remain untested.'
} catch {
    Write-Output ('STOP: '+$_.Exception.Message)
    if($docker -and $compose){& $docker @compose logs --no-color --tail 45 test-runner}
} finally {
    if($docker -and $compose){
        & $docker @compose down --remove-orphans
        $cleanupExit=$LASTEXITCODE
        if($cleanupExit -ne 0){$proofExit=1}
    }
    if($results){
        $sidecar=Join-Path $results 'host-cleanup.json'
        $stream=[IO.File]::Open($sidecar,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
        try {
            $bytes=[Text.Encoding]::UTF8.GetBytes((@{proof_exit=$proofExit;cleanup_exit=$cleanupExit;project=$runId;source=$source;dirty_before=($beforeStatus.Trim().Length -gt 0);frontend_tree=$frontendTree;bundle_source=$BundleSource}|ConvertTo-Json))
            $stream.Write($bytes,0,$bytes.Length)
        } finally {$stream.Dispose()}
        Write-Output ('Evidence: '+$results)
    }
    foreach($name in $names){[Environment]::SetEnvironmentVariable($name,$saved[$name],'Process')}
    Set-Location -LiteralPath $priorLocation.Path
}
exit $proofExit
