param(
    [ValidateSet('bootstrap','status','prepare','configure','enable','confirm','disable','failsafe','watch')]
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
function Disable-Profile([string]$Id, [bool]$Suspended = $false) {
    $state = Read-State $Id
    if (-not $state.Count) { return }
    Assert-Interface $state.interface
    $state.desired = $false
    Save-State $state
    Remove-Task "ServerNetworkAssist-watch-$($state.interface)"
    Remove-Task "ServerNetworkAssist-failsafe-$($state.interface)"
    $service = Get-Service -Name ('WireGuardTunnel$' + $state.interface) -ErrorAction SilentlyContinue
    if ($service) {
        Invoke-Native $WireGuard @('/uninstalltunnelservice', $state.interface) | Out-Null
    }
    foreach ($route in @($state.added_routes)) {
        $existing = Get-NetRoute -DestinationPrefix $route.prefix -InterfaceIndex $route.index -NextHop $route.hop -PolicyStore ActiveStore -ErrorAction SilentlyContinue
        if ($existing) { $existing | Remove-NetRoute -Confirm:$false }
    }
    $state.added_routes = @()
    $state.active = $false
    $state.suspended = $Suspended
    $state.failsafe_token = ''
    Save-State $state
}
function Test-Tunnel($State) {
    $adapter = Get-NetIPAddress -InterfaceAlias $State.interface -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $adapter) { return $false }
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
        @{ ok = $true; helper_version = 1; platform = 'windows'; roles = @('client') } | ConvertTo-Json -Compress
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
        if ($payload.version -ne 1 -or $payload.role -ne 'client') { throw 'Windows supports the client role; use Ubuntu as gateway' }
        Assert-Id $payload.profile_id
        Assert-Interface $payload.interface
        if ($payload.address -notmatch '^([0-9.]+)/(\d+)$') { throw 'Invalid tunnel address' }
        $ip = [Net.IPAddress]::Parse($Matches[1])
        if ($ip.AddressFamily -ne 'InterNetwork' -or [int]$Matches[2] -gt 32) { throw 'Invalid IPv4 address' }
        if ($payload.endpoint -notmatch '^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$') { throw 'Invalid endpoint' }
        if ($payload.gateway_public_key -cnotmatch '^[A-Za-z0-9+/]{43}=$') { throw 'Invalid public key' }
        if ([int]$payload.port -lt 1024 -or [int]$payload.port -gt 65535) { throw 'Invalid port' }
        $old = Read-State $payload.profile_id
        if ($old.desired -or $old.active) { throw 'Disable profile before reconfiguration' }
        Resolve-Targets (@($payload.preserve_routes) + @($payload.endpoint)) | Out-Null
        Prepare-Key $payload.profile_id | Out-Null
        $private = Get-Content -LiteralPath (Join-Path $Base "$($payload.profile_id)\private.key") -Raw
        $config = @('[Interface]', "PrivateKey = $($private.Trim())", "Address = $($payload.address)", 'DNS = 223.5.5.5, 1.1.1.1', '', '[Peer]', "PublicKey = $($payload.gateway_public_key)", "Endpoint = $($payload.endpoint):$($payload.port)", 'AllowedIPs = 0.0.0.0/1, 128.0.0.0/1', 'PersistentKeepalive = 25') -join "`r`n"
        Write-Atomic (Join-Path $Base "$($payload.profile_id)\$($payload.interface).conf") $config
        $state = @{}
        foreach ($property in $payload.PSObject.Properties) { $state[$property.Name] = $property.Value }
        $state.desired = $false; $state.active = $false; $state.added_routes = @(); $state.failures = 0
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
            $conflicts = @(Get-NetRoute -AddressFamily IPv4 -ErrorAction Stop | Where-Object { $_.DestinationPrefix -in @('0.0.0.0/1','128.0.0.0/1') })
            if ($conflicts.Count) { throw 'Another full-tunnel route is active; disconnect it before enabling this profile' }
            try {
                $state.failsafe_token = [Guid]::NewGuid().ToString('N')
                Save-State $state
                Add-Task $state 'failsafe' 120 $state.failsafe_token
                Preserve-Routes $state
                if ($state.maintenance) { Add-Task $state 'watch' 60 }
                Invoke-Native $WireGuard @('/installtunnelservice', (Join-Path $Base "$Value\$($state.interface).conf")) | Out-Null
                # Restore control routes before starting the tunnel after reboot.
                Set-Service -Name ('WireGuardTunnel$' + $state.interface) -StartupType Manual
                $state.desired = $true; $state.active = $true; $state.failures = 0
                Save-State $state
            } catch { Disable-Profile $Value $true; throw }
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
                    Preserve-Routes $state
                    Start-Service -Name $serviceName
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
