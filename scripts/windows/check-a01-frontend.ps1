# Generate the SDK from the isolated test schema, then lint/build the frontend.
[CmdletBinding()]
param([string]$EvidenceDirectory)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')
$savedLocation = Get-Location
$reportPath = $null
$result = [ordered]@{ stage = 'A01 frontend SDK/lint/build'; status = 'checking'; image = 'oven/bun:1.3.12'; registry = 'https://registry.npmjs.org'; dependency_storage = 'disposable Linux volume'; api_base = 'same-origin: explicit empty VITE_API_URL'; api_origin = 'same-origin (VITE_API_URL empty)'; schema_sha256 = $null; bundle_directory = $null; error = $null }
try {
    $projectRoot = Get-FreshLinkRoot
    Set-Location -LiteralPath $projectRoot
    if (-not $EvidenceDirectory) {
        $candidates = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'work') -Directory |
            Where-Object { $_.Name -like 'a01-tests-*' -and (Test-Path -LiteralPath (Join-Path $_.FullName 'openapi.json')) } |
            Sort-Object LastWriteTime -Descending)
        if ($candidates.Count -eq 0) { throw 'No successful isolated test schema found; run check-a01-acceptance.ps1 first.' }
        $EvidenceDirectory = $candidates[0].FullName
    }
    $evidenceRoot = (Resolve-Path -LiteralPath $EvidenceDirectory).Path
    $workPrefix = (Join-Path $projectRoot 'work') + [IO.Path]::DirectorySeparatorChar
    if (-not $evidenceRoot.StartsWith($workPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Evidence must be inside this repository work directory.' }
    [xml]$testReport = Get-Content -LiteralPath (Join-Path $evidenceRoot 'pytest.xml') -Raw
    foreach ($suite in $testReport.testsuites.testsuite) {
        if ([int]$suite.failures -ne 0 -or [int]$suite.errors -ne 0 -or [int]$suite.tests -eq 0) { throw 'Selected test report did not pass.' }
    }
    $schemaPath = Join-Path $evidenceRoot 'openapi.json'
    $schema = Get-Content -LiteralPath $schemaPath -Raw | ConvertFrom-Json
    if ($schema.paths.PSObject.Properties.Name -notcontains '/api/v1/devices/') { throw 'Schema does not contain A01 devices.' }
    $result.schema_sha256 = (Get-FileHash -LiteralPath $schemaPath -Algorithm SHA256).Hash
    $reportPath = Join-Path $projectRoot ('work/a01-frontend-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID + '.json')
    $dockerExecutable = Assert-FreshLinkDocker
    Write-Output 'Using the successful isolated backend schema; no login or private environment file is mounted.'
    Write-Output 'Downloading the pinned official Bun image if needed; package installation may take several minutes.'
    $priorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $dockerExecutable image inspect $result.image --format '{{.Id}}' 2>$null
        $imageExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $priorPreference }
    if ($imageExit -ne 0) {
        & $dockerExecutable pull $result.image
        if ($LASTEXITCODE -ne 0) { throw 'Bun image download failed; do not disable TLS or delete app data.' }
    }
    # Copy a generated, non-secret schema artifact; it is ignored by Git.
    Copy-Item -LiteralPath $schemaPath -Destination (Join-Path $projectRoot 'frontend/openapi.json')
    # Preserve the actual build output, not only the successful compiler log.
    $bundleDirectory = Join-Path $projectRoot ('work/frontend-bundle-' + [guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($bundleDirectory) | Out-Null
    $result.bundle_directory = $bundleDirectory
    Write-Output 'Dependencies use a temporary Linux volume, official npm registry and 4 concurrent downloads.'
    Write-Output 'Lockfile and package integrity checks remain enabled. Existing Windows dependencies are left untouched.'
    $argsForDocker = @('run', '--rm', '--pull', 'never',
        '--env', 'VITE_API_URL=',
        '--mount', ('type=bind,source=' + (Join-Path $projectRoot 'frontend') + ',target=/workspace/frontend'),
        '--mount', ('type=bind,source=' + $bundleDirectory + ',target=/workspace/backend/app/frontend'),
        '--mount', 'type=volume,target=/workspace/frontend/node_modules',
        '--mount', ('type=bind,source=' + (Join-Path $projectRoot 'packages/react-email/package.json') + ',target=/workspace/packages/react-email/package.json,readonly'),
        '--mount', ('type=bind,source=' + (Join-Path $projectRoot 'package.json') + ',target=/workspace/package.json,readonly'),
        '--mount', ('type=bind,source=' + (Join-Path $projectRoot 'bun.lock') + ',target=/workspace/bun.lock,readonly'),
        '--mount', ('type=bind,source=' + (Join-Path $projectRoot 'scripts/normalize-generated-sdk.mjs') + ',target=/workspace/scripts/normalize-generated-sdk.mjs,readonly'),
        '--workdir', '/workspace', '--entrypoint', 'bash', $result.image, '-c',
        'set -e; bun ci --registry https://registry.npmjs.org --network-concurrency 4 --backend copyfile; cd frontend; bun run generate-client; export PATH="/workspace/frontend/node_modules/.bin:/workspace/node_modules/.bin:$PATH"; biome format --write openapi.json; biome check --no-errors-on-unmatched --files-ignore-unknown=true --max-diagnostics=100 ./; bun run build')
    & $dockerExecutable @argsForDocker
    if ($LASTEXITCODE -ne 0) { throw 'Frontend SDK, lint or build failed; keep the output for your Agent.' }
    $result.status = 'passed'
    if (-not (Test-Path -LiteralPath (Join-Path $bundleDirectory 'index.html'))) { throw 'Build reported success but frontend bundle was not preserved.' }
    Write-Output 'PASS A01 frontend SDK generation, Biome check and Vite/TypeScript build.'
    Write-Output 'NOT CHECKED: browser interaction, full pre-commit hooks, remote CI or B/C integration.'
    Write-Output 'Generated SDK source may have changed. Do not run git add .; let your Agent review the diff.'
    Write-Output ('Preserved frontend bundle: ' + $bundleDirectory)
} catch {
    $result.status = 'failed'
    $result.error = $_.Exception.Message
    Write-Output ('STOP: ' + $result.error)
} finally {
    if ($reportPath) {
        [IO.File]::WriteAllText($reportPath, ($result | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
        Write-Output ('Result: ' + $reportPath)
    }
    Set-Location -LiteralPath $savedLocation.Path
}
if ($result.status -ne 'passed') { exit 1 }
