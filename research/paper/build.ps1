# Regenerate tables and figures from results, then build main.pdf with Tectonic.
#   .\build.ps1            # report + PDF
#   .\build.ps1 -NoReport  # PDF only
param([switch]$NoReport)
$ErrorActionPreference = 'Stop'
$paper = $PSScriptRoot
$research = Split-Path -Parent $paper
$tectonic = if ($env:TECTONIC) { $env:TECTONIC } else { 'C:\Users\you\tools\tectonic-0.17.0\tectonic.exe' }
if (-not (Test-Path $tectonic)) { throw "Tectonic not found at $tectonic (set `$env:TECTONIC)" }
if (-not $NoReport) {
    & (Join-Path $research '.venv\Scripts\python.exe') -m jigbench report | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "jigbench report failed" }
}
Set-Location $paper
New-Item -ItemType Directory -Force build | Out-Null
& $tectonic -X compile main.tex --outdir build --keep-logs
if ($LASTEXITCODE -ne 0) { throw "tectonic failed (see build\main.log)" }
"Built $(Join-Path $paper 'build\main.pdf')"
