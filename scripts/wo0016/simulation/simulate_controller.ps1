$ErrorActionPreference = 'Stop'
$root = 'C:\fpga\wt\m8-first-capture-protocol-20261102'
$ctl = Join-Path $root 'scripts\wo0016\operator_capture_controller.ps1'
$fakeCap = Join-Path $root 'scripts\wo0016\simulation\fake_capture.ps1'
$fakeTouch = Join-Path $root 'scripts\wo0016\simulation\fake_touch.ps1'
$fakeVerify = Join-Path $root 'scripts\wo0016\simulation\fake_verify.ps1'
$script:pass = 0
$script:fail = 0
function Check([string]$name, [bool]$ok) {
    if ($ok) { Write-Host "PASS $name"; $script:pass++ } else { Write-Host "FAIL $name"; $script:fail++ }
}
function New-Scenario([string]$name, [scriptblock]$mutate) {
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ('sim-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $dir | Out-Null
    $session = Join-Path $dir 'session'
    $events = Join-Path $dir 'events.json'
    $params = @{
        Simulation = $true
        SessionDir = $session
        CueMode = 'LogOnly'
        DropWindowSeconds = 0.6
        CaptureExe = 'pwsh'
        CaptureArgs = @('-NoProfile', '-File', $fakeCap, '-Dir', $dir)
        TouchExe = 'pwsh'
        TouchArgs = @('-NoProfile', '-File', $fakeTouch, '-Dir', $dir, '-Name', '{marker}')
        VerifyExe = 'pwsh'
        VerifyArgs = @('-NoProfile', '-File', $fakeVerify, '-Dir', $dir, '-rc', '{rc}')
        EventsPath = $events
    }
    if ($mutate) { & $mutate $dir $params }
    & $ctl @params | Out-Null
    $rc = $LASTEXITCODE
    $data = Get-Content $events -Raw | ConvertFrom-Json
    return @{ rc = $rc; events = $data; dir = $dir }
}
function Count-Event($events, [string]$name) {
    return @($events | Where-Object { $_.event -eq $name }).Count
}

$r = New-Scenario 'normal' $null
Check 'normal rc=0' ($r.rc -eq 0)
Check 'normal cue once' ((Count-Event $r.events 'cue_issued') -eq 1)
Check 'normal drop_start touched once' ((Count-Event $r.events 'drop_start_touched') -eq 1)
Check 'normal drop_end touched once' ((Count-Event $r.events 'drop_end_touched') -eq 1)
$dropEnd = @($r.events | Where-Object { $_.event -eq 'drop_end_touched' })[0]
Check 'drop_end source estimated' ($dropEnd.data.source -eq 'ESTIMATED_FIXED_WINDOW')
Check 'drop_end observed' ((Count-Event $r.events 'drop_end_observed') -eq 1)

$r = New-Scenario 'touch-fail' {
    param($dir, $params)
    $params.TouchArgs = @('-NoProfile', '-File', $fakeTouch, '-Dir', $dir, '-Name', '{marker}', '-Fail')
}
Check 'touch-fail rc=2' ($r.rc -eq 2)
Check 'touch-fail no cue' ((Count-Event $r.events 'cue_issued') -eq 0)
Check 'touch-fail event' ((Count-Event $r.events 'drop_start_touch_failed') -eq 1)
Check 'touch-fail no drop_end' ((Count-Event $r.events 'drop_end_touched') -eq 0)

$r = New-Scenario 'no-ack' {
    param($dir, $params)
    $params.CaptureArgs = @('-NoProfile', '-File', $fakeCap, '-Dir', $dir, '-Ack', '0')
}
Check 'no-ack rc!=0' ($r.rc -ne 0)
Check 'no-ack no cue' ((Count-Event $r.events 'cue_issued') -eq 0)
Check 'no-ack not acknowledged' ((Count-Event $r.events 'drop_start_not_acknowledged') -eq 1)
Check 'no-ack no drop_end' ((Count-Event $r.events 'drop_end_touched') -eq 0)

$r = New-Scenario 'duplicate' {
    param($dir, $params)
    $params.CaptureArgs = @('-NoProfile', '-File', $fakeCap, '-Dir', $dir, '-Duplicate', '1')
}
Check 'duplicate rc=0' ($r.rc -eq 0)
Check 'duplicate touch once' ((Count-Event $r.events 'drop_start_touched') -eq 1)
Check 'duplicate cue once' ((Count-Event $r.events 'cue_issued') -eq 1)

$r = New-Scenario 'exit3' {
    param($dir, $params)
    $params.CaptureArgs = @('-NoProfile', '-File', $fakeCap, '-Dir', $dir, '-ExitCode', '3')
}
Check 'exit3 controller rc=1' ($r.rc -eq 1)
$exited = @($r.events | Where-Object { $_.event -eq 'capture_exited' })[0]
Check 'exit3 preserved' ($exited.data.rc -eq 3)

Write-Host "RESULT pass=$script:pass fail=$script:fail"
if ($script:fail -ne 0) { exit 1 }
