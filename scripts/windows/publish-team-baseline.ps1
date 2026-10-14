# Default: local preflight only. -Publish explicitly stages/commits/pushes this branch.
[CmdletBinding()]
param([switch]$Publish)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$originalLocation = Get-Location
$originalOutputEncoding = $OutputEncoding
$utf8 = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = $utf8
$originalConsoleEncoding = $null
try { $originalConsoleEncoding = [Console]::OutputEncoding; [Console]::OutputEncoding = $utf8 } catch { }
$publishResult = [ordered]@{
    stage = 'FreshLink A01 team baseline branch publication'
    status = 'checking'
    publish_requested = [bool]$Publish
    branch = $null
    commit = $null
    remote_branch_verified = $false
    main_merged = $false
    checked_files = 0
    error = $null
    checked_at = [DateTime]::UtcNow.ToString('o')
}
$reportPath = $null
$gitExecutable = $null

function Invoke-TeamGit {
    param([string[]]$GitArgs)
    $priorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(& $gitExecutable -c core.quotepath=false @GitArgs 2>&1)
        $gitExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $priorPreference }
    if ($gitExit -ne 0) {
        foreach ($line in $lines) { Write-Host ([string]$line) }
        throw ('Git operation failed (exit ' + $gitExit + '): ' + ($GitArgs -join ' '))
    }
    return @($lines | ForEach-Object { [string]$_ })
}

function Assert-NoPrivateContent {
    param([string]$Text, [string]$RelativePath, [string[]]$PrivateValues)
    foreach ($privateValue in $PrivateValues) {
        if ($Text.Contains($privateValue)) { throw ('Private configuration value detected in ' + $RelativePath) }
    }
    $tokenPattern = 'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'
    if ([regex]::IsMatch($Text, $tokenPattern)) { throw ('Possible credential material detected in ' + $RelativePath) }
}

