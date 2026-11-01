# Host-side controller for the M8 camera-only capture.
#
# Runs the safety gates, starts the capture, creates drop_start after
# BASELINE_COMPLETE, waits for DROP_START_OBSERVED, shows a desktop cue, then
# creates drop_end after a fixed window (recorded as ESTIMATED, not confirmed).
#
# Simulation mode replaces the board commands with -CaptureExe/-TouchExe/
# -VerifyExe so the whole flow can be tested without a camera or board.
param(
    [string]$BoardHost = 'xilinx@192.168.2.99',
    [string]$SessionDir,
    [int]$Frames = 450,
    [int]$Baseline = 60,
    [int]$Tail = 90,
    [int]$StartDelay = 12,
    [double]$DropWindowSeconds = 4.0,
    [ValidateSet('Window', 'LogOnly', 'Fail')][string]$CueMode = 'Window',
    [switch]$Simulation,
    [string]$CaptureExe,
    [string[]]$CaptureArgs = @(),
    [string]$TouchExe,
    [string[]]$TouchArgs = @(),
    [string]$VerifyExe,
    [string[]]$VerifyArgs = @(),
    [string]$EventsPath
)

$ErrorActionPreference = 'Stop'
$script:events = [System.Collections.Generic.List[object]]::new()
$script:scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:cueProcess = $null
$script:cueSignal = $null

function Add-Event([string]$name, $data = $null) {
    $entry = [ordered]@{ at = (Get-Date).ToUniversalTime().ToString('o'); event = $name }
    if ($null -ne $data) { $entry.data = $data }
    $script:events.Add($entry) | Out-Null
    Write-Host ("[{0}] {1}" -f $entry.at, $name)
}

