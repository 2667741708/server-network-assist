param([ValidateSet('close','launch','inspect','enroll','click','scroll')][string]$Action,[int]$X,[int]$Y,[int]$Delta=-600)
$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'Titan only' }
$script = 'C:\ProgramData\ServerNetworkAssist\client-native\gui-diagnostics-20260917\window-test.ps1'
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File ' + $script + ' -Action ' + $Action
if ($Action -eq 'inspect') { $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\ProgramData\ServerNetworkAssist\client-native\gui-diagnostics-20260917\inspect.ps1' }
if ($Action -in @('enroll','click','scroll')) { $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\ProgramData\ServerNetworkAssist\client-native\gui-diagnostics-20260917\input.ps1 -Action ' + $Action + ' -X ' + $X + ' -Y ' + $Y + ' -Delta ' + $Delta }
$command = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$principal = New-ScheduledTaskPrincipal -UserId 'd321-titan' -LogonType Interactive -RunLevel Highest
$name = 'SNA-Titan-Gui-20260917-' + $Action
Register-ScheduledTask -TaskName $name -Action $command -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Output $name
