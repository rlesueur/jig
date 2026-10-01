<#
Registers (or disables / removes) the per-user scheduled task "\Jig\Jig Research Overnight". No admin needed.

  .\register-overnight-task.ps1            # register (or update) and enable
  .\register-overnight-task.ps1 -Disable   # stop it from running (keeps the definition)
  .\register-overnight-task.ps1 -Remove    # delete it

The task wakes every 30 minutes; each wake-up asks the compute policy whether it may start, and exits
at once if not. It runs only while the user is logged on and never wakes the computer.
#>
param([switch]$Disable, [switch]$Remove)
$ErrorActionPreference = 'Stop'
$path = '\Jig\'
$name = 'Jig Research Overnight'

if ($Disable) { Disable-ScheduledTask -TaskPath $path -TaskName $name | Out-Null; "Disabled $path$name"; return }
if ($Remove) { Unregister-ScheduledTask -TaskPath $path -TaskName $name -Confirm:$false; "Removed $path$name"; return }

$script = Join-Path $PSScriptRoot 'overnight.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable:$false `
    -AllowStartIfOnBatteries:$false -DontStopIfGoingOnBatteries:$false -ExecutionTimeLimit (New-TimeSpan -Hours 7)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskPath $path -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'Jig research benchmarks under the compute policy (research/harness). Disable: research\scripts\register-overnight-task.ps1 -Disable' -Force | Out-Null
Get-ScheduledTask -TaskPath $path -TaskName $name | Select-Object TaskPath, TaskName, State
