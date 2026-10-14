Set-StrictMode -Version Latest

function Find-FreshLinkDocker {
    $toolCommand = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -ne $toolCommand) { return $toolCommand.Source }
    $locations = @(
        (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'),
        (Join-Path $env:LOCALAPPDATA 'Docker\resources\bin\docker.exe')
    )
    foreach ($location in $locations) {
        if (Test-Path -LiteralPath $location) { return $location }
    }
    return $null
}

function Get-FreshLinkRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}

function Assert-FreshLinkDocker {
    $dockerExecutable = Find-FreshLinkDocker
    if (-not $dockerExecutable) {
        throw 'Docker Desktop CLI was not found. Install/start Docker Desktop, then retry. No system setting was changed.'
    }
    & $dockerExecutable compose version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose is unavailable.' }
    $engineType = & $dockerExecutable info --format '{{.OSType}}' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Docker engine is not ready. Open Docker Desktop and wait for it to start.' }
    if ($engineType -ne 'linux') { throw 'This project requires the Linux containers engine.' }
    return $dockerExecutable
}

function New-FreshLinkSecret {
    $secretBytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($secretBytes) } finally { $generator.Dispose() }
    return -join ($secretBytes | ForEach-Object { $_.ToString('x2') })
}
