param(
    [string]$Dir,
    [int]$Ack = 1,
    [int]$Duplicate = 0,
    [int]$ExitCode = 0,
    [int]$Baseline = 59
)
function Emit([string]$text) {
    [Console]::Out.WriteLine($text)
    [Console]::Out.Flush()
}
Emit "CAMERA_READY {'width': 640}"
Start-Sleep -Milliseconds 50
Emit 'RECORDING_STARTED frame=0 monotonic_ns=1000'
Emit "BASELINE_COMPLETE frame=$Baseline monotonic_ns=1000000000"
if ($Duplicate -ne 0) { Emit "BASELINE_COMPLETE frame=$Baseline monotonic_ns=1000000000" }
$start = Join-Path $Dir 'drop_start'
for ($i = 0; $i -lt 100; $i++) {
    if (Test-Path $start) { break }
    Start-Sleep -Milliseconds 50
}
if ($Ack -ne 0 -and (Test-Path $start)) {
    Emit 'DROP_START_OBSERVED frame=61 monotonic_ns=1300000000'
}
$end = Join-Path $Dir 'drop_end'
for ($i = 0; $i -lt 100; $i++) {
    if (Test-Path $end) { break }
    Start-Sleep -Milliseconds 50
}
if (Test-Path $end) { Emit 'DROP_END_OBSERVED frame=300 monotonic_ns=5000000000' }
Emit '{"status":"COMPLETE","frame_count":450}'
exit $ExitCode