if (-not ('WinProbe' -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class WinProbe {
    public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int max);
    public static bool HasTitledWindow(uint target, string title) {
        bool found = false;
        EnumWindows((h, l) => {
            uint pid;
            GetWindowThreadProcessId(h, out pid);
            if (pid == target && IsWindowVisible(h)) {
                var text = new System.Text.StringBuilder(256);
                GetWindowText(h, text, 256);
                if (text.ToString().Contains(title)) { found = true; return false; }
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
"@
}

function Test-CueVisible([int]$cueProcessId, [int]$timeoutMs = 3000) {
    $deadline = (Get-Date).AddMilliseconds($timeoutMs)
    while ((Get-Date) -lt $deadline) {
        if ([WinProbe]::HasTitledWindow([uint32]$cueProcessId, '投料提示')) { return $true }
        Start-Sleep -Milliseconds 50
    }
    return $false
}

function Invoke-Process([string]$exe, [string[]]$arguments) {
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $exe
    foreach ($arg in $arguments) { $psi.ArgumentList.Add($arg) }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $proc = [System.Diagnostics.Process]::Start($psi)
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    return [pscustomobject]@{ rc = $proc.ExitCode; stdout = $stdout; stderr = $stderr }
}

function Assert-Gates {
    if ($Simulation) { Add-Event 'gates_skipped_simulation'; return }
    $checks = @(
        @{ name = 'service'; cmd = "systemctl is-active material-counter-integrated.service" },
        @{ name = 'enabled'; cmd = "systemctl is-enabled material-counter-integrated.service" },
        @{ name = 'processes'; cmd = "pgrep -af '[i]ntegrated_run|[b]oot_integrated|[r]un_counter|[c]apture_touching' || true" },
        @{ name = 'video_holders'; cmd = "sudo -n fuser -v /dev/video0 2>&1 || true" },
        @{ name = 'memavailable'; cmd = "awk '/MemAvailable/{print `$2}' /proc/meminfo" },
        @{ name = 'disk_free_kib'; cmd = "df -k /home | tail -1 | awk '{print `$4}'" },
        @{ name = 'kernel'; cmd = "dmesg 2>/dev/null | grep -Ei 'oom-kill|out of memory|dma.*(abort|error)|EXT4-fs error' || true" },
        @{ name = 'sudo'; cmd = "sudo -n true && echo SUDO_OK" }
    )
    foreach ($check in $checks) {
        $result = Invoke-Process 'ssh' @('-o', 'BatchMode=yes', $BoardHost, $check.cmd)
        $value = $result.stdout.Trim()
        Add-Event ('gate_' + $check.name) @{ rc = $result.rc; value = $value }
        switch ($check.name) {
            'service' { if ($value -ne 'inactive') { throw "gate failed: service=$value" } }
            'enabled' { if ($value -ne 'disabled') { throw "gate failed: enabled=$value" } }
            'processes' { if ($value) { throw "gate failed: business process present" } }
            'video_holders' { if ($value) { throw "gate failed: /dev/video0 busy" } }
            'memavailable' { if ([int]$value -lt 358400) { throw "gate failed: MemAvailable=$value" } }
            'disk_free_kib' { if ([int]$value -lt 1048576) { throw "gate failed: disk=$value" } }
            'kernel' { if ($value) { throw "gate failed: kernel findings" } }
            'sudo' { if ($value -notmatch 'SUDO_OK') { throw "gate failed: sudo" } }
        }
    }
    $exists = Invoke-Process 'ssh' @('-o', 'BatchMode=yes', $BoardHost,
        "test -e '$SessionDir' && echo EXISTS || echo ABSENT")
    if ($exists.stdout.Trim() -ne 'ABSENT') { throw "gate failed: session dir exists" }
    Add-Event 'gates_passed'
}

function Send-Marker([string]$marker) {
    if ($TouchExe) {
        $arguments = @($TouchArgs | ForEach-Object { $_ -replace '\{marker\}', $marker })
        return Invoke-Process $TouchExe $arguments
    }
    $remote = "cat /proc/uptime; sudo -n touch '$SessionDir/events/$marker'"
    return Invoke-Process 'ssh' @('-o', 'BatchMode=yes', $BoardHost, $remote)
}

function Show-Cue {
    $text = "现在开始投放 10 颗 M8"
    if ($CueMode -eq 'Fail') {
        Add-Event 'cue_failed' @{ reason = 'simulated' }
        return $false
    }
    if ($CueMode -eq 'LogOnly') {
        Add-Event 'cue_issued' @{ text = $text; mode = 'LogOnly'; visible = $false }
        return $true
    }
    if (-not $script:cueProcess) {
        Add-Event 'cue_failed' @{ reason = 'prewarm_missing' }
        return $false
    }
    New-Item -ItemType File -Path $script:cueSignal -Force | Out-Null
    $visible = Test-CueVisible $script:cueProcess.Id 1500
    if (-not $visible) {
        Add-Event 'cue_failed' @{ reason = 'window_not_visible'; pid = $script:cueProcess.Id }
        try { $script:cueProcess.Kill() } catch { }
        return $false
    }
    Add-Event 'cue_issued' @{ text = $text; mode = 'Window'; visible = $true;
        pid = $script:cueProcess.Id; drop_seconds = $DropWindowSeconds }
    return $true
}

function Start-Capture {
    if ($CaptureExe) {
        $psi = [System.Diagnostics.ProcessStartInfo]::new()
        $psi.FileName = $CaptureExe
        foreach ($arg in $CaptureArgs) { $psi.ArgumentList.Add($arg) }
    } else {
        if (-not $SessionDir) { throw 'SessionDir is required' }
        $remote = "sudo -n systemd-run --scope -p MemoryMax=512M -- " +
            "nice -n 19 timeout 180 /usr/local/share/pynq-venv/bin/python3 -B " +
            "/home/xilinx/capture-tools/capture_touching_camera_only.py " +
            "--output-dir '$SessionDir' --frames $Frames --baseline-frames $Baseline " +
            "--tail-frames $Tail --start-delay-s $StartDelay --format ram --diagnostics"
        $psi = [System.Diagnostics.ProcessStartInfo]::new()
        $psi.FileName = 'ssh'
        foreach ($arg in @('-o', 'BatchMode=yes', $BoardHost, $remote)) { $psi.ArgumentList.Add($arg) }
    }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $proc = [System.Diagnostics.Process]::Start($psi)
    $stderrTask = $proc.StandardError.ReadToEndAsync()
    Add-Event 'capture_started' @{ pid = $proc.Id }
    if ($CueMode -eq 'Window') {
        $script:cueSignal = Join-Path ([System.IO.Path]::GetTempPath()) (
            'm8-cue-' + [guid]::NewGuid().ToString('N') + '.flag')
        $cueScript = Join-Path $script:scriptRoot 'show_drop_cue.ps1'
        $script:cueProcess = Start-Process -FilePath 'pwsh' -ArgumentList @(
            '-NoProfile', '-WindowStyle', 'Hidden', '-File', $cueScript,
            '-Text', '现在开始投放 10 颗 M8', '-DropSeconds', [string]$DropWindowSeconds,
            '-CloseDelay', '3', '-SignalPath', $script:cueSignal,
            '-WaitSeconds', '90') -PassThru
        Add-Event 'cue_prewarmed' @{ pid = $script:cueProcess.Id }
    }
    return [pscustomobject]@{ proc = $proc; stderr = $stderrTask }
}

function Monitor-Capture($capture) {
    $proc = $capture.proc
    $baselineSeen = $false
    $dropStartTouched = $false
    $dropStartAcked = $false
    $cueAt = $null
    $dropEndTouched = $false
    $dropEndObserved = $false
    $cueFailed = $false
    $readTask = $proc.StandardOutput.ReadLineAsync()
    while ($true) {
        if ($readTask.Wait(50)) {
            $line = $readTask.Result
            if ($null -eq $line) { break }
            Write-Host $line
            if ($line -match 'BASELINE_COMPLETE frame=(\d+) monotonic_ns=(\d+)') {
                if (-not $baselineSeen) {
                    $baselineSeen = $true
                    Add-Event 'baseline_complete' @{ frame = [int]$Matches[1]; monotonic_ns = [long]$Matches[2] }
                    $result = Send-Marker 'drop_start'
                    if ($result.rc -ne 0) {
                        Add-Event 'drop_start_touch_failed' @{ rc = $result.rc; output = $result.stdout }
                        throw 'drop_start touch failed; operator cue withheld'
                    }
                    $dropStartTouched = $true
                    Add-Event 'drop_start_touched' @{ board_uptime = ($result.stdout -split "`n")[0] }
                }
            } elseif ($line -match 'DROP_START_OBSERVED frame=(\d+) monotonic_ns=(\d+)') {
                if ($dropStartTouched -and -not $dropStartAcked) {
                    $dropStartAcked = $true
                    Add-Event 'drop_start_observed' @{ frame = [int]$Matches[1]; monotonic_ns = [long]$Matches[2] }
                    $cueOk = Show-Cue
                    if ($cueOk) {
                        $cueAt = Get-Date
                    } else {
                        $cueFailed = $true
                        $stopResult = Send-Marker 'stop'
                        Add-Event 'cue_failed_abort' @{
                            stop_rc = $stopResult.rc
                            board_uptime = ($stopResult.stdout -split "`n")[0]
                        }
                    }
                }
            } elseif ($line -match 'DROP_END_OBSERVED frame=(\d+) monotonic_ns=(\d+)') {
                $dropEndObserved = $true
                Add-Event 'drop_end_observed' @{ frame = [int]$Matches[1]; monotonic_ns = [long]$Matches[2] }
            }
            $readTask = $proc.StandardOutput.ReadLineAsync()
        }
        # The drop_end timer must run even when the capture stream is quiet.
        if ($cueAt -and -not $dropEndTouched -and
            ((Get-Date) - $cueAt).TotalSeconds -ge $DropWindowSeconds) {
            $result = Send-Marker 'drop_end'
            $dropEndTouched = $true
            Add-Event 'drop_window_ended' @{ window_seconds = $DropWindowSeconds }
            Add-Event 'drop_end_touched' @{
                rc = $result.rc
                board_uptime = ($result.stdout -split "`n")[0]
                source = 'ESTIMATED_FIXED_WINDOW'
                window_seconds = $DropWindowSeconds
            }
        }
    }
    $proc.WaitForExit()
    $stderr = $capture.stderr.Result
    Add-Event 'capture_exited' @{ rc = $proc.ExitCode; stderr = $stderr.Trim() }
    if (-not $dropStartAcked) { Add-Event 'drop_start_not_acknowledged' }
    if (-not $dropEndTouched) { Add-Event 'drop_end_not_sent' }
    if (-not $dropEndObserved) { Add-Event 'drop_end_not_observed' }
    return [pscustomobject]@{ rc = $proc.ExitCode; cueFailed = $cueFailed }
}

function Verify-Session([int]$rc) {
    if ($VerifyExe) {
        $arguments = @($VerifyArgs | ForEach-Object {
            $_ -replace '\{session\}', $SessionDir -replace '\{rc\}', $rc })
        $result = Invoke-Process $VerifyExe $arguments
        Add-Event 'verify' @{ rc = $result.rc; output = $result.stdout.Trim() }
        return $result.rc
    }
    $remote = "sudo -n cat '$SessionDir/manifest.json'"
    $result = Invoke-Process 'ssh' @('-o', 'BatchMode=yes', $BoardHost, $remote)
    if ($result.rc -ne 0) { Add-Event 'verify_failed' @{ rc = $result.rc; stderr = $result.stderr }; return 1 }
    try { $manifest = $result.stdout | ConvertFrom-Json } catch { Add-Event 'verify_manifest_parse_failed'; return 1 }
    Add-Event 'verify' @{ status = $manifest.status; frame_count = $manifest.frame_count;
        timing = $manifest.timing; events = $manifest.events;
        frames_after_drop_end = $manifest.frames_after_drop_end }
    if ($manifest.status -ne 'COMPLETE' -or $manifest.frame_count -ne $Frames) { return 1 }
    return 0
}

try {
    if ($Simulation -and -not $CaptureExe) { throw 'simulation needs -CaptureExe' }
    Add-Event 'controller_started' @{ simulation = [bool]$Simulation; session = $SessionDir }
    Assert-Gates
    $capture = Start-Capture
    $monitor = Monitor-Capture $capture
    $rc = $monitor.rc
    if ($monitor.cueFailed) {
        Add-Event 'session_invalid_cue_failed'
        $verifyRc = 1
        $exit = 2
    } else {
        $verifyRc = Verify-Session $rc
        $exit = if ($rc -ne 0 -or $verifyRc -ne 0) { 1 } else { 0 }
    }
    Add-Event 'controller_finished' @{ capture_rc = $rc; verify_rc = $verifyRc; exit = $exit }
} catch {
    Add-Event 'controller_failed' @{ error = $_.Exception.Message }
    $exit = 2
} finally {
    if ($script:cueProcess -and -not $script:cueProcess.HasExited) {
        try { $script:cueProcess.Kill() } catch { }
    }
    if ($script:cueSignal -and (Test-Path $script:cueSignal)) {
        Remove-Item $script:cueSignal -Force -ErrorAction SilentlyContinue
    }
    Add-Event 'controller_exit' @{ code = $exit }
    $path = if ($EventsPath) { $EventsPath } else { Join-Path (Get-Location) 'controller-events.json' }
    $script:events | ConvertTo-Json -Depth 8 | Set-Content -Path $path -Encoding utf8
    Write-Host "EVENTS $path"
}
exit $exit