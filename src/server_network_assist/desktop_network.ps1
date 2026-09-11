param(
    [ValidateSet('status','connect','disconnect','pause','restore-startup')][string]$Action = 'status',
    [string]$Tunnel = '',
    [ValidateSet('Automatic','Manual','Disabled')][string]$StartMode = 'Manual',
    [ValidateSet(0,1)][int]$Delayed = 0,
    [ValidateSet(0,1)][int]$WasActive = 0
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$services = @(Get-Service -Name 'WireGuardTunnel$*' -ErrorAction SilentlyContinue)
if ($Action -ne 'status') {
    $service = $services | Where-Object { $_.Name -ceq ('WireGuardTunnel$' + $Tunnel) } | Select-Object -First 1
    if (-not $service) { throw 'Unknown WireGuard tunnel service' }
    if ($Action -eq 'connect') { Start-Service -Name $service.Name }
    elseif ($Action -eq 'pause') {
        Set-Service -Name $service.Name -StartupType Disabled
        Stop-Service -Name $service.Name
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(15))
    }
    elseif ($Action -eq 'restore-startup') {
        Set-Service -Name $service.Name -StartupType $StartMode
        $key = 'HKLM:\SYSTEM\CurrentControlSet\Services\' + $service.Name
        Set-ItemProperty -LiteralPath $key -Name DelayedAutoStart -Value $Delayed -Type DWord
        if ($WasActive -eq 1) { Start-Service -Name $service.Name }
        else { Stop-Service -Name $service.Name }
        $wanted = if ($WasActive -eq 1) { 'Running' } else { 'Stopped' }
        $service.WaitForStatus($wanted, [TimeSpan]::FromSeconds(15))
    }
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
    $startupKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\' + $service.Name
    $startup = Get-ItemProperty -LiteralPath $startupKey
    $startMode = switch ($startup.Start) { 2 { 'Automatic' } 3 { 'Manual' } 4 { 'Disabled' } default { 'Unknown' } }
    $tunnels += @{
        name = $name; active = ($service.Status -eq 'Running'); addresses = $addresses
        received = $received; sent = $sent; handshake = $handshake; endpoint = $endpoint; telemetry = $telemetry
        start_mode = $startMode; delayed = [bool]$startup.DelayedAutoStart
        service_state = [string]$service.Status
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
