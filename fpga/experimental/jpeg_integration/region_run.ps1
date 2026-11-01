param(
    [ValidateSet('all','sim','ooc')][string]$Mode='all',
    [string]$BuildRoot='C:\w\mc-jpeg-regions-20261101',
    [string]$Vivado='C:\AMDDesignTools\2026.1\Vivado\bin\vivado.bat'
)
$ErrorActionPreference='Stop'
$runDir=Join-Path $BuildRoot ('region-'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$inputDir=Join-Path $runDir 'input'
New-Item -ItemType Directory -Path $inputDir -Force | Out-Null
$files=@('rle_region_features.v','region_reference.v','tb_rle_region_features.sv',
    'tb_region_diff.sv','tb_region_limits.sv','region_xsim.tcl','region_ooc.tcl','region_clock.xdc')
foreach($name in $files) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $inputDir }
$hashes=foreach($name in $files) {
    $hash=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $inputDir $name)).Hash
    "$hash  $name"
}
$hashes | Set-Content -LiteralPath (Join-Path $runDir 'region_inputs.sha256') -Encoding ascii
$oldSrc=$env:REGION_SRC_DIR
$oldRequire=$env:REGION_REQUIRE_BRAM
try {
    $env:REGION_SRC_DIR=$inputDir
    $env:REGION_REQUIRE_BRAM='1'
    foreach($stage in @('sim','ooc')) {
        if($Mode -ne 'all' -and $Mode -ne $stage) { continue }
        $stageDir=Join-Path $runDir $stage
        New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
        Push-Location -LiteralPath $stageDir
        try {
            $scriptName=if($stage -eq 'sim') {'region_xsim.tcl'} else {'region_ooc.tcl'}
            & $Vivado -mode batch -source (Join-Path $inputDir $scriptName) -log region_driver.log -journal region_driver.jou
            if($LASTEXITCODE -ne 0) { throw "$stage failed with exit $LASTEXITCODE; inspect $stageDir" }
        } finally { Pop-Location }
    }
    foreach($name in $files) {
        $before=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $inputDir $name)).Hash
        $after=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PSScriptRoot $name)).Hash
        if($before -ne $after) { throw "Source changed during run: $name. Results cover the frozen input only." }
    }
    Write-Output "REGION_RUN_PASS: $runDir"
} finally {
    $env:REGION_SRC_DIR=$oldSrc
    $env:REGION_REQUIRE_BRAM=$oldRequire
}
