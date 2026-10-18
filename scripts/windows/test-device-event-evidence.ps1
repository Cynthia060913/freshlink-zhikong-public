# Pure filesystem regression; no Docker, Python, notifications or existing data.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'device-event-evidence.ps1')
$root=Join-Path (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path ('work/event-evidence-test-'+(Get-Date -Format yyyyMMddHHmmss)+'-'+$PID)
[IO.Directory]::CreateDirectory($root)|Out-Null
$report=Join-Path $root 'mqtt-event-worker.json'
[IO.File]::WriteAllText($report,'{"phase":"live_mqtt_continuous_event_worker_passed","b_new_events":2,"fixture":"unchanged"}')
[IO.File]::SetAttributes($report,[IO.FileAttributes]::ReadOnly)
$before=(Get-FileHash -LiteralPath $report -Algorithm SHA256).Hash
$sha='1234567890123456789012345678901234567890'
$path=Write-DeviceEventSourceEvidence -ReportPath $report -Commit $sha -Dirty $false
if((Get-FileHash -LiteralPath $report -Algorithm SHA256).Hash -ne $before){throw 'Raw report was rewritten'}
if(([IO.File]::GetAttributes($report) -band [IO.FileAttributes]::ReadOnly) -eq 0){throw 'Raw report permission changed'}
$saved=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json
if($saved.source_commit -ne $sha -or $saved.source_worktree_dirty -or $saved.raw_report_sha256 -ne $before.ToLowerInvariant()){throw 'Provenance mismatch'}
$duplicateRejected=$false
try{Write-DeviceEventSourceEvidence -ReportPath $report -Commit $sha -Dirty $false|Out-Null}catch{$duplicateRejected=$true}
if(-not $duplicateRejected){throw 'Existing sidecar was overwritten'}
$invalidRejected=$false
try{Write-DeviceEventSourceEvidence -ReportPath $report -Commit 'main' -Dirty $false|Out-Null}catch{$invalidRejected=$true}
if(-not $invalidRejected){throw 'Mutable source accepted'}
Write-Output 'PASS read-only raw report unchanged, permissions unchanged, exact provenance/hash, existing sidecar and mutable revision refused (5 checks).'
Write-Output 'Synthetic fixtures retained under work; no existing files deleted.'
