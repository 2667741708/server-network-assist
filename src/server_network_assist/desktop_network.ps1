param(
    [ValidateSet('status','connect','disconnect')][string]$Action = 'status',
    [string]$Tunnel = ''
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$services = @(Get-Service -Name 'WireGuardTunnel$*' -ErrorAction SilentlyContinue)
if ($Action -ne 'status') {
    $service = $services | Where-Object { $_.Name -ceq ('WireGuardTunnel$' + $Tunnel) } | Select-Object -First 1
    if (-not $service) { throw 'Unknown WireGuard tunnel service' }
    if ($Action -eq 'connect') { Start-Service -Name $service.Name }
    else { Stop-Service -Name $service.Name }
    @{ ok = $true } | ConvertTo-Json -Compress
    exit 0
}
$wg = Join-Path $env:ProgramFiles 'WireGuard\wg.exe'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
$tunnels = @()
foreach ($service in $services) {
    $name = $service.Name.Substring('WireGuardTunnel$'.Length)
    $received = [long]0
    $sent = [long]0
    $handshake = [long]0
    $endpoint = ''
    $telemetry = $false
    if ($service.Status -eq 'Running' -and (Test-Path -LiteralPath $wg)) {
        try {
            # Explicit public fields only: never use wg showconf or a dump with private keys.
            $rows = @(& $wg show $name transfer 2>$null)
            if ($LASTEXITCODE -eq 0) {
                $telemetry = $true
                foreach ($row in $rows) {
                    $parts = $row -split '\s+'
                    if ($parts.Count -ge 3) { $received += [long]$parts[1]; $sent += [long]$parts[2] }
                }
            }
            foreach ($row in @(& $wg show $name latest-handshakes 2>$null)) {
                $parts = $row -split '\s+'
                if ($parts.Count -ge 2) { $handshake = [Math]::Max($handshake, [long]$parts[1]) }
            }
            $endpoints = @(& $wg show $name endpoints 2>$null)
            $endpoint = ($endpoints | ForEach-Object { ($_ -split '\s+', 2)[1] }) -join ', '
        } catch {}
    }
    $addresses = @(Get-NetIPAddress -InterfaceAlias $name -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { $_.IPAddress })
    $tunnels += @{
        name = $name; active = ($service.Status -eq 'Running'); addresses = $addresses
        received = $received; sent = $sent; handshake = $handshake; endpoint = $endpoint; telemetry = $telemetry
    }
}
$routes = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | ForEach-Object {
    @{ adapter = $_.InterfaceAlias; gateway = $_.NextHop; metric = $_.RouteMetric }
})
$proxy = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
@{
    tunnels = $tunnels; routes = $routes
    elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    proxy = @{ enabled = [bool]$proxy.ProxyEnable; server = [string]$proxy.ProxyServer; pac = [bool]$proxy.AutoConfigURL }
} | ConvertTo-Json -Depth 8 -Compress