try {
    $projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
    Set-Location -LiteralPath $projectRoot
    $gitCommand = Get-Command git -ErrorAction Stop
    $gitExecutable = $gitCommand.Source
    $gitRoot = ((Invoke-TeamGit -GitArgs @('rev-parse', '--show-toplevel')) -join '').Trim()
    if ([IO.Path]::GetFullPath($gitRoot).TrimEnd('\', '/') -ne $projectRoot.TrimEnd('\', '/')) {
        throw 'This is not the FreshLink repository root.'
    }
    $manifestPath = Join-Path $PSScriptRoot 'team-baseline-files.json'
    $manifest = [IO.File]::ReadAllText($manifestPath, $utf8) | ConvertFrom-Json
    $branch = ((Invoke-TeamGit -GitArgs @('branch', '--show-current')) -join '').Trim()
    $origin = ((Invoke-TeamGit -GitArgs @('remote', 'get-url', 'origin')) -join '').Trim()
    if ($branch -ne $manifest.branch) { throw ('Expected branch ' + $manifest.branch + '; current ' + $branch) }
    if ($origin -ne $manifest.origin) { throw 'Origin is not the approved team repository.' }
    $publishResult.branch = $branch
    $publishResult.commit = ((Invoke-TeamGit -GitArgs @('rev-parse', 'HEAD')) -join '').Trim()
    $allowed = @($manifest.files)
    if (@($allowed | Sort-Object -Unique).Count -ne $allowed.Count) { throw 'Duplicate manifest paths.' }
    # Compare Git-normalized content, not Windows-only line-ending changes.
    # Untracked files still require explicit review; unrelated content still stops publication.
    $trackedChanges = @(Invoke-TeamGit -GitArgs @('-c', 'core.safecrlf=false', 'diff', '--name-only'))
    $untrackedChanges = @(Invoke-TeamGit -GitArgs @('ls-files', '-o', '--exclude-standard'))
    $changes = @(@($trackedChanges + $untrackedChanges) | Where-Object { $_ } | Sort-Object -Unique)
    $staged = @(Invoke-TeamGit -GitArgs @('diff', '--cached', '--name-only'))
    foreach ($relative in @($changes + $staged)) {
        if ($relative -and $allowed -notcontains $relative) { throw ('Unreviewed change is preserved; publication stopped: ' + $relative) }
    }
    $unmerged = @(Invoke-TeamGit -GitArgs @('ls-files', '-u'))
    if ($unmerged.Count -gt 0) { throw 'Unresolved merge conflicts; publication stopped.' }
    foreach ($privatePath in @('.env.freshlink', 'work/probe.json')) {
        $ignored = @(Invoke-TeamGit -GitArgs @('check-ignore', '--', $privatePath))
        if ($ignored.Count -eq 0) { throw 'Private-file ignore rules are missing.' }
    }
    $privateValues = @()
    $privateEnv = Join-Path $projectRoot '.env.freshlink'
    if (Test-Path -LiteralPath $privateEnv) {
        foreach ($line in [IO.File]::ReadAllLines($privateEnv, $utf8)) {
            if ($line -match '^(SECRET_KEY|FIRST_SUPERUSER_PASSWORD|POSTGRES_PASSWORD|SMTP_PASSWORD)\s*=(.*)$') {
                $value = $Matches[2].Trim().Trim([char]34).Trim([char]39)
                if ($value.Length -ge 12) { $privateValues += $value }
            }
        }
    }
    $rootPrefix = $projectRoot.TrimEnd('\') + '\'
    foreach ($relative in $allowed) {
        if ($relative -match '(^|/)(\.git|work|node_modules)(/|$)|(^|/)\.env|\.(db|sqlite|vhdx)$') {
            throw 'Forbidden private or generated path in manifest.'
        }
        $absolute = [IO.Path]::GetFullPath((Join-Path $projectRoot $relative))
        if (-not $absolute.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Manifest path leaves repository.' }
        if (-not (Test-Path -LiteralPath $absolute -PathType Leaf)) { throw ('Missing reviewed file: ' + $relative) }
        Assert-NoPrivateContent -Text ([IO.File]::ReadAllText($absolute, $utf8)) -RelativePath $relative -PrivateValues $privateValues
    }
    Invoke-TeamGit -GitArgs @('-c', 'core.safecrlf=false', 'diff', '--check') | Out-Null
    $publishResult.checked_files = $allowed.Count
    Write-Output ('PASS local preflight: ' + $allowed.Count + ' reviewed paths; private values were not printed.')
    Write-Output 'Preflight is not a new application, full security or contest acceptance test.'
    $workDirectory = Join-Path $projectRoot 'work'
    [IO.Directory]::CreateDirectory($workDirectory) | Out-Null
    $reportPath = Join-Path $workDirectory ('team-baseline-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + $PID + '.json')

    if (-not $Publish) {
        $publishResult.status = 'local_preflight_passed'
        Write-Output 'No git staging, commit, network access or push performed. Use -Publish in your own terminal to publish this branch.'
    } else {
        # The user's remote read succeeded with HTTP/1.1. Keep this command-scoped.
        # Fetch before staging. Stop instead of rewriting a newer shared main.
        Invoke-TeamGit -GitArgs @('-c', 'http.sslBackend=schannel', '-c', 'http.version=HTTP/1.1', 'fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main') | Out-Null
        Invoke-TeamGit -GitArgs @('merge-base', '--is-ancestor', 'origin/main', 'HEAD') | Out-Null
        $authorName = ((Invoke-TeamGit -GitArgs @('config', 'user.name')) -join '').Trim()
        $authorEmail = ((Invoke-TeamGit -GitArgs @('config', 'user.email')) -join '').Trim()
        if (-not $authorName -or -not $authorEmail) { throw 'Choose your Git commit identity before publishing; identity was not changed.' }
        $toStage = @($changes | Where-Object { $_ })
        if ($toStage.Count -gt 0) { Invoke-TeamGit -GitArgs (@('add', '--') + $toStage) | Out-Null }
        $stagedNow = @(Invoke-TeamGit -GitArgs @('diff', '--cached', '--name-only'))
        foreach ($relative in $stagedNow) {
            if ($allowed -notcontains $relative) { throw 'Unexpected staged file; not committed.' }
            $stagedText = (Invoke-TeamGit -GitArgs @('show', (':' + $relative))) -join "`n"
            Assert-NoPrivateContent -Text $stagedText -RelativePath $relative -PrivateValues $privateValues
        }
        Invoke-TeamGit -GitArgs @('diff', '--cached', '--check') | Out-Null
        if ($stagedNow.Count -gt 0) {
            Invoke-TeamGit -GitArgs @('commit', '-m', 'fix: validate A01 backend and frontend and repair CI') | Out-Null
        }
        $publishResult.commit = ((Invoke-TeamGit -GitArgs @('rev-parse', 'HEAD')) -join '').Trim()
        Invoke-TeamGit -GitArgs @('-c', 'http.sslBackend=schannel', '-c', 'http.version=HTTP/1.1', 'push', '-u', 'origin', $branch) | Out-Null
        $remoteLine = ((Invoke-TeamGit -GitArgs @('-c', 'http.sslBackend=schannel', '-c', 'http.version=HTTP/1.1', 'ls-remote', '--heads', 'origin', ('refs/heads/' + $branch))) -join '').Trim()
        if (-not $remoteLine -or ($remoteLine -split '\s+')[0] -ne $publishResult.commit) { throw 'Push could not be verified against the remote branch.' }
        $publishResult.remote_branch_verified = $true
        $publishResult.status = 'branch_published_verified'
        Write-Output ('PASS remote branch verified: ' + $branch)
        Write-Output ('Commit: ' + $publishResult.commit)
        Write-Output 'Main was NOT merged. Existing PR #4 for this branch will update; review the new CI before merging.'
    }
} catch {
    $publishResult.status = 'failed'
    $publishResult.error = $_.Exception.Message
    Write-Output ('STOP: ' + $publishResult.error)
    Write-Output 'No reset, force push, file deletion or database change was attempted. Keep the error for your Agent.'
} finally {
    if ($reportPath) {
        [IO.File]::WriteAllText($reportPath, ($publishResult | ConvertTo-Json -Depth 4), $utf8)
        Write-Output ('Result: ' + $reportPath)
    }
    Set-Location -LiteralPath $originalLocation.Path
    $OutputEncoding = $originalOutputEncoding
    if ($null -ne $originalConsoleEncoding) { try { [Console]::OutputEncoding = $originalConsoleEncoding } catch { } }
}
if ($publishResult.status -eq 'failed') { exit 1 }
