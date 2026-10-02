param(
    [Parameter(Mandatory=$true)][string]$PackagePath,
    [Parameter(Mandatory=$true)][string]$EnrollmentFile
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$expectedZip = 'eca245d8595047c6b6a89c62b4e93c9599f0d2c97c234b54014770f0d0cf7858'
$expectedExe = '152d9b0d215ac38538815849dc6a81c114078a3e930a189dbdbec82aba1c9734'
$profileRoot = 'C:\Users\86133'
$localAppData = Join-Path $profileRoot 'AppData\Local'
$installRoot = Join-Path $localAppData 'Programs\PureNetworkClient'
$dataRoot = Join-Path $localAppData 'PureNetworkClient\data'
$workRoot = Join-Path $localAppData 'PureNetworkClient-Install-20260919'
$desktop = Join-Path $profileRoot 'Desktop'
if (-not (Test-Path -LiteralPath $desktop -PathType Container)) {
    throw 'D321 interactive user desktop is unavailable.'
}
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value

function Assert-Elevated {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'D321 installation must run elevated.'
    }
}

function Remove-ExactDirectory([string]$Path, [string[]]$Allowed) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $approved = $Allowed | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') }
    if ($approved -notcontains $full) { throw "Refusing unapproved recursive removal: $full" }
    if (Test-Path -LiteralPath $full -PathType Container) {
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

function Snapshot-Network([string]$Path) {
    $snapshot = [ordered]@{
        routes = @(Get-NetRoute -AddressFamily IPv4 | Sort-Object InterfaceIndex,DestinationPrefix,NextHop,RouteMetric |
            Select-Object InterfaceIndex,DestinationPrefix,NextHop,RouteMetric,PolicyStore)
        dns = @(Get-DnsClientServerAddress -AddressFamily IPv4 | Sort-Object InterfaceIndex |
            Select-Object InterfaceIndex,InterfaceAlias,ServerAddresses)
        adapters = @(Get-NetAdapter | Sort-Object ifIndex | Select-Object Name,ifIndex,Status,MacAddress)
        winhttp = (& netsh.exe winhttp show proxy | Out-String)
    }
    $snapshot | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function New-Client([string]$Origin, [string]$Token) {
    Add-Type -AssemblyName System.Net.Http
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.BaseAddress = [Uri]$Origin
    $client.DefaultRequestHeaders.Add('X-Client-Token', $Token)
    $client.DefaultRequestHeaders.Add('Origin', $Origin)
    return $client
}

function Invoke-GetJson($Client, [string]$Path) {
    $response = $Client.GetAsync($Path).GetAwaiter().GetResult()
    $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) { throw "Client GET failed: $Path $($response.StatusCode)" }
    return $content | ConvertFrom-Json
}

function Invoke-PostJson($Client, [string]$Path, $Body) {
    $json = $Body | ConvertTo-Json -Depth 8 -Compress
    $payload = [Net.Http.StringContent]::new($json, [Text.Encoding]::UTF8, 'application/json')
    $response = $Client.PostAsync($Path, $payload).GetAwaiter().GetResult()
    $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) { throw "Client POST failed: $Path $($response.StatusCode) $content" }
    return $content | ConvertFrom-Json
}

Assert-Elevated
if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) { throw 'Full offline package is missing.' }
if (-not (Test-Path -LiteralPath $EnrollmentFile -PathType Leaf)) { throw 'Private enrollment file is missing.' }
if ((Get-FileHash -LiteralPath $PackagePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedZip) {
    throw 'Full offline package hash mismatch.'
}

$oldProgram = $installRoot
$oldNext = Join-Path $profileRoot 'LanBridge-proxy-test-20260918'
$oldNative = Join-Path $env:ProgramData 'ServerNetworkAssist\client-native'
$oldCustomer = Join-Path $env:ProgramData ("ServerNetworkAssist\client\" + $sid)
$oldData = Join-Path $localAppData 'PureNetworkClient'
$allowedRemove = @($oldProgram, $oldNext, $oldNative, $oldCustomer, $oldData, $workRoot)

Remove-ExactDirectory $workRoot $allowedRemove
New-Item -ItemType Directory -Path $workRoot | Out-Null
Snapshot-Network (Join-Path $workRoot 'baseline-before.json')

$ownedAdapters = @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -like 'SNA Customer*' -or $_.Name -like 'LanBridge*' -or $_.InterfaceDescription -like 'SNA Customer*'
})
if ($ownedAdapters.Count -gt 0) { throw 'Old customer tunnel still exists; refusing removal before recovery.' }

