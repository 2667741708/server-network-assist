$ErrorActionPreference = 'Stop'
$dataRoot = 'C:\Users\86133\AppData\Local\PureNetworkClient\data'
$installRoot = 'C:\Users\86133\AppData\Local\Programs\PureNetworkClient'
$desktop = 'C:\Users\86133\Desktop'
$secret = 'C:\Users\86133\Downloads\SNA-20260919\d321-subscriptions.secret.json'
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
    $body = $Value | ConvertTo-Json -Depth 6 -Compress
    $content = [Net.Http.StringContent]::new($body, [Text.Encoding]::UTF8, 'application/json')
    $response = $client.PostAsync($Path, $content).GetAwaiter().GetResult()
    $result = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) { throw "POST failed: $Path $result" }
    return $result | ConvertFrom-Json
}

$health = Get-Json '/api/health'
if ($health.revision -ne '20260919-subscription-egress-labels') { throw 'Wrong PureNetworkClient revision.' }
$null = Post-Json '/api/network/leave' @{}
$state = Get-Json '/api/state'
if ($state.active) { throw 'PureNetworkClient is unexpectedly online.' }
$rows = @($state.saved_subscriptions)
if ($rows.Count -ne 2) { throw 'PureNetworkClient must contain two subscriptions.' }
$modes = @($rows | ForEach-Object { @($_.egress_modes) } | Sort-Object -Unique)
if ($modes.Count -ne 2 -or $modes -notcontains 'physical' -or $modes -notcontains 'source_proxy') {
    throw 'PureNetworkClient subscription type metadata is incomplete.'
}

$taskName = 'PureNetworkClient-Interactive-Install'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
if (Test-Path -LiteralPath $secret -PathType Leaf) { Remove-Item -LiteralPath $secret -Force }

$oldTasks = @('LanBridge-Proxy-Test-20260918','ServerNetworkAssist-Customer-Native','ServerNetworkAssist-Client',
  'SNA-D321-Pure-Open','SNA-D321-Pure-Close')
$oldTaskCount = @($oldTasks | Where-Object { Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue }).Count
$oldPaths = @(
    'C:\Users\86133\LanBridge-proxy-test-20260918',
    'C:\ProgramData\ServerNetworkAssist\client-native'
)
$oldPathCount = @($oldPaths | Where-Object { Test-Path -LiteralPath $_ }).Count
$oldShortcutCount = @(@('纯享入网 Next.lnk','SNA Customer Client.lnk') | Where-Object {
    Test-Path -LiteralPath (Join-Path $desktop $_)
}).Count
$exe = @(Get-ChildItem -LiteralPath $installRoot -Filter 'ServerNetworkAssistClient.exe' -Recurse -File)
if ($exe.Count -ne 1) { throw 'Expected one installed PureNetworkClient executable.' }

[pscustomobject]@{
    ok = $true
    revision = $health.revision
    running_pid = $instance.pid
    active = $false
    explicit_leave_verified = $true
    subscription_count = $rows.Count
    subscription_modes = $modes
    old_tasks_remaining = $oldTaskCount
    old_paths_remaining = $oldPathCount
    old_shortcuts_remaining = $oldShortcutCount
    new_shortcut = (Test-Path -LiteralPath (Join-Path $desktop '纯享入网.lnk'))
    full_offline_dependencies = (Test-Path -LiteralPath (Join-Path $exe[0].DirectoryName 'dependencies\wireguard-amd64.msi'))
    management_tunnel = [bool](Get-NetAdapter -Name 'fleet-titan' -ErrorAction SilentlyContinue)
    other_project_preserved = (Test-Path -LiteralPath 'C:\Program Files\BorrowNetworkManager')
    secret_file_removed = -not (Test-Path -LiteralPath $secret)
} | ConvertTo-Json -Depth 5 -Compress
