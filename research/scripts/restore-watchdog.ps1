# Entry point for the "\Jig\Jig Research Restore" watchdog scheduled task.
# Independent of the harness: if a GPU handover left the main model server stopped and no harness is
# alive to restore it, this restores it from the latest unrestored handover file. Safe to run often; it
# does nothing when there is nothing to restore. See research/harness/jigbench/handover.py.
$ErrorActionPreference = 'Stop'
$research = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $research 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$log = Join-Path $logs ("restore-watchdog-{0:yyyyMMdd}.log" -f (Get-Date))
$python = Join-Path $research '.venv\Scripts\python.exe'
Set-Location $research
"=== $(Get-Date -Format o) restore-watchdog start (pid $PID)" | Add-Content -Path $log -Encoding utf8
$ErrorActionPreference = 'Continue'
& $python -m jigbench restore-watchdog 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath $log -Append -Encoding utf8
$code = $LASTEXITCODE
"=== $(Get-Date -Format o) restore-watchdog end, exit $code" | Add-Content -Path $log -Encoding utf8
exit $code
