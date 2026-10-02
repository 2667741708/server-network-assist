param([ValidateSet('upload','publish','check')][string]$Action = 'upload')
$ErrorActionPreference = 'Stop'
$settings = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name ProxyEnable,ProxyServer
if ($settings.ProxyEnable -ne 1 -or $settings.ProxyServer -ne '127.0.0.1:7897') { throw 'The existing proxy is not enabled as expected.' }
$previous = $env:HTTPS_PROXY
try {
    $env:HTTPS_PROXY = 'http://127.0.0.1:7897'
    if ($Action -eq 'upload') {
        & gh release upload client-campus-20260918 artifacts/client-campus-1030-20260918-v2/ServerNetworkAssistClient.exe --repo 2667741708/server-network-assist
    } elseif ($Action -eq 'publish') {
        & gh release edit client-campus-20260918 --tag client-campus-20260918 --draft=false --repo 2667741708/server-network-assist
    } else {
        & gh release view client-campus-20260918 --repo 2667741708/server-network-assist --json url,isDraft,assets
    }
    if ($LASTEXITCODE -ne 0) { throw 'GitHub release operation failed.' }
} finally {
    $env:HTTPS_PROXY = $previous
}
