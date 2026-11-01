param(
    [ValidateSet('csim','csynth','cosim')][string]$Flow = 'csim',
    [ValidateSet('smoke','faults','entropy-cut','align','batch')][string]$Mode = 'smoke',
    [ValidateSet('vendorY','opencv')][string]$ReferenceKind = 'vendorY',
    [ValidateSet('none','port','all')][string]$CosimTrace = 'none',
    [Alias('Input')][Parameter(Mandatory=$true)][string]$JpegInput,
    [Parameter(Mandatory=$true)][string]$Reference,
    [string]$WorkDir = 'C:/w/mc-jpeg-axis-hls-20261101',
    [string]$ToolRoot = 'C:/AMDDesignTools/2026.1/Vitis',
    [string]$PythonExe = 'C:/Users/14173/Documents/material-counter/.venv/Scripts/python.exe',
    [switch]$PrepareBatchReferences,
    [uint32]$FrameId = 1,
    [uint32]$EndFrameId = 1,
    [ValidateRange(1,86400)][int]$TimeoutSeconds = 1200,
    [string]$RunLabel,
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path.Replace('\','/')
$scriptDir = $PSScriptRoot.Replace('\','/')
$toolRoot = (Resolve-Path -LiteralPath $ToolRoot).Path.Replace('\','/')
$inputPath = (Resolve-Path -LiteralPath $JpegInput).Path.Replace('\','/')
$referencePath = if ($PrepareBatchReferences) {
    if ($Mode -ne 'batch') { throw '-PrepareBatchReferences requires -Mode batch.' }
    $batchOut = [System.IO.Path]::GetFullPath($Reference)
    $buildBoundary = [System.IO.Path]::GetFullPath($WorkDir).TrimEnd([char]'\') + '\'
    if (-not $batchOut.StartsWith($buildBoundary,[StringComparison]::OrdinalIgnoreCase)) { throw 'Generated batch references must be inside -WorkDir.' }
    $staging = Join-Path $PSScriptRoot ('_generated/' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    $python = (Resolve-Path -LiteralPath $PythonExe).Path
    & $python (Join-Path $PSScriptRoot 'prepare_batch_refs.py') --input-dir $inputPath --output-dir $staging --first $FrameId --last $EndFrameId | Write-Host
    if ($LASTEXITCODE -ne 0) { throw "OpenCV reference preparation failed with exit code $LASTEXITCODE." }
    New-Item -ItemType Directory -Path $batchOut -Force | Out-Null
    $generated = @(Get-ChildItem -LiteralPath $staging -File -Filter '*.gray8')
    if($generated.Count -ne $EndFrameId - $FrameId + 1) { throw 'Unexpected generated reference count.' }
    foreach($file in $generated) {
        if($file.Name -notmatch '^frame_\d{6}\.gray8$' -or $file.Length -ne 256000) { throw 'Unexpected generated reference file.' }
        Move-Item -LiteralPath $file.FullName -Destination (Join-Path $batchOut $file.Name) -Force
    }
    Remove-Item -LiteralPath $staging
    $batchOut.Replace('\','/')
} else {
    (Resolve-Path -LiteralPath $Reference).Path.Replace('\','/')
}
$workRoot = [System.IO.Path]::GetFullPath($WorkDir)
New-Item -ItemType Directory -Force -Path $workRoot | Out-Null
if (-not $RunLabel) { $RunLabel = (Get-Date -Format 'yyyyMMddTHHmmssfff') + "-$Flow" }
$runEvidence = Join-Path (Join-Path $workRoot 'runs') $RunLabel
New-Item -ItemType Directory -Path $runEvidence | Out-Null
$designFiles = @((Join-Path $repoRoot 'fpga/experimental/jpeg_axis/jpeg_axis_gray.cpp'), (Join-Path $repoRoot 'fpga/experimental/jpeg_axis/jpeg_axis_gray.h'))
$designFiles += @(Get-ChildItem -LiteralPath (Join-Path $repoRoot 'fpga/experimental/jpeg_axis/vendor') -File -Recurse | Select-Object -ExpandProperty FullName)
$liveDesignHashes = @($designFiles | Sort-Object | ForEach-Object {
    [ordered]@{path=[System.IO.Path]::GetRelativePath($repoRoot,$_).Replace('\','/');sha256=(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()}
})
$synthStatePath = Join-Path $workRoot 'synthesis-state.json'
$snapshotRoot = Join-Path $runEvidence 'source'
if ($Flow -eq 'cosim' -and -not $PrepareOnly) {
    if (-not (Test-Path -LiteralPath $synthStatePath)) { throw 'No source-bound successful csynth state; run csynth before cosim.' }
    $synthState = Get-Content -Raw -LiteralPath $synthStatePath | ConvertFrom-Json -AsHashtable
    if ($synthState.result -ne 'PASS') { throw 'Latest source-bound csynth did not pass.' }
    if (($liveDesignHashes | ConvertTo-Json -Depth 5 -Compress) -ne ($synthState.design_hashes | ConvertTo-Json -Depth 5 -Compress)) {
        throw 'Live design differs from the successful synthesis snapshot; rerun csim and csynth before cosim.'
    }
    $designSourceRoot = $synthState.source_root
} else {
    $designSourceRoot = $snapshotRoot
}
$snapshotFiles = @($designFiles) + @(Join-Path $PSScriptRoot 'jpeg_axis_gray_tb.cpp')
foreach ($file in $snapshotFiles) {
    $relative = [System.IO.Path]::GetRelativePath($repoRoot,$file)
    $destination = Join-Path $snapshotRoot $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -LiteralPath $file -Destination $destination
}
$snapshotManifest = @($snapshotFiles | ForEach-Object {
    $relative = [System.IO.Path]::GetRelativePath($repoRoot,$_).Replace('\','/')
    [ordered]@{path=$relative;sha256=(Get-FileHash -LiteralPath (Join-Path $snapshotRoot $relative) -Algorithm SHA256).Hash.ToLowerInvariant()}
})
$snapshotManifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $runEvidence 'source-snapshot.json') -Encoding utf8
foreach ($expected in $liveDesignHashes) {
    $copied = $snapshotManifest | Where-Object path -eq $expected.path
    if ($copied.sha256 -ne $expected.sha256) { throw "Source changed while snapshotting: $($expected.path); rerun with a stable source." }
}
$snapshotScriptDir = Join-Path $snapshotRoot 'scripts/experiments/jpeg_axis_verify'
$cfg = Join-Path $runEvidence 'hls_config.cfg'
$template = Get-Content -Raw (Join-Path $PSScriptRoot 'hls_config.cfg')
$rendered = $template.Replace('@SOURCE_ROOT@', $designSourceRoot.Replace('\','/')).Replace('@SCRIPT_DIR@', $snapshotScriptDir.Replace('\','/')).Replace('@TOOL_ROOT@', $toolRoot).Replace('@INPUT@', $inputPath).Replace('@REFERENCE@', $referencePath).Replace('@FRAME_ID@', [string]$FrameId).Replace('@END_FRAME_ID@', [string]$EndFrameId).Replace('@MODE@', $Mode).Replace('@REF_KIND@', $ReferenceKind).Replace('@TRACE_LEVEL@', $CosimTrace)
if ($rendered -match '@[A-Z_]+@') { throw "Unexpanded HLS config token: $($Matches[0])" }
Set-Content -LiteralPath $cfg -Value $rendered -Encoding utf8
$cfgHash = (Get-FileHash -LiteralPath $cfg -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $runEvidence 'hls_config.sha256') -Value $cfgHash -Encoding ascii
$sourceFiles = @(
    (Join-Path $repoRoot 'fpga/experimental/jpeg_axis/jpeg_axis_gray.cpp'),
    (Join-Path $repoRoot 'fpga/experimental/jpeg_axis/jpeg_axis_gray.h'),
    (Join-Path $PSScriptRoot 'jpeg_axis_gray_tb.cpp'),
    (Join-Path $PSScriptRoot 'run.ps1'),
    (Join-Path $PSScriptRoot 'hls_config.cfg')
)
$sourceFiles += @(Get-ChildItem -LiteralPath (Join-Path $repoRoot 'fpga/experimental/jpeg_axis/vendor/jpegDec') -File -Filter '*.hpp' | Select-Object -ExpandProperty FullName)
function Get-SourceHashes {
    @($sourceFiles | ForEach-Object {
        [ordered]@{
            path = [System.IO.Path]::GetRelativePath($repoRoot, $_).Replace('\','/')
            sha256 = if (Test-Path -LiteralPath $_) { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant() } else { $null }
        }
    })
}
$sourceHashesBefore = Get-SourceHashes
[ordered]@{
    recorded_at_utc = [DateTime]::UtcNow.ToString('o')
    flow = $Flow
    mode = $Mode
    timeout_seconds = $TimeoutSeconds
    config_sha256 = $cfgHash
    files = $sourceHashesBefore
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $runEvidence 'sources-before.json') -Encoding utf8
if ($PrepareOnly) {
    Write-Output "run_label=$RunLabel"
    Write-Output "config=$cfg"
    Write-Output "config_sha256=$cfgHash"
    exit 0
}

if ($Flow -eq 'cosim') {
    if (-not $rendered.Contains('cosim.random_stall=true')) { throw 'cosim.random_stall=true is required.' }
    if ([string]::IsNullOrWhiteSpace(([regex]::Match($rendered, '(?m)^cosim\.argv=(.+)$')).Groups[1].Value)) { throw 'cosim.argv must not be empty.' }
    if (-not (Test-Path -LiteralPath (Join-Path $workRoot 'hls/syn'))) {
        throw "C Synthesis must complete before Co-Simulation; expected '$workRoot/hls/syn'."
    }
}

$tool = if ($Flow -eq 'csynth') { Join-Path $toolRoot 'bin/v++.bat' } else { Join-Path $toolRoot 'bin/vitis-run.bat' }
if (-not (Test-Path -LiteralPath $tool)) { throw "HLS executable not found: $tool" }
$flowArgs = if ($Flow -eq 'csim') { '--mode hls --config "{0}" --work_dir "{1}" --csim' -f $cfg,$workRoot } elseif ($Flow -eq 'csynth') { '--compile --mode hls --config "{0}" --work_dir "{1}"' -f $cfg,$workRoot } else { '--mode hls --config "{0}" --work_dir "{1}" --cosim' -f $cfg,$workRoot }
$commandLine = '"{0}" {1}' -f $tool,$flowArgs
$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $env:ComSpec
$psi.Arguments = '/d /s /c "' + $commandLine + '"'
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $psi
if ($Flow -eq 'csynth') {
    [ordered]@{result='RUNNING';run_label=$RunLabel;source_root=$designSourceRoot;design_hashes=$liveDesignHashes} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $synthStatePath -Encoding utf8
}
if (-not $process.Start()) { throw "Could not start $Flow process." }
$stdoutPath = Join-Path $runEvidence 'stdout.log'
$stderrPath = Join-Path $runEvidence 'stderr.log'
$stdoutFile = [System.IO.File]::Open($stdoutPath,[System.IO.FileMode]::CreateNew,[System.IO.FileAccess]::Write,[System.IO.FileShare]::Read)
$stderrFile = [System.IO.File]::Open($stderrPath,[System.IO.FileMode]::CreateNew,[System.IO.FileAccess]::Write,[System.IO.FileShare]::Read)
$stdoutTask = $process.StandardOutput.BaseStream.CopyToAsync($stdoutFile)
$stderrTask = $process.StandardError.BaseStream.CopyToAsync($stderrFile)
$finished = $process.WaitForExit($TimeoutSeconds * 1000)
if (-not $finished) {
    $process.Kill($true)
    $process.WaitForExit()
}
$stdoutTask.GetAwaiter().GetResult() | Out-Null
$stderrTask.GetAwaiter().GetResult() | Out-Null
$stdoutFile.Dispose()
$stderrFile.Dispose()
$stdout = [System.IO.File]::ReadAllText($stdoutPath)
$stderr = [System.IO.File]::ReadAllText($stderrPath)
$exitCode = if ($finished) { $process.ExitCode } else { 124 }
$reportEvidence = Join-Path $runEvidence 'reports'
New-Item -ItemType Directory -Path $reportEvidence | Out-Null
if ($Flow -eq 'cosim') {
    $reports = @(Get-ChildItem -LiteralPath (Join-Path $workRoot 'hls/sim/report') -File -ErrorAction SilentlyContinue)
    foreach($report in $reports) { Copy-Item -LiteralPath $report.FullName -Destination (Join-Path $reportEvidence $report.Name) }
    foreach($relative in @('hls/sim/verilog/run_xsim.bat','hls/sim/verilog/jpeg_axis_gray.tcl','hls/sim/verilog/xsim.log','hls/sim/verilog/jpeg_axis_gray_subsystem/jpeg_axis_gray_env.sv')) {
        $file = Join-Path $workRoot $relative
        if(Test-Path -LiteralPath $file) { Copy-Item -LiteralPath $file -Destination (Join-Path $reportEvidence (Split-Path $file -Leaf)) }
    }
} elseif ($Flow -eq 'csynth') {
    foreach($relative in @('hls/syn/report/jpeg_axis_gray_csynth.rpt','hls/syn/report/jpeg_axis_gray_csynth.xml','hls/impl/ip/component.xml')) {
        $file = Join-Path $workRoot $relative
        if(Test-Path -LiteralPath $file) { Copy-Item -LiteralPath $file -Destination (Join-Path $reportEvidence (Split-Path $file -Leaf)) }
    }
}
$headerPaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$dependencyEvidence = Join-Path $runEvidence 'dependencies'
New-Item -ItemType Directory -Path $dependencyEvidence | Out-Null
if ($Flow -eq 'csynth') {
    $preprocessed = Join-Path $workRoot 'hls/.autopilot/db/jpeg_axis_gray.pp.0.cpp'
    if (Test-Path -LiteralPath $preprocessed) {
        Copy-Item -LiteralPath $preprocessed -Destination (Join-Path $dependencyEvidence 'jpeg_axis_gray.pp.0.cpp')
        $markers = [regex]::Matches((Get-Content -Raw -LiteralPath $preprocessed), '(?m)^#\s+\d+\s+"([^"]+)"')
        foreach ($marker in $markers) {
            $p = $marker.Groups[1].Value.Replace('\\','/').Replace('\','/')
            if ($p -match '^[A-Za-z]:/' -and (Test-Path -LiteralPath $p -PathType Leaf)) { [void]$headerPaths.Add([System.IO.Path]::GetFullPath($p)) }
        }
    }
} else {
    $deps = @(Get-ChildItem -LiteralPath (Join-Path $workRoot 'hls/csim/build/obj') -Filter '*.d' -File -ErrorAction SilentlyContinue)
    foreach ($dep in $deps) {
        Copy-Item -LiteralPath $dep.FullName -Destination (Join-Path $dependencyEvidence $dep.Name)
        foreach ($line in (Get-Content -LiteralPath $dep.FullName)) {
            $p = $line.Trim().TrimEnd([char]'\').Trim().Replace('\ ', ' ')
            if ($p -match '^[A-Za-z]:[\\/]' -and (Test-Path -LiteralPath $p -PathType Leaf)) { [void]$headerPaths.Add([System.IO.Path]::GetFullPath($p)) }
        }
    }
}
[ordered]@{
    provenance = if($Flow -eq 'csynth'){'synthesis preprocessor line markers; includes standard and tool headers'}else{'compiler .d dependencies; compiler omits system headers'}
    files = @($headerPaths | Sort-Object | ForEach-Object {[ordered]@{path=$_.Replace('\','/');sha256=(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()}})
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $runEvidence 'transitive-headers.json') -Encoding utf8
if ($Flow -eq 'csynth') {
    [ordered]@{result=if($exitCode -eq 0){'PASS'}else{'FAIL'};run_label=$RunLabel;source_root=$designSourceRoot;design_hashes=$liveDesignHashes;config_sha256=$cfgHash} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $synthStatePath -Encoding utf8
}
$sourceHashesAfter = Get-SourceHashes
[ordered]@{
    recorded_at_utc = [DateTime]::UtcNow.ToString('o')
    files = $sourceHashesAfter
    changed_during_run = (($sourceHashesBefore | ConvertTo-Json -Depth 5 -Compress) -ne ($sourceHashesAfter | ConvertTo-Json -Depth 5 -Compress))
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $runEvidence 'sources-after.json') -Encoding utf8
$log = Join-Path $runEvidence "$RunLabel.log"
Set-Content -LiteralPath $log -Value ($stdout + [Environment]::NewLine + $stderr) -Encoding utf8
if (-not $finished) {
    Add-Content -LiteralPath $log -Value "WATCHDOG_TIMEOUT after ${TimeoutSeconds}s; process tree terminated."
} elseif ($exitCode -ne 0) {
    Add-Content -LiteralPath $log -Value "FLOW_FAIL exit_code=$exitCode"
} else {
    Add-Content -LiteralPath $log -Value 'FLOW_PASS exit_code=0'
}
Set-Content -LiteralPath (Join-Path $runEvidence "$RunLabel.log.sha256") -Value ((Get-FileHash -LiteralPath $log -Algorithm SHA256).Hash.ToLowerInvariant()) -Encoding ascii
$process.Dispose()
Write-Output "run_label=$RunLabel"
Write-Output "config=$cfg"
Write-Output "config_sha256=$cfgHash"
Write-Output "log=$log"
if (-not $finished) { throw "$Flow exceeded watchdog ${TimeoutSeconds}s; process tree terminated. Evidence: $log" }
if ($exitCode -ne 0) { throw "$Flow failed with exit code $exitCode. Evidence: $log" }
