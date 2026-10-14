$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$dockerTool = Assert-FreshLinkDocker
$projectRoot = Get-FreshLinkRoot
$privateEnvironment = Join-Path $projectRoot '.env.freshlink'

# Generate per-machine development secrets once. Do not echo or overwrite them.
if (-not (Test-Path -LiteralPath $privateEnvironment)) {
    $environmentLines = @(
        '# Local-only secrets; do not share or commit.',
        ('POSTGRES_PASSWORD=' + (New-FreshLinkSecret)),
        ('SECRET_KEY=' + (New-FreshLinkSecret)),
        ('FIRST_SUPERUSER_PASSWORD=' + (New-FreshLinkSecret))
    )
    [System.IO.File]::WriteAllLines($privateEnvironment, $environmentLines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Output 'Created private .env.freshlink. Passwords were not printed.'
}

Push-Location $projectRoot
try {
    & $dockerTool compose --env-file .env.freshlink -f compose.freshlink.yml config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose validation failed.' }
    & $dockerTool compose --env-file .env.freshlink -f compose.freshlink.yml up -d --build --wait --wait-timeout 180
    if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Ask your Agent to inspect Compose logs. Existing data was not deleted.' }
    Write-Output 'Containers started. Run scripts/windows/smoke-test.ps1 before declaring P0 passed.'
    Write-Output 'Local URL: http://localhost:18000 ; account: admin@example.com'
    Write-Output 'Read your password locally from .env.freshlink. Do not send that file to teammates.'
} finally { Pop-Location }
