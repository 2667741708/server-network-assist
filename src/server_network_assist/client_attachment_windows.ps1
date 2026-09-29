$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$adapters = @(Get-NetAdapter -Physical)
$addresses = @(Get-NetIPAddress -AddressFamily IPv4)
$addresses6 = @(Get-NetIPAddress -AddressFamily IPv6 -ErrorAction SilentlyContinue)
$interfaces = @(Get-NetIPInterface -AddressFamily IPv4)
$allRoutes = @(Get-NetRoute -PolicyStore ActiveStore -AddressFamily IPv4 -ErrorAction Stop)
$defaults = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue)
$defaults6 = @(Get-NetRoute -AddressFamily IPv6 -DestinationPrefix '::/0' -ErrorAction SilentlyContinue)
$interfaces6 = @(Get-NetIPInterface -AddressFamily IPv6 -ErrorAction SilentlyContinue)
$profiles = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue)
$links = @()
foreach ($adapter in $adapters) {
    $index = [int]$adapter.ifIndex
    $ips = @($addresses | Where-Object { $_.InterfaceIndex -eq $index -and $_.AddressState -eq 'Preferred' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { [string]$_.IPAddress })
    $iface = $interfaces | Where-Object { $_.InterfaceIndex -eq $index } | Select-Object -First 1
    $iface6 = $interfaces6 | Where-Object { $_.InterfaceIndex -eq $index } | Select-Object -First 1
    $ips6 = @($addresses6 | Where-Object { $_.InterfaceIndex -eq $index -and $_.AddressState -eq 'Preferred' -and $_.IPAddress -notlike 'fe80:*' -and $_.IPAddress -ne '::1' } | ForEach-Object { [string]$_.IPAddress })
    $profile = $profiles | Where-Object { $_.InterfaceIndex -eq $index } | Select-Object -First 1
    $routes = @($defaults | Where-Object { $_.InterfaceIndex -eq $index } | ForEach-Object {
        @{ gateway = [string]$_.NextHop; metric = [int]$_.RouteMetric + [int]$iface.InterfaceMetric }
    })
    $wifi = [int]$adapter.NdisPhysicalMedium -in @(1,9)
    $routes6 = @($defaults6 | Where-Object { $_.InterfaceIndex -eq $index } | ForEach-Object {
        @{ gateway = [string]$_.NextHop; metric = [int]$_.RouteMetric + [int]$iface6.InterfaceMetric }
    })
    $links += @{
        id = $index; name = [string]$adapter.Name; wifi = $wifi; profile = [string]$profile.Name
        connected = ($adapter.Status -eq 'Up' -and (($iface.ConnectionState -eq 'Connected' -and $ips.Count -gt 0) -or ($iface6.ConnectionState -eq 'Connected' -and $ips6.Count -gt 0)))
        addresses = $ips; defaults = $routes; addresses6 = $ips6; defaults6 = $routes6
    }
}
$routeRows = @($allRoutes | ForEach-Object {
    $route = $_
    $routeIndex = [int]$route.InterfaceIndex
    $routeInterface = $interfaces | Where-Object { [int]$_.InterfaceIndex -eq $routeIndex } | Select-Object -First 1
    @{
        destination_prefix = [string]$route.DestinationPrefix
        next_hop = [string]$route.NextHop
        interface_index = $routeIndex
        route_metric = [int]$route.RouteMetric
        interface_metric = if ($null -ne $routeInterface) { [int]$routeInterface.InterfaceMetric } else { $null }
        connection_state = if ($null -ne $routeInterface) { [int]$routeInterface.ConnectionState } else { $null }
        interface_alias = [string]$route.InterfaceAlias
    }
})
@{ supported = $true; links = $links; routes = $routeRows } | ConvertTo-Json -Depth 7 -Compress
