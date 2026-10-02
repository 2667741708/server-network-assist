$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
if ([Environment]::MachineName -ne 'WHM-SAVE') { throw 'WHM-SAVE only' }
$root = 'C:\Users\hmw20\.ssh\gui-d321-control-20260917'
$before = Get-Content -LiteralPath (Join-Path $root 'baseline.json') -Raw | ConvertFrom-Json
$routes = @(Get-NetRoute | Select-Object DestinationPrefix,InterfaceIndex,NextHop,RouteMetric | Sort-Object DestinationPrefix,InterfaceIndex,NextHop,RouteMetric)
$dns = @(Get-DnsClientServerAddress | Select-Object InterfaceIndex,AddressFamily,ServerAddresses | Sort-Object InterfaceIndex,AddressFamily)
$proxy = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' | Select-Object ProxyEnable,ProxyServer,ProxyOverride,AutoConfigURL
function Same-Json($left,$right) {
    return (($left | ConvertTo-Json -Depth 8 -Compress) -ceq ($right | ConvertTo-Json -Depth 8 -Compress))
}
$report = @{
    time = (Get-Date).ToString('o')
    baseline_time = $before.time
    routes_equal = (Same-Json $before.routes $routes)
    dns_equal = (Same-Json $before.dns $dns)
    wininet_proxy_equal = (Same-Json $before.proxy $proxy)
    scope = 'Selected route fields, DNS server addresses, four WinINET proxy fields; not a complete OS configuration audit'
}
$json = $report | ConvertTo-Json -Depth 5 -Compress
try {
    $target = Join-Path (Split-Path -Parent $PSScriptRoot) 'artifacts\local-gui-network-baseline-20260917.json'
    $json | Set-Content -LiteralPath $target -Encoding UTF8
} catch { Write-Warning ('Optional report unavailable: ' + $_.Exception.GetType().Name) }
Write-Output $json
