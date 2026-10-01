# Entry point for the "\Jig\Jig Research Overnight" scheduled task.
# The Python side enforces the compute policy (01:00-07:00 Europe/London or 30 min idle, GPU not in use
# by others) and stops promptly when the user returns; this wrapper only sets paths and logs.
$ErrorActionPreference = 'Stop'
$research = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $research 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$log = Join-Path $logs ("overnight-task-{0:yyyyMMdd}.log" -f (Get-Date))
$python = Join-Path $research '.venv\Scripts\python.exe'
$queue = Join-Path $research 'harness\configs\queue.yaml'
"=== $(Get-Date -Format o) task start (pid $PID)" | Add-Content -Path $log -Encoding utf8
Set-Location $research
$ErrorActionPreference = 'Continue'
& $python -m jigbench overnight --queue $queue 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath $log -Append -Encoding utf8
$code = $LASTEXITCODE
"=== $(Get-Date -Format o) task end, exit $code" | Add-Content -Path $log -Encoding utf8
exit $code
