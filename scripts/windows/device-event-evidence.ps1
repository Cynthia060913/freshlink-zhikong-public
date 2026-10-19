# Preserve the container-owned raw report. Only create a new host-owned sidecar.
function Write-DeviceEventSourceEvidence {
    param(
        [Parameter(Mandatory=$true)][string]$ReportPath,
        [Parameter(Mandatory=$true)][string]$Commit,
        [Parameter(Mandatory=$true)][bool]$Dirty
    )
    if($Commit -notmatch '^[0-9a-f]{40}$'){throw 'Invalid proof source commit'}
    $rawPath=(Resolve-Path -LiteralPath $ReportPath).Path
    if((Split-Path $rawPath -Leaf) -ne 'mqtt-event-worker.json'){throw 'Unexpected raw proof report'}
    if(([IO.File]::GetAttributes($rawPath) -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Refusing report link'}
    $raw=Get-Content -LiteralPath $rawPath -Raw|ConvertFrom-Json
    if($raw.phase -ne 'live_mqtt_continuous_event_worker_passed' -or $raw.b_new_events -ne 2){throw 'Wrong continuous proof phase'}
    $raw|Add-Member -NotePropertyName source_commit -NotePropertyValue $Commit
    $raw|Add-Member -NotePropertyName source_worktree_dirty -NotePropertyValue $Dirty
    $raw|Add-Member -NotePropertyName raw_report_sha256 -NotePropertyValue (Get-FileHash -LiteralPath $rawPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $target=Join-Path (Split-Path $rawPath -Parent) 'mqtt-event-worker-source.json'
    # CreateNew refuses an existing target. Never overwrite/chmod the root-owned
    # container file (0644 on Linux), or grant broad write permission to fix CI.
    $stream=[IO.File]::Open($target,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {
        $bytes=[Text.Encoding]::UTF8.GetBytes(($raw|ConvertTo-Json -Depth 10))
        $stream.Write($bytes,0,$bytes.Length)
    } finally {$stream.Dispose()}
    return $target
}
