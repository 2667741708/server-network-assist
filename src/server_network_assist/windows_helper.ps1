param(
    [ValidateSet('bootstrap','status','prepare','configure','enable','confirm','disable','failsafe','watch','verify')]
    [string]$Action = 'status',
    [string]$Value = '',
    [string]$Extra = '',
    [switch]$FunctionsOnly
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$Base = 'C:\ProgramData\ServerNetworkAssist'
$Wg = Join-Path $env:ProgramFiles 'WireGuard\wg.exe'
$WireGuard = Join-Path $env:ProgramFiles 'WireGuard\wireguard.exe'
$Self = Join-Path $Base 'windows_helper.ps1'

function Invoke-Native([string]$Program, [string[]]$Arguments, [string]$InputText = '') {
    if ($InputText) { $output = $InputText | & $Program @Arguments 2>&1 }
    else { $output = & $Program @Arguments 2>&1 }
    if ($LASTEXITCODE -ne 0) { throw "Native operation failed: $Program (exit $LASTEXITCODE)" }
    return ($output -join "`n").Trim()
}
function Assert-Id([string]$Id) {
    if ($Id -cnotmatch '^[a-zA-Z0-9_-]{1,64}$') { throw 'Invalid profile id' }
}
function Assert-Interface([string]$Name) {
    if ($Name -cnotmatch '^na[a-f0-9]{10}$') { throw 'Invalid interface name' }
}
function Get-StatePath([string]$Id) {
    Assert-Id $Id
    return (Join-Path $Base "$Id\state.json")
}
function Read-State([string]$Id) {
    $path = Get-StatePath $Id
    $state = @{}
    if (Test-Path -LiteralPath $path) {
        $obj = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($property in $obj.PSObject.Properties) { $state[$property.Name] = $property.Value }
    }
    return $state
}
function Write-Atomic([string]$Path, [string]$Text) {
    $parent = Split-Path -Parent $Path
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $temporary = "$Path.$PID.tmp"
    [IO.File]::WriteAllText($temporary, $Text, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
function Save-State($State) {
    $State.updated_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    Write-Atomic (Get-StatePath $State.profile_id) ($State | ConvertTo-Json -Depth 12)
}
function Assert-Tools {
    if (-not (Test-Path -LiteralPath $Wg) -or -not (Test-Path -LiteralPath $WireGuard)) {
        throw 'Install WireGuard for Windows first'
    }
}
function Prepare-Key([string]$Id) {
    Assert-Id $Id
    Assert-Tools
    $path = Join-Path $Base "$Id\private.key"
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Atomic $path (Invoke-Native $Wg @('genkey'))
    }
    $private = Get-Content -LiteralPath $path -Raw
    return (Invoke-Native $Wg @('pubkey') $private)
}
function Remove-Task([string]$Name) {
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    }
}
function Add-Task($State, [string]$Kind, [int]$Seconds, [string]$Token = '') {
    $name = "ServerNetworkAssist-$Kind-$($State.interface)"
    $arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Self`" $Kind $($State.profile_id) $Token"
    $actionSpec = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
    if ($Kind -eq 'watch') {
        $trigger = @(
            (New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(60) -RepetitionInterval (New-TimeSpan -Minutes 1)),
            (New-ScheduledTaskTrigger -AtStartup)
        )
    } else { $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds($Seconds) }
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
    Register-ScheduledTask -TaskName $name -Action $actionSpec -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
}
function Resolve-Targets($Values) {
    $targets = @()
    foreach ($target in $Values) {
        if ($target -match '^([0-9.]+)/([0-9]+)$') {
            $address = [Net.IPAddress]::Parse($Matches[1])
            $prefix = [int]$Matches[2]
            if ($address.AddressFamily -ne 'InterNetwork' -or $prefix -lt 2 -or $prefix -gt 32) { throw 'Invalid preserved IPv4 network' }
            $targets += $target
        } else {
            foreach ($ip in [Net.Dns]::GetHostAddresses($target)) {
                if ($ip.AddressFamily -eq 'InterNetwork') { $targets += "$ip/32" }
            }
        }
    }
    return @($targets | Select-Object -Unique)
}
function Preserve-Routes($State) {
    foreach ($target in @(Resolve-Targets (@($State.preserve_routes) + @($State.endpoint)))) {
        if (Get-NetRoute -AddressFamily IPv4 -DestinationPrefix $target -ErrorAction SilentlyContinue) { continue }
        $ip = $target.Split('/')[0]
        $route = Find-NetRoute -RemoteIPAddress $ip | Where-Object { $_.PSObject.Properties['NextHop'] } | Select-Object -First 1
        if (-not $route) { throw "No original route to $target" }
        $entry = @{ prefix = $target; index = $route.InterfaceIndex; hop = $route.NextHop }
        # Journal before mutation so partial failures can restore every owned route.
        $State.added_routes = @($State.added_routes) + @($entry)
        Save-State $State
        New-NetRoute -DestinationPrefix $target -InterfaceIndex $entry.index -NextHop $entry.hop -RouteMetric 5 -PolicyStore ActiveStore | Out-Null
    }
}
function Get-GatewayUplink {
    $route = Find-NetRoute -RemoteIPAddress '1.1.1.1' | Where-Object { $_.PSObject.Properties['NextHop'] } | Select-Object -First 1
    if (-not $route -or $route.InterfaceAlias -like 'na*') { throw 'No independent IPv4 uplink found' }
    return $route.InterfaceIndex
}
function Assert-GatewayAvailable($State) {
    foreach ($command in @('Get-NetNat','New-NetNat','Remove-NetNat','Set-NetIPInterface','New-NetFirewallRule')) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Windows gateway requires $command (WinNAT)" }
    }
    # WinNAT supports one internal prefix. Never delete or reuse someone else's NAT.
    if (@(Get-NetNat -ErrorAction Stop).Count) { throw 'An existing WinNAT is present. Use a separate gateway; existing WSL/Docker/Hyper-V NATs are not modified.' }
    if (@(Get-NetFirewallRule -Name "SNA-$($State.interface)-*" -ErrorAction SilentlyContinue).Count) { throw 'Gateway firewall names are already in use; retry cleanup first' }
    Get-GatewayUplink | Out-Null
}
function Enable-Gateway($State) {
    $tunnel = Get-NetIPInterface -InterfaceAlias $State.interface -AddressFamily IPv4 -ErrorAction Stop
    $uplink = Get-NetIPInterface -InterfaceIndex (Get-GatewayUplink) -AddressFamily IPv4 -ErrorAction Stop
    foreach ($adapter in @($tunnel, $uplink)) {
        $State.forwarding = @($State.forwarding) + @(@{ index = $adapter.InterfaceIndex; value = [string]$adapter.Forwarding })
        Save-State $State
        Set-NetIPInterface -InterfaceIndex $adapter.InterfaceIndex -AddressFamily IPv4 -Forwarding Enabled
    }
    $State.nat_owned = $true
    Save-State $State
    New-NetNat -Name "SNA-$($State.interface)" -InternalIPInterfaceAddressPrefix $State.subnet | Out-Null
    $State.firewall_owned = $true
    Save-State $State
    New-NetFirewallRule -Name "SNA-$($State.interface)-udp" -DisplayName "SNA $($State.interface) WireGuard" -Direction Inbound -Action Allow -Protocol UDP -LocalPort $State.port -Profile Any | Out-Null
    New-NetFirewallRule -Name "SNA-$($State.interface)-tunnel" -DisplayName "SNA $($State.interface) tunnel" -Direction Inbound -Action Allow -InterfaceAlias $State.interface -RemoteAddress $State.subnet -Profile Any | Out-Null
    if ($State.proxy_mode -eq 'share') {
        $listen = $State.address.Split('/')[0]
        if (Get-PortProxyMapping $State) { throw 'Proxy relay mapping already exists; finish cleanup first' }
        if (Get-NetTCPConnection -LocalAddress $listen -LocalPort $State.relay_port -State Listen -ErrorAction SilentlyContinue) { throw 'Proxy relay port already in use' }
        $State.relay_owned = $true
        Save-State $State
        Start-Service -Name iphlpsvc
        Invoke-Native 'netsh.exe' @('interface','portproxy','add','v4tov4',"listenaddress=$listen","listenport=$($State.relay_port)","connectaddress=$($State.proxy_host)","connectport=$($State.proxy_port)",'protocol=tcp') | Out-Null
    }
}
function Get-PortProxyMapping($State) {
    $settings = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\PortProxy\v4tov4\tcp' -ErrorAction SilentlyContinue
    if (-not $settings) { return $null }
    $entry = $settings.PSObject.Properties["$($State.address.Split('/')[0])/$($State.relay_port)"]
    if ($entry) { return [string]$entry.Value }
    return $null
}
function Restore-Gateway($State) {
    if ($State.relay_owned) {
        $mapping = Get-PortProxyMapping $State
        if ($mapping) {
            if ($mapping -ne "$($State.proxy_host)/$($State.proxy_port)") { throw 'Owned proxy mapping changed; refusing to remove it' }
            Invoke-Native 'netsh.exe' @('interface','portproxy','delete','v4tov4',"listenaddress=$($State.address.Split('/')[0])","listenport=$($State.relay_port)") | Out-Null
        }
        $State.relay_owned = $false
        Save-State $State
    }
    if ($State.nat_owned) {
        $nat = Get-NetNat -Name "SNA-$($State.interface)" -ErrorAction SilentlyContinue
        if ($nat) {
            if ([string]$nat.InternalIPInterfaceAddressPrefix -ne $State.subnet) { throw 'Owned NAT changed; refusing to remove it' }
            $nat | Remove-NetNat -Confirm:$false
        }
        $State.nat_owned = $false
        Save-State $State
    }
    if ($State.firewall_owned) {
        Get-NetFirewallRule -Name "SNA-$($State.interface)-*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        $State.firewall_owned = $false
        Save-State $State
    }
    foreach ($entry in @($State.forwarding)) {
        if (Get-NetIPInterface -InterfaceIndex $entry.index -AddressFamily IPv4 -ErrorAction SilentlyContinue) {
            Set-NetIPInterface -InterfaceIndex $entry.index -AddressFamily IPv4 -Forwarding $entry.value
        }
    }
    $State.forwarding = @()
    Save-State $State
}
function Notify-ProxyChange {
    if (-not ('SnaInternetOptions' -as [type])) {
        Add-Type 'using System; using System.Runtime.InteropServices; public static class SnaInternetOptions { [DllImport("wininet.dll")] public static extern bool InternetSetOption(IntPtr h, int option, IntPtr buffer, int length); }'
    }
    [SnaInternetOptions]::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0) | Out-Null
    [SnaInternetOptions]::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0) | Out-Null
}
function Set-ClientProxy($State) {
    if ($State.proxy_mode -ne 'share') { return }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $key = "Registry::HKEY_USERS\$sid\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    $settings = Get-ItemProperty -LiteralPath $key -ErrorAction Stop
    $backup = @{}
    foreach ($name in @('ProxyEnable','ProxyServer','ProxyOverride','AutoConfigURL')) {
        $property = $settings.PSObject.Properties[$name]
        $backup[$name] = @{ present = [bool]$property; value = $(if ($property) { $property.Value } else { $null }) }
    }
    $State.proxy_backup = @{ key = $key; values = $backup }
    Save-State $State
    Set-ItemProperty -LiteralPath $key -Name ProxyServer -Value "$($State.gateway_ip):$($State.relay_port)"
    Set-ItemProperty -LiteralPath $key -Name ProxyOverride -Value '<local>'
    Set-ItemProperty -LiteralPath $key -Name ProxyEnable -Type DWord -Value 1
    Remove-ItemProperty -LiteralPath $key -Name AutoConfigURL -ErrorAction SilentlyContinue
    Notify-ProxyChange
}
function Restore-ClientProxy($State) {
    if (-not $State.proxy_backup) { return }
    $key = $State.proxy_backup.key
    if (-not (Test-Path -LiteralPath $key)) { throw 'Original user registry hive is not loaded; log in as the original SSH user and retry cleanup' }
    foreach ($name in @('ProxyEnable','ProxyServer','ProxyOverride','AutoConfigURL')) {
        $entry = $State.proxy_backup.values.$name
        if ($entry.present) { Set-ItemProperty -LiteralPath $key -Name $name -Value $entry.value }
        else { Remove-ItemProperty -LiteralPath $key -Name $name -ErrorAction SilentlyContinue }
    }
    Notify-ProxyChange
    $State.proxy_backup = $null
    Save-State $State
}
function Test-Proxy($State) {
    $proxyHost = $State.gateway_ip
    $proxyPort = $State.relay_port
    if ($State.role -eq 'gateway') { $proxyHost = $State.proxy_host; $proxyPort = $State.proxy_port }
    foreach ($url in @('https://connectivitycheck.gstatic.com/generate_204','https://www.baidu.com')) {
        $response = $null
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $request = [Net.HttpWebRequest]::Create($url)
            $request.Proxy = [Net.WebProxy]::new("http://${proxyHost}:$proxyPort")
            $request.Timeout = 6000
            $request.AllowAutoRedirect = $false
            $response = $request.GetResponse()
            if ([int]$response.StatusCode -in @(200,204)) { return $true }
        } catch {} finally { if ($response) { $response.Dispose() } }
    }
    return $false
}
function Disable-Profile([string]$Id, [bool]$Suspended = $false) {
    $state = Read-State $Id
    if (-not $state.Count) { return }
    Assert-Interface $state.interface
    $state.desired = $false
    Save-State $state
    $cleanupErrors = @()
    try { Remove-Task "ServerNetworkAssist-watch-$($state.interface)" } catch { $cleanupErrors += $_.Exception.Message }
    try { Remove-Task "ServerNetworkAssist-failsafe-$($state.interface)" } catch { $cleanupErrors += $_.Exception.Message }
    try {
        if ($state.role -eq 'gateway') { Restore-Gateway $state }
        else { Restore-ClientProxy $state }
    } catch { $cleanupErrors += $_.Exception.Message }
    $service = Get-Service -Name ('WireGuardTunnel$' + $state.interface) -ErrorAction SilentlyContinue
    if ($service) {
        try { Invoke-Native $WireGuard @('/uninstalltunnelservice', $state.interface) | Out-Null }
        catch { $cleanupErrors += $_.Exception.Message }
    }
    $remainingRoutes = @()
    foreach ($route in @($state.added_routes)) {
        try {
            $existing = Get-NetRoute -DestinationPrefix $route.prefix -InterfaceIndex $route.index -NextHop $route.hop -PolicyStore ActiveStore -ErrorAction SilentlyContinue
            if ($existing) { $existing | Remove-NetRoute -Confirm:$false }
        } catch { $remainingRoutes += $route; $cleanupErrors += $_.Exception.Message }
    }
    $state.added_routes = $remainingRoutes
    $state.active = (Get-Service -Name ('WireGuardTunnel$' + $state.interface) -ErrorAction SilentlyContinue).Status -eq 'Running'
    $state.suspended = $Suspended
    $state.failsafe_token = ''
    Save-State $state
    if ($cleanupErrors.Count) { throw ('Rollback incomplete; retry disable: ' + ($cleanupErrors -join '; ')) }
}
function Test-Tunnel($State) {
    $adapter = Get-NetIPAddress -InterfaceAlias $State.interface -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $adapter) { return $false }
    if ($State.proxy_mode -eq 'share') { return (Test-Proxy $State) }
    if ($State.role -eq 'gateway') { return [bool](Get-NetNat -Name "SNA-$($State.interface)" -ErrorAction SilentlyContinue) }
    foreach ($target in @('1.1.1.1','223.5.5.5')) {
        $client = [Net.Sockets.TcpClient]::new([Net.IPEndPoint]::new([Net.IPAddress]::Parse($adapter.IPAddress), 0))
        try {
            $pending = $client.BeginConnect($target, 443, $null, $null)
            if ($pending.AsyncWaitHandle.WaitOne(4000)) { $client.EndConnect($pending); return $true }
        } catch {} finally { $client.Dispose() }
    }
    return $false
}

if ($FunctionsOnly) { return }

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run through an elevated Administrator SSH account' }
    if ($Action -eq 'bootstrap') {
        Assert-Tools
        [IO.Directory]::CreateDirectory($Base) | Out-Null
        Invoke-Native 'icacls.exe' @($Base, '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F') | Out-Null
        @{ ok = $true; helper_version = 2; platform = 'windows'; roles = @('client','gateway') } | ConvertTo-Json -Compress
        exit 0
    }
    if ($Action -eq 'status') {
        $values = @()
        foreach ($file in @(Get-ChildItem -LiteralPath $Base -Filter state.json -Recurse -ErrorAction SilentlyContinue)) {
            $state = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            $active = (Get-Service -Name ('WireGuardTunnel$' + $state.interface) -ErrorAction SilentlyContinue).Status -eq 'Running'
            $values += @{ profile_id = $state.profile_id; interface = $state.interface; role = $state.role; desired = $state.desired; active = $active; suspended = $state.suspended; failures = $state.failures }
        }
        @{ ok = $true; assist = $values } | ConvertTo-Json -Depth 8 -Compress
        exit 0
    }
    if ($Action -eq 'configure') {
        $encoded = $Value.Replace('-', '+').Replace('_', '/')
        while ($encoded.Length % 4) { $encoded += '=' }
        $payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded)) | ConvertFrom-Json
        if ($payload.version -ne 1 -or $payload.role -notin @('client','gateway')) { throw 'Unsupported configuration' }
        Assert-Id $payload.profile_id
        Assert-Interface $payload.interface
        if ($payload.address -notmatch '^([0-9.]+)/(\d+)$') { throw 'Invalid tunnel address' }
        $ip = [Net.IPAddress]::Parse($Matches[1])
        if ($ip.AddressFamily -ne 'InterNetwork' -or [int]$Matches[2] -gt 32) { throw 'Invalid IPv4 address' }
        if ($payload.role -eq 'client') {
            if ($payload.endpoint -notmatch '^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$') { throw 'Invalid endpoint' }
            if ($payload.gateway_public_key -cnotmatch '^[A-Za-z0-9+/]{43}=$') { throw 'Invalid public key' }
        }
        if ($payload.proxy_mode -and $payload.proxy_mode -notin @('direct','share')) { throw 'Invalid proxy mode' }
        if ($payload.proxy_mode -eq 'share') {
            $proxyIp = [Net.IPAddress]::Parse($payload.proxy_host)
            if ($proxyIp.AddressFamily -ne 'InterNetwork' -or $proxyIp.Equals([Net.IPAddress]::Any)) { throw 'Invalid proxy IPv4 address' }
            if ([int]$payload.proxy_port -lt 1 -or [int]$payload.proxy_port -gt 65535 -or [int]$payload.relay_port -ne 17897) { throw 'Invalid proxy port' }
        }
        if ([int]$payload.port -lt 1024 -or [int]$payload.port -gt 65535) { throw 'Invalid port' }
        $old = Read-State $payload.profile_id
        if ($old.desired -or $old.active -or $old.proxy_backup -or $old.nat_owned -or $old.relay_owned -or $old.added_routes -or $old.forwarding -or $old.firewall_owned) { throw 'Disable profile and finish cleanup before reconfiguration' }
        if ($payload.role -eq 'client') { Resolve-Targets (@($payload.preserve_routes) + @($payload.endpoint)) | Out-Null }
        Prepare-Key $payload.profile_id | Out-Null
        $private = Get-Content -LiteralPath (Join-Path $Base "$($payload.profile_id)\private.key") -Raw
        $configLines = @('[Interface]', "PrivateKey = $($private.Trim())", "Address = $($payload.address)")
        if ($payload.role -eq 'gateway') {
            if ($payload.subnet -notmatch '^([0-9.]+)/(\d+)$' -or [int]$Matches[2] -lt 16 -or [int]$Matches[2] -gt 27) { throw 'Invalid gateway subnet' }
            $subnetBytes = [Net.IPAddress]::Parse($Matches[1]).GetAddressBytes()
            $prefix = [int]$Matches[2]
            $configLines += "ListenPort = $($payload.port)"
            if (@($payload.peers).Count -lt 1 -or @($payload.peers).Count -gt 32) { throw 'Invalid gateway peer count' }
            foreach ($peer in $payload.peers) {
                if ($peer.public_key -cnotmatch '^[A-Za-z0-9+/]{43}=$' -or $peer.allowed_ip -notmatch '^([0-9.]+)/32$') { throw 'Invalid gateway peer' }
                $peerBytes = [Net.IPAddress]::Parse($Matches[1]).GetAddressBytes()
                for ($bit = 0; $bit -lt $prefix; $bit++) {
                    $mask = 1 -shl (7 - ($bit % 8))
                    $offset = [int][Math]::Floor($bit / 8)
                    if (($subnetBytes[$offset] -band $mask) -ne ($peerBytes[$offset] -band $mask)) { throw 'Peer is outside gateway subnet' }
                }
                $configLines += @('', '[Peer]', "PublicKey = $($peer.public_key)", "AllowedIPs = $($peer.allowed_ip)")
            }
        } else {
            $configLines += @('DNS = 223.5.5.5, 1.1.1.1', '', '[Peer]', "PublicKey = $($payload.gateway_public_key)", "Endpoint = $($payload.endpoint):$($payload.port)", 'AllowedIPs = 0.0.0.0/1, 128.0.0.0/1', 'PersistentKeepalive = 25')
        }
        $config = $configLines -join "`r`n"
        Write-Atomic (Join-Path $Base "$($payload.profile_id)\$($payload.interface).conf") $config
        $state = @{}
        foreach ($property in $payload.PSObject.Properties) { $state[$property.Name] = $property.Value }
        $state.desired = $false; $state.active = $false; $state.added_routes = @(); $state.failures = 0; $state.forwarding = @()
        Save-State $state
    } else {
        Assert-Id $Value
        if ($Action -eq 'prepare') {
            @{ ok = $true; public_key = (Prepare-Key $Value) } | ConvertTo-Json -Compress
            exit 0
        }
        $state = Read-State $Value
        if ($Action -eq 'disable') { Disable-Profile $Value }
        elseif (-not $state.Count) { throw 'Profile is not configured' }
        elseif ($Action -eq 'enable') {
            Assert-Tools
            Assert-Interface $state.interface
            if ($state.active -or $state.desired) { throw 'Profile is already enabled' }
            if ($state.role -eq 'gateway') { Assert-GatewayAvailable $state }
            else {
                $conflicts = @(Get-NetRoute -AddressFamily IPv4 -ErrorAction Stop | Where-Object { $_.DestinationPrefix -in @('0.0.0.0/1','128.0.0.0/1') })
                if ($conflicts.Count) { throw 'Another full-tunnel route is active; disconnect it before enabling this profile' }
            }
            if ($state.role -eq 'gateway' -and $state.proxy_mode -eq 'share' -and -not (Test-Proxy $state)) { throw 'Source HTTP proxy verification failed' }
            try {
                if ($state.role -eq 'client') {
                    $state.failsafe_token = [Guid]::NewGuid().ToString('N')
                    Save-State $state
                    Add-Task $state 'failsafe' 120 $state.failsafe_token
                }
                if ($state.role -eq 'client') { Preserve-Routes $state }
                if ($state.maintenance) { Add-Task $state 'watch' 60 }
                Invoke-Native $WireGuard @('/installtunnelservice', (Join-Path $Base "$Value\$($state.interface).conf")) | Out-Null
                # Restore control routes before starting the tunnel after reboot.
                Set-Service -Name ('WireGuardTunnel$' + $state.interface) -StartupType Manual
                if ($state.role -eq 'gateway') { Enable-Gateway $state }
                else { Set-ClientProxy $state }
                $state.desired = $true; $state.active = $true; $state.failures = 0
                Save-State $state
            } catch { Disable-Profile $Value $true; throw }
        }
        elseif ($Action -eq 'verify') {
            if ($state.role -eq 'gateway' -and $state.proxy_mode -eq 'share') { $verified = Test-Proxy $state }
            else { $verified = Test-Tunnel $state }
            if (-not $verified) { throw 'Selected network/proxy path verification failed' }
        }
        elseif ($Action -eq 'confirm') {
            if (-not $state.desired -or -not (Test-Tunnel $state)) { throw 'Tunnel verification failed' }
            $state.failsafe_token = ''
            Save-State $state
            Remove-Task "ServerNetworkAssist-failsafe-$($state.interface)"
        }
        elseif ($Action -eq 'failsafe') {
            if ($state.failsafe_token -and $state.failsafe_token -eq $Extra) { Disable-Profile $Value $true }
        }
        elseif ($Action -eq 'watch' -and $state.desired) {
            if ($state.failsafe_token) { exit 0 }
            $serviceName = 'WireGuardTunnel$' + $state.interface
            $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            if ($service -and $service.Status -ne 'Running') {
                try {
                    if ($state.role -eq 'client') { Preserve-Routes $state }
                    Start-Service -Name $serviceName
                    if ($state.role -eq 'gateway') {
                        # Rebuild only our journaled resources after reboot/service restart.
                        Restore-Gateway $state
                        Assert-GatewayAvailable $state
                        Enable-Gateway $state
                    }
                } catch {
                    Disable-Profile $Value $true
                    throw
                }
            }
            if (Test-Tunnel $state) { $state.failures = 0 }
            else { $state.failures = [int]$state.failures + 1 }
            Save-State $state
            if ($state.failures -ge 6) { Disable-Profile $Value $true }
        }
    }
    @{ ok = $true } | ConvertTo-Json -Compress
} catch {
    @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
}
