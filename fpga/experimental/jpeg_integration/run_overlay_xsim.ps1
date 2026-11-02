[CmdletBinding()]
param(
    [string]$BuildRoot = '',
    [string]$ToolDir = 'C:/AMDDesignTools/2026.1/Vivado/bin',
    [string]$XpmSource = 'C:/AMDDesignTools/2026.1/Vivado/data/ip/xpm/xpm_cdc/hdl/xpm_cdc.sv',
    [string]$GlblSource = 'C:/AMDDesignTools/2026.1/Vivado/data/verilog/src/glbl.v'
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($BuildRoot)) {
    $BuildRoot = Join-Path $PSScriptRoot '../../../.runtime/overlay-xsim'
}
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$rtl = Join-Path $PSScriptRoot 'overrides/video_binary_overlay.v'
$benches = @(
    (Join-Path $PSScriptRoot 'tb_overlay_count_atomic.sv'),
    (Join-Path $PSScriptRoot 'tb_overlay_atomic_update.sv')
)
$sources = @($rtl, $XpmSource, $GlblSource) + $benches
$tools = @('xvlog.bat', 'xelab.bat', 'xsim.bat') | ForEach-Object { Join-Path $ToolDir $_ }
foreach ($path in @($sources) + $tools) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing required file: $path" }
}

$runId = 'overlay-xsim-{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 8))
$build = Join-Path ([IO.Path]::GetFullPath($BuildRoot)) $runId
New-Item -ItemType Directory -Path $build | Out-Null
$oldLocation = Get-Location
Push-Location -LiteralPath $build
try {
    & ($tools[0]) -sv $sources -log compile.xvlog.log
    if ($LASTEXITCODE -ne 0) { throw "xvlog failed with exit code $LASTEXITCODE; inspect $build/compile.xvlog.log" }

    $tests = @(
        @{ Top = 'tb_overlay_count_atomic'; Snapshot = 'overlay_count'; Label = 'count'; Marker = 'TEST_PASS count glyph:' },
        @{ Top = 'tb_overlay_atomic_update'; Snapshot = 'overlay_atomic'; Label = 'atomic'; Marker = 'TEST_PASS overlay atomic update:' }
    )
    $simulationLogs = @()
    foreach ($test in $tests) {
        & ($tools[1]) $test.Top glbl -mt 2 -s $test.Snapshot -log "$($test.Label).xelab.log"
        if ($LASTEXITCODE -ne 0) { throw "xelab failed for $($test.Top), exit code $LASTEXITCODE; inspect $build/$($test.Label).xelab.log" }

        $logName = "$($test.Label).simulate.log"
        & ($tools[2]) $test.Snapshot -runall -log $logName
        if ($LASTEXITCODE -ne 0) { throw "xsim failed for $($test.Top), exit code $LASTEXITCODE; inspect $build/$logName" }
        if (-not (Test-Path -LiteralPath $logName -PathType Leaf)) { throw "XSim transcript missing: $build/$logName" }
        $transcript = Get-Content -LiteralPath $logName -Raw
        if ([regex]::Matches($transcript, "(?m)^$([regex]::Escape($test.Marker))").Count -ne 1) {
            throw "Expected exactly one $($test.Marker) marker; inspect $build/$logName"
        }
        if ($transcript -match '(?m)^\s*(Fatal:|ERROR:)') { throw "Fatal/error in simulation transcript: $build/$logName" }
        $simulationLogs += $logName
    }

    $evidence = @($sources + @('compile.xvlog.log') + @('count.xelab.log', 'count.simulate.log', 'atomic.xelab.log', 'atomic.simulate.log')) |
        ForEach-Object {
            $item = if ([IO.Path]::IsPathRooted($_)) { $_ } else { Join-Path $build $_ }
            $hash = Get-FileHash -LiteralPath $item -Algorithm SHA256
            [pscustomobject]@{ path = [IO.Path]::GetFullPath($item); sha256 = $hash.Hash.ToLowerInvariant(); bytes = (Get-Item -LiteralPath $item).Length }
        }
    $receipt = [pscustomobject]@{
        status = 'PASS'
        scope = 'count glyph BCD and atomic pixel-domain overlay update'
        pass_markers = @('TEST_PASS count glyph', 'TEST_PASS overlay atomic update')
        sources_and_logs = @($evidence)
        build_dir = $build
    }
    $receiptPath = Join-Path $build 'overlay_xsim_receipt.json'
    $receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receiptPath -Encoding utf8
    Write-Output ($receipt | ConvertTo-Json -Depth 5)
}
finally {
    Pop-Location
}
