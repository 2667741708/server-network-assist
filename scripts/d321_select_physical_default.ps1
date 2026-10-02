$ErrorActionPreference = 'Stop'
$dataRoot = 'C:\Users\86133\AppData\Local\PureNetworkClient\data'
$instance = Get-Content -LiteralPath (Join-Path $dataRoot 'client-instance.json') -Raw | ConvertFrom-Json
$origin = 'http://127.0.0.1:' + $instance.port
Add-Type -AssemblyName System.Net.Http
$handler = [Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [Net.Http.HttpClient]::new($handler)
$client.BaseAddress = [Uri]$origin
$client.DefaultRequestHeaders.Add('X-Client-Token', [string]$instance.token)
$client.DefaultRequestHeaders.Add('Origin', $origin)
function Get-Json([string]$Path) {
    $response = $client.GetAsync($Path).GetAwaiter().GetResult()
    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) { throw "GET failed: $Path" }
    return $body | ConvertFrom-Json
}
function Post-Json([string]$Path, $Value) {
    $content = [Net.Http.StringContent]::new(($Value | ConvertTo-Json -Compress), [Text.Encoding]::UTF8, 'application/json')
    $response = $client.PostAsync($Path, $content).GetAwaiter().GetResult()
    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) { throw "POST failed: $Path $body" }
    return $body | ConvertFrom-Json
}
$null = Post-Json '/api/network/leave' @{}
$state = Get-Json '/api/state'
$physical = @($state.saved_subscriptions | Where-Object { @($_.egress_modes) -contains 'physical' })
if ($physical.Count -ne 1) { throw 'Expected one physical subscription.' }
$null = Post-Json '/api/online/subscriptions/select' @{subscription_id=$physical[0].id}
$catalog = Get-Json '/api/online/subscription'
$state = Get-Json '/api/state'
if ($state.active) { throw 'Client must finish out of network.' }
$selected = @($state.saved_subscriptions | Where-Object selected)
if ($selected.Count -ne 1 -or @($selected[0].egress_modes) -notcontains 'physical') {
    throw 'Physical subscription was not selected.'
}
[pscustomobject]@{
    ok=$true
    selected='physical'
    active=$false
    route_count=@($catalog.routes).Count
    type_label_ready=(@($selected[0].egress_modes) -contains 'physical')
} | ConvertTo-Json -Compress
