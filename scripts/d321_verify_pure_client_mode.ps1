param([Parameter(Mandatory=$true)][ValidateSet('physical','source_proxy')][string]$Mode)
$ErrorActionPreference = 'Stop'
$dataRoot = 'C:\Users\86133\AppData\Local\PureNetworkClient\data'
$instance = Get-Content -LiteralPath (Join-Path $dataRoot 'client-instance.json') -Raw | ConvertFrom-Json
$origin = 'http://127.0.0.1:' + $instance.port

Add-Type -AssemblyName System.Net.Http
function New-HttpClient([bool]$ClientApi) {
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $http = [Net.Http.HttpClient]::new($handler)
    $http.Timeout = [TimeSpan]::FromSeconds(20)
    if ($ClientApi) {
        $http.BaseAddress = [Uri]$origin
        $http.DefaultRequestHeaders.Add('X-Client-Token', [string]$instance.token)
        $http.DefaultRequestHeaders.Add('Origin', $origin)
    } else {
        $http.DefaultRequestHeaders.UserAgent.ParseAdd('PureNetworkClient-D321-Verification/1')
    }
    return $http
}
$api = New-HttpClient $true

function Get-Api([string]$Path) {
    $response = $api.GetAsync($Path).GetAwaiter().GetResult()
    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) { throw "GET failed: $Path $body" }
    return $body | ConvertFrom-Json
}
function Post-Api([string]$Path, $Value) {
    $body = $Value | ConvertTo-Json -Depth 6 -Compress
    $content = [Net.Http.StringContent]::new($body, [Text.Encoding]::UTF8, 'application/json')
    $response = $api.PostAsync($Path, $content).GetAwaiter().GetResult()
    $result = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) { throw "POST failed: $Path $result" }
    return $result | ConvertFrom-Json
}
function Network-Fingerprint {
    return [ordered]@{
        defaults = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' |
            Sort-Object InterfaceIndex,NextHop,RouteMetric |
            Select-Object InterfaceIndex,NextHop,RouteMetric,InterfaceMetric)
        dns = @(Get-DnsClientServerAddress -AddressFamily IPv4 | Sort-Object InterfaceIndex |
            Select-Object InterfaceIndex,InterfaceAlias,ServerAddresses)
        winhttp = (& netsh.exe winhttp show proxy | Out-String).Trim()
    } | ConvertTo-Json -Depth 6 -Compress
}
function Test-Url([string]$Url) {
    $http = New-HttpClient $false
    try {
        $response = $http.GetAsync($Url, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        return [pscustomobject]@{url=$Url;connected=$true;status=[int]$response.StatusCode}
    } catch {
        return [pscustomobject]@{url=$Url;connected=$false;error=$_.Exception.GetBaseException().Message}
    } finally {
        $http.Dispose()
    }
}

$before = Network-Fingerprint
$state = Get-Api '/api/state'
$row = @($state.saved_subscriptions | Where-Object { @($_.egress_modes) -contains $Mode })
if ($row.Count -ne 1) { throw "Expected one saved subscription for $Mode" }
$null = Post-Api '/api/online/subscriptions/select' @{subscription_id=$row[0].id}
$catalog = Get-Api '/api/online/subscription'
$route = @($catalog.routes | Where-Object { $_.egress_mode -eq $Mode -and $_.available -ne $false })
if ($route.Count -ne 1) { throw "Expected one available route for $Mode" }

$activeTunnel = $null
$tests = @()
$connectSucceeded = $false
$leaveSucceeded = $false
try {
    $null = Post-Api '/api/online/connect' @{grant_id=$route[0].id}
    $deadline = (Get-Date).AddSeconds(40)
    do {
        Start-Sleep -Milliseconds 500
        $state = Get-Api '/api/state'
    } while (-not $state.active -and (Get-Date) -lt $deadline)
    if (-not $state.active) { throw "Client did not enter network for $Mode" }
    if ($state.active.kind -ne 'online') { throw 'Client entered an unexpected connection kind.' }
    $activeTunnel = [string]$state.active.tunnel
    $connectSucceeded = $true
    $tests += Test-Url 'https://www.baidu.com/'
    if ($Mode -eq 'source_proxy') {
        $tests += Test-Url 'https://api.github.com/'
        $tests += Test-Url 'https://www.google.com/generate_204'
        $tests += Test-Url 'https://chatgpt.com/'
    }
} finally {
    try {
        $null = Post-Api '/api/network/leave' @{}
        $deadline = (Get-Date).AddSeconds(40)
        do {
            Start-Sleep -Milliseconds 500
            $state = Get-Api '/api/state'
        } while ($state.active -and (Get-Date) -lt $deadline)
        $leaveSucceeded = -not [bool]$state.active
    } catch {
        $leaveSucceeded = $false
    }
}
if (-not $leaveSucceeded) { throw "Explicit leave did not complete for $Mode" }
if (Test-Path -LiteralPath (Join-Path $dataRoot 'customer-owned-tunnel.json')) { throw 'Owned tunnel marker remains after leave.' }
if (Test-Path -LiteralPath (Join-Path $dataRoot 'customer-active-line.json')) { throw 'Active line marker remains after leave.' }
if ($activeTunnel -and (Get-NetAdapter -Name $activeTunnel -ErrorAction SilentlyContinue)) { throw 'Owned tunnel adapter remains after leave.' }
$after = Network-Fingerprint
if ($before -ne $after) { throw "Default route, DNS, or WinHTTP proxy changed after explicit leave for $Mode" }
$internal = Test-NetConnection -ComputerName '10.20.32.13' -Port 9182 -InformationLevel Quiet
$fleet = Get-NetAdapter -Name 'fleet-titan' -ErrorAction SilentlyContinue

[pscustomobject]@{
    ok = $true
    mode = $Mode
    connect = $connectSucceeded
    explicit_leave = $leaveSucceeded
    route_dns_proxy_restored = ($before -eq $after)
    owned_tunnel_removed = $true
    internal_source_reachable_after_leave = [bool]$internal
    management_tunnel_up = ($fleet.Status -eq 'Up')
    web_tests = $tests
} | ConvertTo-Json -Depth 6 -Compress
