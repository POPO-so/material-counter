# One desktop visibility test for the pre-warmed drop cue (no camera, no board).
$ErrorActionPreference = 'Stop'
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
$house = Split-Path -Parent $PSScriptRoot
$cue = Join-Path $house 'show_drop_cue.ps1'
$signal = Join-Path $env:TEMP ('m8-cue-test-' + [guid]::NewGuid().ToString('N') + '.flag')
$p = Start-Process -FilePath 'pwsh' -ArgumentList @(
    '-NoProfile', '-WindowStyle', 'Hidden', '-File', $cue,
    '-DropSeconds', '4', '-CloseDelay', '2',
    '-SignalPath', $signal, '-WaitSeconds', '30') -PassThru
Start-Sleep -Seconds 2
"VISIBLE_BEFORE_SIGNAL=$([WinProbe]::HasTitledWindow([uint32]$p.Id, '投料提示'))"
$signalAt = Get-Date
New-Item -ItemType File -Path $signal -Force | Out-Null
$visibleAt = $null
for ($i = 0; $i -lt 80; $i++) {
    if ([WinProbe]::HasTitledWindow([uint32]$p.Id, '投料提示')) { $visibleAt = (Get-Date); break }
    Start-Sleep -Milliseconds 50
}
"VISIBLE_AFTER_SIGNAL=$($null -ne $visibleAt)"
if ($visibleAt) { "SIGNAL_TO_VISIBLE_MS=$([int]($visibleAt - $signalAt).TotalMilliseconds)" }
Start-Sleep -Seconds 2
"VISIBLE_MID_DROP=$([WinProbe]::HasTitledWindow([uint32]$p.Id, '投料提示'))"
$p.WaitForExit()
"PROCESS_EXITED=$($p.HasExited) EXIT_CODE=$($p.ExitCode)"
"TOTAL_FROM_SIGNAL_MS=$([int]((Get-Date) - $signalAt).TotalMilliseconds)"
Remove-Item $signal -Force -ErrorAction SilentlyContinue