$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'Titan only' }
$root = 'C:\ProgramData\ServerNetworkAssist\titan-exit-test'
New-Item -ItemType Directory -Path $root -Force | Out-Null
& icacls.exe $root /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect backup directory' }
$wg = 'C:\Program Files\WireGuard\wg.exe'
$conf = & $wg showconf fleet-titan
if ($LASTEXITCODE -ne 0) { throw 'Cannot back up legacy tunnel' }
$conf | Set-Content -LiteralPath "$root\fleet-original.conf" -Encoding ASCII
$peer = (& $wg show fleet-titan peers).Trim()
$peer | Set-Content -LiteralPath "$root\fleet-peer.txt" -Encoding ASCII
$serviceKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\WireGuardTunnel$fleet-titan'
(Get-ItemProperty -LiteralPath $serviceKey).ImagePath | Set-Content -LiteralPath "$root\fleet-service-path.txt" -Encoding ASCII
Export-ScheduledTask -TaskName 'ServerNetworkAssist-Client' | Set-Content -LiteralPath "$root\client-task.xml" -Encoding UTF8
Get-NetRoute -AddressFamily IPv4 | Select-Object DestinationPrefix,InterfaceAlias,NextHop,RouteMetric | ConvertTo-Json | Set-Content -LiteralPath "$root\routes-original.json" -Encoding UTF8
$rollback = @'
$ErrorActionPreference = 'Stop'
$root = 'C:\ProgramData\ServerNetworkAssist\titan-exit-test'
if (Test-Path -LiteralPath "$root\migration-ok") { exit 0 }
$peer = (Get-Content -LiteralPath "$root\fleet-peer.txt").Trim()
Set-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\WireGuardTunnel$fleet-titan' -Name ImagePath -Value (Get-Content -LiteralPath "$root\fleet-service-path.txt")
& 'C:\Program Files\WireGuard\wg.exe' set fleet-titan peer $peer allowed-ips '0.0.0.0/1,128.0.0.0/1'
foreach ($prefix in @('0.0.0.0/1','128.0.0.0/1')) {
    if (-not (Get-NetRoute -InterfaceAlias fleet-titan -DestinationPrefix $prefix -ErrorAction SilentlyContinue)) {
        New-NetRoute -InterfaceAlias fleet-titan -DestinationPrefix $prefix -NextHop '0.0.0.0' -RouteMetric 0 | Out-Null
    }
}
'@
$rollback | Set-Content -LiteralPath "$root\rollback.ps1" -Encoding ASCII
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\ServerNetworkAssist\titan-exit-test\rollback.ps1'
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(4)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'SNA-Titan-Migration-Rollback' -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Disable-ScheduledTask -TaskName 'ServerNetworkAssist-Client' | Out-Null
Stop-ScheduledTask -TaskName 'ServerNetworkAssist-Client'
& $wg set fleet-titan peer $peer allowed-ips '10.203.49.0/24'
if ($LASTEXITCODE -ne 0) { throw 'Legacy management shrink failed' }
Get-NetRoute -InterfaceAlias fleet-titan -AddressFamily IPv4 | Where-Object DestinationPrefix -in @('0.0.0.0/1','128.0.0.0/1') | Remove-NetRoute -Confirm:$false
if (-not (Get-NetRoute -InterfaceAlias fleet-titan -DestinationPrefix '10.203.49.0/24' -ErrorAction SilentlyContinue)) {
    New-NetRoute -InterfaceAlias fleet-titan -DestinationPrefix '10.203.49.0/24' -NextHop '0.0.0.0' -RouteMetric 5 | Out-Null
}
Set-Service -Name 'WireGuardTunnel$fleet-titan' -StartupType Manual
$management = ($conf -join "`n") -replace '(?m)^AllowedIPs\s*=.*$', 'AllowedIPs = 10.203.49.0/24'
$management = $management -replace '\[Interface\]', "[Interface]`nAddress = 10.203.49.3/32"
$management | Set-Content -LiteralPath "$root\fleet-titan.conf" -Encoding ASCII
Set-ItemProperty -LiteralPath $serviceKey -Name ImagePath -Value '"C:\Program Files\WireGuard\wireguard.exe" /tunnelservice "C:\ProgramData\ServerNetworkAssist\titan-exit-test\fleet-titan.conf"'
Write-Output 'Legacy public borrowing removed; management preserved; rollback armed'
