param(
    [string]$WorkDir = 'C:/w/mc-jpeg-axis-hls-20261101',
    [string]$VivadoRoot = 'C:/AMDDesignTools/2026.1/Vivado',
    [ValidateRange(1,86400)][int]$TimeoutSeconds = 300,
    [ValidatePattern('^[A-Za-z0-9_]+$')][string]$SnapshotName = 'jpeg_axis_gray_trace',
    [switch]$DebugElaboration = $true,
    [Parameter(Mandatory=$true)][string]$RunLabel
)
$ErrorActionPreference = 'Stop'
$run = Join-Path (Join-Path $WorkDir 'runs') $RunLabel
New-Item -ItemType Directory -Path $run | Out-Null
$sim = Join-Path $WorkDir 'hls/sim/verilog'
if (-not (Test-Path (Join-Path $sim 'xsim.dir/jpeg_axis_gray/xsimk.exe'))) { throw 'No elaborated cosim snapshot to replay.' }
$vcdPath = (Join-Path $run 'axis-ports.vcd').Replace('\','/')
$tcl = (Join-Path $run 'capture_axis_ports.tcl').Replace('\','/')
$captureBody = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'capture_axis_ports.tcl')
Set-Content -LiteralPath $tcl -Value ("set ::jpeg_axis_vcd_path {$vcdPath}" + [Environment]::NewLine + $captureBody) -Encoding utf8
if ($DebugElaboration) {
    if($SnapshotName -eq 'jpeg_axis_gray'){throw 'Debug replay must use an independent snapshot name.'}
    $xelabLine = (Select-String -LiteralPath (Join-Path $sim 'run_xsim.bat') -Pattern '^call .*[/\\]xelab ').Line
    if(-not $xelabLine){throw 'Cannot locate the generated xelab command.'}
    $compileArgs = $xelabLine -replace '^call\s+\S+\s+', ''
    $compileArgs = $compileArgs -replace '-s\s+jpeg_axis_gray(?=\s|$)', "-s $SnapshotName"
    $compileArgs = ($compileArgs -replace '-debug\s+\w+', '') + ' -debug typical'
    Set-Content -LiteralPath (Join-Path $run 'xelab-arguments.txt') -Value $compileArgs -Encoding utf8
    $compiler = Start-Process -FilePath (Join-Path $VivadoRoot 'bin/xelab.bat') -ArgumentList $compileArgs -WorkingDirectory $sim -RedirectStandardOutput (Join-Path $run 'xelab.stdout.log') -RedirectStandardError (Join-Path $run 'xelab.stderr.log') -PassThru -WindowStyle Hidden
    $compiled = $compiler.WaitForExit($TimeoutSeconds * 1000)
    if(-not $compiled){$compiler.Kill($true);$compiler.WaitForExit();throw 'Diagnostic trace elaboration watchdog timeout.'}
    if($compiler.ExitCode -ne 0){throw "Diagnostic trace elaboration failed: $($compiler.ExitCode)"}
}
$argsLine = '-testplusarg "UVM_VERBOSITY=UVM_NONE" -testplusarg "UVM_TESTNAME=jpeg_axis_gray_test_lib" -testplusarg "UVM_TIMEOUT=20000000000000" --noieeewarnings {0} -tclbatch "{1}" -wdb "{2}"' -f $SnapshotName, $tcl, (Join-Path $run 'replay.wdb')
$proc = Start-Process -FilePath (Join-Path $VivadoRoot 'bin/xsim.bat') -ArgumentList $argsLine -WorkingDirectory $sim -RedirectStandardOutput (Join-Path $run 'stdout.log') -RedirectStandardError (Join-Path $run 'stderr.log') -PassThru -WindowStyle Hidden
$done = $proc.WaitForExit($TimeoutSeconds * 1000)
if (-not $done) { $proc.Kill($true); $proc.WaitForExit() }
$exitCode = if($done){$proc.ExitCode}else{124}
[ordered]@{run_label=$RunLabel;exit_code=$exitCode;watchdog_timeout=(-not $done);classification='diagnostic replay of generated RTL/VIP snapshot; not an HLS green receipt';random_stall_changed=$false;tcl_sha256=(Get-FileHash -LiteralPath $tcl -Algorithm SHA256).Hash.ToLowerInvariant()} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $run 'replay-result.json') -Encoding utf8
Write-Output "replay_evidence=$run exit_code=$exitCode"
if($exitCode -ne 0){throw "XSim replay failed or timed out: $exitCode"}