$knownRoots = @($oldProgram, $oldNext, $oldNative)
Get-CimInstance Win32_Process | ForEach-Object {
    $process = $_
    $known = @($knownRoots | Where-Object {
        $process.ExecutablePath -and $process.ExecutablePath.StartsWith($_, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($known.Count -gt 0) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }
}

@('LanBridge-Proxy-Test-20260918','ServerNetworkAssist-Customer-Native','ServerNetworkAssist-Client',
  'SNA-D321-Pure-Open','SNA-D321-Pure-Close') | ForEach-Object {
    if (Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $_ -Confirm:$false
    }
}

@('纯享入网.lnk','纯享入网 Next.lnk','SNA Customer Client.lnk') | ForEach-Object {
    $shortcut = Join-Path $desktop $_
    if (Test-Path -LiteralPath $shortcut -PathType Leaf) { Remove-Item -LiteralPath $shortcut -Force }
}

Remove-ExactDirectory $oldProgram $allowedRemove
Remove-ExactDirectory $oldNext $allowedRemove
Remove-ExactDirectory $oldNative $allowedRemove
Remove-ExactDirectory $oldCustomer $allowedRemove
Remove-ExactDirectory $oldData $allowedRemove
$expanded = Join-Path $workRoot 'expanded'
Expand-Archive -LiteralPath $PackagePath -DestinationPath $expanded -Force
$clientExe = @(Get-ChildItem -LiteralPath $expanded -Filter 'ServerNetworkAssistClient.exe' -Recurse -File)
if ($clientExe.Count -ne 1) { throw 'Full offline package must contain one customer executable.' }
if ((Get-FileHash -LiteralPath $clientExe[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedExe) {
    throw 'Customer executable hash mismatch.'
}
$bundleRoot = $clientExe[0].Directory.FullName
$appRoot = Join-Path $installRoot ('app-' + $expectedExe.Substring(0,12))
New-Item -ItemType Directory -Path $appRoot -Force | Out-Null
Get-ChildItem -LiteralPath $bundleRoot -Force | Copy-Item -Destination $appRoot -Recurse -Force
$installedExe = Join-Path $appRoot 'ServerNetworkAssistClient.exe'
if (-not (Test-Path -LiteralPath $installedExe -PathType Leaf)) { throw 'Installed customer executable is missing.' }

New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
& icacls.exe $dataRoot /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect PureNetworkClient data directory.' }

@{
    schema_version = 1
    product = '纯享入网'
    data_directory = $dataRoot
    revision = '20260919-subscription-egress-labels'
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $appRoot 'client-install.json') -Encoding UTF8

$shell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path $desktop '纯享入网.lnk'
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $installedExe
$shortcut.Arguments = '--data "' + $dataRoot + '"'
$shortcut.WorkingDirectory = $appRoot
$shortcut.IconLocation = $installedExe + ',0'
$shortcut.Save()

if (-not (Test-Path -LiteralPath 'C:\Program Files\WireGuard\wireguard.exe' -PathType Leaf)) {
    throw 'WireGuard is missing; the complete offline installer remains installed for repair.'
}
$webView = @(Get-ChildItem -LiteralPath 'C:\Program Files (x86)\Microsoft\EdgeWebView\Application' -Filter 'msedgewebview2.exe' -Recurse -File -ErrorAction SilentlyContinue)
if ($webView.Count -lt 1) { throw 'WebView2 is missing; the complete offline installer remains installed for repair.' }

$taskName = 'PureNetworkClient-Interactive-Install'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$action = New-ScheduledTaskAction -Execute $installedExe -Argument ('--data "' + $dataRoot + '"') -WorkingDirectory $appRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 12)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $taskName

$instancePath = Join-Path $dataRoot 'client-instance.json'
$deadline = (Get-Date).AddSeconds(45)
while (-not (Test-Path -LiteralPath $instancePath -PathType Leaf) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
if (-not (Test-Path -LiteralPath $instancePath -PathType Leaf)) { throw 'New PureNetworkClient did not start its local panel.' }
$instance = Get-Content -LiteralPath $instancePath -Raw | ConvertFrom-Json
$origin = 'http://127.0.0.1:' + $instance.port
$client = New-Client $origin $instance.token
$health = Invoke-GetJson $client '/api/health'
if ($health.revision -ne '20260919-subscription-egress-labels') { throw 'Unexpected client revision is running.' }

$privateInput = Get-Content -LiteralPath $EnrollmentFile -Raw | ConvertFrom-Json
if (@($privateInput.subscriptions).Count -ne 2) { throw 'Expected exactly two private subscriptions.' }
$verified = @()
foreach ($entry in @($privateInput.subscriptions)) {
    if ($entry.mode -notin @('physical','source_proxy')) { throw 'Unexpected subscription mode.' }
    $added = Invoke-PostJson $client '/api/online/subscriptions/add' @{url=$entry.url;label=$entry.name}
    $null = Invoke-PostJson $client '/api/online/subscriptions/select' @{subscription_id=$added.subscription_id}
    $catalog = Invoke-GetJson $client '/api/online/subscription'
    $modes = @($catalog.routes | ForEach-Object { $_.egress_mode } | Sort-Object -Unique)
    if ($modes.Count -ne 1 -or $modes[0] -ne $entry.mode) { throw 'Subscription exit type verification failed.' }
    $verified += [pscustomobject]@{label=$entry.name;mode=$entry.mode;subscription_id=$added.subscription_id}
}

$physical = $verified | Where-Object mode -eq 'physical' | Select-Object -First 1
$null = Invoke-PostJson $client '/api/online/subscriptions/select' @{subscription_id=$physical.subscription_id}
$null = Invoke-GetJson $client '/api/online/subscription'
$leave = Invoke-PostJson $client '/api/network/leave' @{}
$state = Invoke-GetJson $client '/api/state'
if ($state.active) { throw 'Client must remain out of network after installation.' }
$rows = @($state.saved_subscriptions)
if ($rows.Count -ne 2) { throw 'Installed client did not retain both subscriptions.' }
foreach ($row in $rows) {
    if (@($row.egress_modes).Count -ne 1) { throw 'Saved subscription label metadata is incomplete.' }
}

Remove-Item -LiteralPath $EnrollmentFile -Force
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Snapshot-Network (Join-Path $workRoot 'baseline-after-install.json')

[pscustomobject]@{
    ok = $true
    revision = $health.revision
    executable_sha256 = (Get-FileHash -LiteralPath $installedExe -Algorithm SHA256).Hash.ToLowerInvariant()
    offline_dependencies_in_install = (Test-Path -LiteralPath (Join-Path $appRoot 'dependencies\wireguard-amd64.msi'))
    desktop_shortcut = (Test-Path -LiteralPath $shortcutPath)
    subscriptions = @($verified | Select-Object label,mode)
    selected = 'physical'
    active = $false
    explicit_leave_endpoint_verified = ($null -ne $leave)
    borrow_network_manager_preserved = (Test-Path -LiteralPath 'C:\Program Files\BorrowNetworkManager')
    management_tunnel_preserved = [bool](Get-NetAdapter -Name 'fleet-titan' -ErrorAction SilentlyContinue)
    package_path = $appRoot
    data_path = $dataRoot
} | ConvertTo-Json -Depth 6 -Compress
