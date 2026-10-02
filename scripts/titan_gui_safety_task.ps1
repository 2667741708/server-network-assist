param([switch]$Remove)
$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'Titan only' }
$name = 'SNA-Titan-Gui-20260917-safety-exit'
if ($Remove) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    exit
}
$exe = 'C:\ProgramData\ServerNetworkAssist\client-native\gui-diagnostics-20260917\ServerNetworkAssistClient.exe'
$data = 'C:\ProgramData\ServerNetworkAssist\client\S-1-5-21-1446874470-693334550-1715965273-1001\gui-window-test-20260917'
$action = New-ScheduledTaskAction -Execute $exe -Argument ('--leave-network --data ' + $data)
$principal = New-ScheduledTaskPrincipal -UserId 'd321-titan' -LogonType Interactive -RunLevel Highest
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Write-Output 'Safety exit armed; this is not a client login command'
