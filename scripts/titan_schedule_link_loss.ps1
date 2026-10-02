$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'Titan only' }
$root = 'C:\ProgramData\ServerNetworkAssist\titan-exit-test'
$restore = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File $root\titan_restore_test_adapter.ps1"
$system = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(50)
Register-ScheduledTask -TaskName 'SNA-Titan-Link-Restore' -Action $restore -Trigger $trigger -Principal $system -Force | Out-Null
$registered = Get-ScheduledTask -TaskName 'SNA-Titan-Link-Restore'
if ($registered.State -eq 'Disabled') { throw 'Independent restore unavailable' }
$test = New-ScheduledTaskAction -Execute 'C:\Users\86133\.codex\tools\Python313\python.exe' -Argument "$root\titan_link_loss_acceptance.py"
Register-ScheduledTask -TaskName 'SNA-Titan-Link-Loss-Test' -Action $test -Principal $system -Force | Out-Null
Start-ScheduledTask -TaskName 'SNA-Titan-Link-Loss-Test'
