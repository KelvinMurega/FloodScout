# Registers a Windows scheduled task that keeps the FloodScout scores current
# without anyone opening the app: it runs start-scheduler.ps1 when you sign in
# and every morning, and if the computer was off at that time it runs as soon
# as it is back on.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-scheduler-task.ps1
#
# To remove it:
#
#   Unregister-ScheduledTask -TaskName "FloodScout daily refresh" -Confirm:$false
#
# Docker Desktop runs only while someone is signed in, so the task does too.
# A computer that is switched off cannot refresh anything; the days it missed
# are scored when it next runs.

param(
    [string]$At = "07:00"
)

$ErrorActionPreference = "Stop"
$taskName = "FloodScout daily refresh"
$script = Join-Path $PSScriptRoot "start-scheduler.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$triggers = @(
    New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    New-ScheduledTaskTrigger -Daily -At $At
)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal `
    -Description "Starts Docker and the FloodScout scheduler, which refreshes flood risk scores and catches up on missed days." `
    -Force | Out-Null

Write-Output "Registered '$taskName': at sign-in and daily at $At (or as soon as possible after)."
