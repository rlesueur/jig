<#
Registers (or disables / removes) the per-user watchdog task "\Jig\Jig Research Restore". No admin needed.

  .\register-restore-task.ps1            # register (or update) and enable
  .\register-restore-task.ps1 -Disable   # stop it from running (keeps the definition)
  .\register-restore-task.ps1 -Remove    # delete it

The task wakes every 5 minutes while the user is logged on and restores the main model server from the
latest unrestored GPU-handover file if no harness is alive to do it. It never wakes the computer. This is
the independent safety net that guarantees the 8080 server is restored even if the harness crashes.
#>
param([switch]$Disable, [switch]$Remove)
$ErrorActionPreference = 'Stop'
$path = '\Jig\'
$name = 'Jig Research Restore'

if ($Disable) { Disable-ScheduledTask -TaskPath $path -TaskName $name | Out-Null; "Disabled $path$name"; return }
if ($Remove) { Unregister-ScheduledTask -TaskPath $path -TaskName $name -Confirm:$false; "Removed $path$name"; return }

$script = Join-Path $PSScriptRoot 'restore-watchdog.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable:$false `
    -AllowStartIfOnBatteries:$true -DontStopIfGoingOnBatteries:$true -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskPath $path -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'Restore the main model server after a Jig research GPU handover. Disable: research\scripts\register-restore-task.ps1 -Disable' -Force | Out-Null
Get-ScheduledTask -TaskPath $path -TaskName $name | Select-Object TaskPath, TaskName, State
