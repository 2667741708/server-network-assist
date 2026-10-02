param([ValidateSet('close','verify','launch')][string]$Action,[string]$TargetExe,[string]$ExpectedHash)
$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'D321 only' }
$name = 'SNA-Subscription-20260917-' + $Action + '-' + (Get-Date -Format 'HHmmss')
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { throw 'Task already exists; inspect it before replacement' }
if ($Action -eq 'launch') {
    if (Get-Process -Name ServerNetworkAssistClient -ErrorAction SilentlyContinue) { throw 'Existing client processes remain' }
    if (-not $TargetExe.StartsWith('C:\Users\86133\AppData\Local\Programs\PureNetworkClient\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected EXE' }
    if ((Get-FileHash -LiteralPath $TargetExe).Hash -ne $ExpectedHash) { throw 'EXE hash mismatch' }
    $data = 'C:\ProgramData\ServerNetworkAssist\client\S-1-5-21-1446874470-693334550-1715965273-1001\gui-window-test-20260917'
    $command = New-ScheduledTaskAction -Execute $TargetExe -Argument ('--data "'+$data+'"')
} else {
    $script = 'C:\Users\86133\sna-local-gui-goal-20260917/d321_subscription_upgrade.ps1'
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$script+'" -Action '+$Action
    $command = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument $arguments
}
$principal = New-ScheduledTaskPrincipal -UserId 'd321-titan' -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $name -Action $command -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $name
Write-Output ('Started '+$name)
