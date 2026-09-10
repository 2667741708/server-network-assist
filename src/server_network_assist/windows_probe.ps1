$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Test-Web([string]$Url, [bool]$Direct) {
    $response = $null
    try {
        $request = [Net.HttpWebRequest]::Create($Url)
        $request.Timeout = 6000
        $request.ReadWriteTimeout = 6000
        $request.AllowAutoRedirect = $false
        if ($Direct) { $request.Proxy = $null }
        else { $request.Proxy = [Net.WebRequest]::GetSystemWebProxy() }
        $response = $request.GetResponse()
        $code = [int]$response.StatusCode
        if ($Url.EndsWith('generate_204')) { return ($code -eq 204) }
        return ($code -eq 200)
    } catch { return $false }
    finally { if ($response) { $response.Dispose() } }
}

$route = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue)
$routeText = ($route | ForEach-Object { "$($_.InterfaceAlias): $($_.NextHop)" }) -join '; '
$dns = $false
try { $dns = @([Net.Dns]::GetHostAddresses('www.baidu.com')).Count -gt 0 } catch {}
$direct = $false
$system = $false
foreach ($url in @('https://connectivitycheck.gstatic.com/generate_204', 'https://www.baidu.com')) {
    if (Test-Web $url $true) { $direct = $true }
    if (Test-Web $url $false) { $system = $true }
}
$proxy = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
$proxyEnabled = [bool]$proxy.ProxyEnable
$diagnosis = ''
if ($direct -and -not $system) { $diagnosis = 'system_proxy_failed' }
if (-not $direct -and $system) { $diagnosis = 'proxy_only' }
$helper = 'C:\ProgramData\ServerNetworkAssist\windows_helper.ps1'
$assist = @()
$helperReady = $false
if (Test-Path -LiteralPath $helper) {
    try {
        $raw = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $helper status
        if ($LASTEXITCODE -eq 0) {
            $value = $raw | ConvertFrom-Json
            $helperReady = [bool]$value.ok
            $assist = @($value.assist)
        }
    } catch {}
}
@{
    ssh = $true; os = 'Windows'; hostname = [Net.Dns]::GetHostName()
    dns = $dns; internet = $direct; system_internet = $system
    proxy_enabled = $proxyEnabled; diagnosis = $diagnosis
    default_route = $routeText; http_code = $(if ($direct) { '200' } else { '000' })
    helper = $helperReady; assist = $assist; client_supported = $true
    gateway_supported = [bool](Get-Command New-NetNat -ErrorAction SilentlyContinue)
} | ConvertTo-Json -Depth 8 -Compress
