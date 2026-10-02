param([switch]$Remove)
$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'WHM-SAVE') { throw 'WHM-SAVE only' }
$name = 'SNA-WHM-Gui-D321-20260917-safety-exit'
if ($Remove) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    Write-Output 'Customer-only safety task removed'
    exit
}
$root = 'C:\Users\hmw20\.ssh\gui-d321-control-20260917'
$settings = Get-Content -LiteralPath (Join-Path $root 'gui-config.json') -Raw | ConvertFrom-Json
if ((Get-FileHash -LiteralPath $settings.exe -Algorithm SHA256).Hash -ne $settings.sha256) { throw 'Unexpected customer EXE' }
$action = New-ScheduledTaskAction -Execute $settings.exe -Argument ('--leave-network --data ' + $settings.data)
$principal = New-ScheduledTaskPrincipal -UserId hmw20 -LogonType Interactive -RunLevel Highest
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(8)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Write-Output 'Safety armed for the isolated customer data only; no login/connect performed'
