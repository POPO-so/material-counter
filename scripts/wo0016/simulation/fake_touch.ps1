param(
    [string]$Dir,
    [string]$Name,
    [switch]$Fail
)
if ($Fail) {
    [Console]::Error.WriteLine('fake touch failed')
    exit 1
}
$path = Join-Path $Dir $Name
if (-not (Test-Path $path)) { New-Item -ItemType File -Path $path | Out-Null }
Write-Output '1.3 0'
exit 0