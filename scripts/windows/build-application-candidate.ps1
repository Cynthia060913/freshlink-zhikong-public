# Build a pinned Git archive only. No deployment, database, save/push or cleanup.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$Revision)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$root=Get-FreshLinkRoot
$prior=Get-Location
$report=$null
$reportPath=$null
try {
    Set-Location -LiteralPath $root
    $head=(& git rev-parse HEAD).Trim()
    if($LASTEXITCODE -ne 0 -or $head -ne $Revision){throw 'Only the exact checked-out commit may be built'}
    if((& git status --porcelain=v1 | Out-String).Trim()){throw 'Save/review tracked and untracked changes before building; no automatic commit'}
    $docker=Assert-FreshLinkDocker
    $source=(& (Join-Path $PSScriptRoot 'export-source-candidate.ps1') -Revision $Revision -Repository $root) | ConvertFrom-Json
    $artifact=Split-Path -Parent $source.zip
    $context=Join-Path $artifact 'windows-extraction/FreshLink'
    $manifest=Join-Path $artifact 'manifest.json'
    $image='freshlink-application-candidate:'+$Revision.Substring(0,12)+'-'+[guid]::NewGuid().ToString('N').Substring(0,12)
    if($image -notmatch '^freshlink-application-candidate:[0-9a-f]{12}-[0-9a-f]{12}$'){throw 'Refusing an unexpected image tag'}
    $existing=(& $docker image ls --quiet --filter ('reference='+$image) | Out-String).Trim()
    if($LASTEXITCODE -ne 0 -or $existing){throw 'Image identity is unavailable or the generated tag already exists; no overwrite'}
    $reportPath=Join-Path $artifact 'application-build.json'
    $report=[ordered]@{status='building_not_deployed';source=$Revision;source_zip_sha256=$source.zip_sha256;image=$image;image_id=$null;image_source_audit=$null;dependency_policy='Bun 1.3.12 digest + bun ci; Python 3.14.8 + uv sync --frozen';error=$null;not_verified=@('startup','formal migrations','E1','second machine','notification receipts','full image security audit','final submission')}
    # The only context is the already extracted, byte-verified commit. Local
    # .env.freshlink, work, .git and application data are never in this context.
    & $docker build --progress plain --file (Join-Path $context 'backend/Dockerfile') --tag $image --build-arg ('FRESHLINK_SOURCE='+$Revision) $context
    if($LASTEXITCODE -ne 0){throw 'Candidate image build failed; no application was started'}
    $id=(& $docker image inspect $image --format '{{.Id}}').Trim()
    if($LASTEXITCODE -ne 0 -or $id -notmatch '^sha256:[0-9a-f]{64}$'){throw 'Image identity not verified'}
    $label=(& $docker image inspect $image --format '{{index .Config.Labels "org.opencontainers.image.revision"}}').Trim()
    if($LASTEXITCODE -ne 0 -or $label -ne $Revision){throw 'Image source label differs from the archived commit'}
    $report.image_id=$id
    $audit=& $docker run --rm --pull never --network none --mount ('type=bind,source='+$manifest+',target=/proof/source-manifest.json,readonly') --env ('FRESHLINK_EXPECTED_SOURCE='+$Revision) --entrypoint python $image -B scripts/check-image-source.py
    if($LASTEXITCODE -ne 0){throw 'Read-only final image source audit failed'}
    $report.image_source_audit=($audit -join "`n") | ConvertFrom-Json
    if($report.image_source_audit.status -ne 'verified_image_source_and_frontend_presence_not_E1'){throw 'Unexpected source audit result'}
    if((& git rev-parse HEAD).Trim() -ne $Revision -or (& git status --porcelain=v1 | Out-String).Trim()){throw 'Checkout changed while building; do not publish this result as a clean build'}
    $report.status='candidate_image_built_source_verified_not_application_acceptance'
    Write-Output 'PASS image built from the exact Git archive; backend source bytes and built frontend presence verified.'
    Write-Output 'NOT ACCEPTED: application startup, formal migrations, E1, second machine, real notifications or final submission.'
} catch {
    if($report){$report.status='failed_not_deployed';$report.error=$_.Exception.Message}
    throw
} finally {
    if($reportPath -and $report){
        $stream=[IO.File]::Open($reportPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
        try{$bytes=[Text.Encoding]::UTF8.GetBytes(($report|ConvertTo-Json -Depth 8));$stream.Write($bytes,0,$bytes.Length)}finally{$stream.Dispose()}
        Write-Output ('Evidence: '+$reportPath)
    }
    Set-Location -LiteralPath $prior.Path
}
