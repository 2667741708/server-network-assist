$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'Titan only' }
$adapter = Get-NetAdapter | Where-Object ifIndex -eq 16
$address = Get-NetIPAddress -InterfaceIndex 16 -AddressFamily IPv4
if (-not $adapter -or $address.IPAddress -notcontains '10.20.31.134') { throw 'Unexpected adapter' }
if ($adapter.Status -ne 'Up') { throw 'Original adapter not up' }
$restoreTask = Get-ScheduledTask -TaskName 'SNA-Titan-Link-Restore'
if ($restoreTask.State -eq 'Disabled') { throw 'Independent restore disabled' }
$restoreInfo = Get-ScheduledTaskInfo -TaskName 'SNA-Titan-Link-Restore'
if ($restoreInfo.NextRunTime -lt (Get-Date).AddSeconds(35)) { throw 'Insufficient independent restore margin' }
$adapter | Disable-NetAdapter -Confirm:$false
