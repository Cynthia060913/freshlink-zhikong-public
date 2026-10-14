$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$projectRoot = Get-FreshLinkRoot
$runDirectory = Join-Path $projectRoot 'work'
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
$attemptName = ('p0-start-{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID)
$logPath = Join-Path $runDirectory ($attemptName + '.log')
$resultPath = Join-Path $runDirectory ($attemptName + '.json')
$attemptResult = [ordered]@{
    stage = 'P0 template startup'
    status = 'running'
    started_at = [DateTimeOffset]::UtcNow.ToString('o')
    finished_at = $null
    completed_checks = @()
    browser_verified = $false
    cold_chain_verified = $false
    error = $null
    log_file = $logPath
}
$transcriptStarted = $false
$attemptExitCode = 0

try {
    Start-Transcript -LiteralPath $logPath -NoClobber | Out-Null
    $transcriptStarted = $true
    Write-Output 'FreshLink P0: build/start the template, then check the local API and login.'
    Write-Output 'The first build downloads images and dependencies. Keep this window open.'
    & (Join-Path $PSScriptRoot 'start-local.ps1')
    $attemptResult.completed_checks += 'Compose startup and container health'
    & (Join-Path $PSScriptRoot 'smoke-test.ps1')
    $attemptResult.completed_checks += 'API health, frontend HTML, login and authenticated user'
    $attemptResult.status = 'passed'
    Write-Output 'P0 startup and API checks passed. Open http://localhost:18000 for the browser check.'
    Write-Output 'Local account: admin@example.com. Read the password from .env.freshlink on this computer only.'
} catch {
    $attemptResult.status = 'failed'
    $attemptResult.error = $_.Exception.Message
    $attemptExitCode = 1
    Write-Output ('P0 stopped: ' + $attemptResult.error)
    Write-Output 'Keep the log for your Agent. Do not delete the database to retry.'
} finally {
    $attemptResult.finished_at = [DateTimeOffset]::UtcNow.ToString('o')
    [System.IO.File]::WriteAllText($resultPath, ($attemptResult | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding($false)))
    Write-Output ('Result file: ' + $resultPath)
    Write-Output ('Log file: ' + $logPath)
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
}
exit $attemptExitCode
