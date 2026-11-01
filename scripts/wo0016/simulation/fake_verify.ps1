param([string]$Dir, [int]$rc = 0)
$start = Test-Path (Join-Path $Dir 'drop_start')
$end = Test-Path (Join-Path $Dir 'drop_end')
if ($start -and $end -and $rc -eq 0) {
    Write-Output '{"status":"COMPLETE","frame_count":450}'
    exit 0
}
Write-Output ("missing markers start=$start end=$end rc=$rc")
exit 1