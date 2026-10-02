$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'Titan only' }
$adapter = Get-NetAdapter | Where-Object ifIndex -eq 16
if (-not $adapter -or $adapter.InterfaceDescription -notmatch 'Ethernet|Realtek|Intel') { throw 'Unexpected adapter' }
$adapter | Enable-NetAdapter -Confirm:$false
