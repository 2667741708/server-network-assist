$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'Titan only' }
$script = 'C:\Users\86133\sna-wifi-goal-20260917\scripts\titan_codex_goal_runner.py'
$action = New-ScheduledTaskAction -Execute 'C:\Users\86133\.codex\tools\Python313\pythonw.exe' -Argument $script
$principal = New-ScheduledTaskPrincipal -UserId 'd321-titan' -LogonType Interactive -RunLevel Highest
$name = 'SNA-Codex-Wifi-20260917'
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Output $name
