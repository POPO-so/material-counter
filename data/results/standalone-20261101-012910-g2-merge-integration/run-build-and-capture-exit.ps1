$ErrorActionPreference = "Continue"
Set-Location "R:\"
$env:PYNQ_SOURCE = "P:\pynq-v3.1"
$env:VIDEO_IP_BUILT = "P:\standalone-ip"
$env:G2_OUT = "R:\data\results\standalone-20261101-012910-g2-integration"
$env:G2_MONITOR_SIM_OUT = "R:\data\results\standalone-20261101-012910-g2-integration\monitor-sim"
$env:OFFLOAD_CANDIDATE = "1"
$env:DISPLAY_BUILD_JOBS = "1"
$env:DISPLAY_MAX_THREADS = "1"
$env:XILINX_VIVADO = "C:\AMDDesignTools\2026.1\Vivado"
Set-Content -LiteralPath "R:\data\results\standalone-20261101-012910-g2-integration\build-start-local-runner.txt" -Value (Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz")
& "C:\AMDDesignTools\2026.1\Vivado\bin\vivado.bat" -mode batch -source "R:\scripts\standalone\build_g2_integration.tcl" -log "R:\data\results\standalone-20261101-012910-g2-integration\vivado-batch.log" -journal "R:\data\results\standalone-20261101-012910-g2-integration\vivado-batch.jou" | Out-Null
$exitCode = $LASTEXITCODE
Set-Content -LiteralPath "R:\data\results\standalone-20261101-012910-g2-integration\vivado-exit-code.txt" -Value $exitCode -NoNewline
Set-Content -LiteralPath "R:\data\results\standalone-20261101-012910-g2-integration\build-end-local-runner.txt" -Value (Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz")
exit $exitCode
