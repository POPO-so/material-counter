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
    'tb_region_diff.sv','tb_region_limits.sv','tb_region_candidate_filters.sv',
    'rle_feature_tap.v','region_xsim.tcl','region_ooc.tcl','region_clock.xdc')
$sourcePaths=@{}
foreach($name in $files) { $sourcePaths[$name]=Join-Path $PSScriptRoot $name }
$sourcePaths['rle_feature_tap.v']=Join-Path $PSScriptRoot '..\rle\rle_feature_tap.v'
foreach($name in $files) { Copy-Item -LiteralPath $sourcePaths[$name] -Destination (Join-Path $inputDir $name) }
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
        $after=(Get-FileHash -Algorithm SHA256 -LiteralPath $sourcePaths[$name]).Hash
        if($before -ne $after) { throw "Source changed during run: $name. Results cover the frozen input only." }
    }
    if($Mode -eq 'all') {
        $receiptFiles=[ordered]@{
            'rle_region_features.v'=(Join-Path $inputDir 'rle_region_features.v')
            'tb_region_candidate_filters.sv'=(Join-Path $inputDir 'tb_region_candidate_filters.sv')
            'rle_feature_tap.v'=(Join-Path $inputDir 'rle_feature_tap.v')
            'region_candidates.log'=(Join-Path $runDir 'sim\region_candidates.log')
            'region_cases.log'=(Join-Path $runDir 'sim\region_cases.log')
            'region_diff.log'=(Join-Path $runDir 'sim\region_diff.log')
            'region_limits.log'=(Join-Path $runDir 'sim\region_limits.log')
            'region_driver.log'=(Join-Path $runDir 'sim\region_driver.log')
            'region_inputs.sha256'=(Join-Path $runDir 'region_inputs.sha256')
            'region_memory.rpt'=(Join-Path $runDir 'ooc\region_memory.rpt')
            'region_util.rpt'=(Join-Path $runDir 'ooc\region_util.rpt')
            'region_elab_tap.log'=(Join-Path $runDir 'sim\region_elab_tap.log')
        }
        $evidence=@(foreach($name in $receiptFiles.Keys) {
            $path=$receiptFiles[$name]
            if(-not(Test-Path -LiteralPath $path)) { throw "Receipt evidence is missing: $path" }
            [ordered]@{
                path=(Resolve-Path -LiteralPath $path).Path.Replace('\','/')
                sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
            }
        })
        $sourceSha=(Get-FileHash -Algorithm SHA256 -LiteralPath $receiptFiles['rle_region_features.v']).Hash.ToLowerInvariant()
        $receipt=[ordered]@{
            schema='material-counter.rle-region-bram-g2-green.v1'
            status='PASS'
            source_sha256=$sourceSha
            evidence=$evidence
        }
        $receiptPath=Join-Path $runDir 'region-green.json'
        if(Test-Path -LiteralPath $receiptPath) { throw "Refusing to overwrite region receipt: $receiptPath" }
        $receiptJson=$receipt | ConvertTo-Json -Depth 5
        [System.IO.File]::WriteAllText($receiptPath,$receiptJson,[System.Text.UTF8Encoding]::new($false))
        Write-Output "REGION_GREEN_RECEIPT: $receiptPath"
    }
    Write-Output "REGION_RUN_PASS: $runDir"
} finally {
    $env:REGION_SRC_DIR=$oldSrc
    $env:REGION_REQUIRE_BRAM=$oldRequire
}
