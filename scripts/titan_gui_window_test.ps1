param([ValidateSet('baseline','verify','close','launch')][string]$Action)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'Titan only' }
$root = 'C:\ProgramData\ServerNetworkAssist\client-native\gui-diagnostics-20260917'
$oldData = 'C:\ProgramData\ServerNetworkAssist\client\S-1-5-21-1446874470-693334550-1715965273-1001'
$data = Join-Path $oldData 'gui-window-test-20260917'
if ($Action -in @('baseline','verify')) {
    $routes = @(Get-NetRoute | Select-Object DestinationPrefix,InterfaceIndex,NextHop,RouteMetric | Sort-Object DestinationPrefix,InterfaceIndex,NextHop,RouteMetric)
    $dns = @(Get-DnsClientServerAddress | Select-Object InterfaceIndex,AddressFamily,ServerAddresses | Sort-Object InterfaceIndex,AddressFamily)
    $proxy = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' | Select-Object ProxyEnable,ProxyServer,ProxyOverride,AutoConfigURL
    $current = @{routes=$routes;dns=$dns;proxy=$proxy}
    if ($Action -eq 'baseline') {
        $current | ConvertTo-Json -Depth 8 -Compress | Set-Content -LiteralPath (Join-Path $root 'baseline.json') -Encoding UTF8
        Write-Output 'Baseline saved'
    } else {
        $before = Get-Content -LiteralPath (Join-Path $root 'baseline.json') -Raw | ConvertFrom-Json
        $report = [ordered]@{
            routes_equal=(($before.routes | ConvertTo-Json -Depth 8 -Compress) -ceq ($routes | ConvertTo-Json -Depth 8 -Compress))
            dns_equal=(($before.dns | ConvertTo-Json -Depth 8 -Compress) -ceq ($dns | ConvertTo-Json -Depth 8 -Compress))
            proxy_equal=(($before.proxy | ConvertTo-Json -Depth 8 -Compress) -ceq ($proxy | ConvertTo-Json -Depth 8 -Compress))
            management_running=((Get-Service -Name 'WireGuardTunnel$fleet-titan').Status -eq 'Running')
            client_files=@(Get-ChildItem -LiteralPath $data -File | Select-Object -ExpandProperty Name)
            time=(Get-Date).ToString('o')
        }
        $current | ConvertTo-Json -Depth 8 -Compress | Set-Content -LiteralPath (Join-Path $root 'after.json') -Encoding UTF8
        $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root 'verification.json') -Encoding UTF8
        $report | ConvertTo-Json -Depth 5
    }
    exit
}
if ($Action -eq 'close') {
    $closingData = $oldData
    if (Test-Path -LiteralPath (Join-Path $data 'client-instance.json')) { $closingData = $data }
    $instance = Get-Content -LiteralPath (Join-Path $closingData 'client-instance.json') | ConvertFrom-Json
    $owner = Get-Process -Id $instance.pid -ErrorAction Stop
    if (-not $owner.Path.StartsWith('C:\ProgramData\ServerNetworkAssist\client-native\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected client owner' }
    foreach ($file in @('customer-active-line.json','customer-leaving-network.json','customer-owned-tunnel.json')) {
        if (Test-Path -LiteralPath (Join-Path $closingData $file)) { throw 'Existing active or recovering client: inspect before closing' }
    }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class TitanGuiClose {
 public delegate bool EnumProc(IntPtr h, IntPtr l);
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
 [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint message, IntPtr w, IntPtr l);
 public static int Close(int owner) {
  int count=0;
  EnumWindows((h,l)=>{uint pid;GetWindowThreadProcessId(h,out pid);if(pid==owner && GetWindowTextLength(h)>0){PostMessage(h,0x10,IntPtr.Zero,IntPtr.Zero);count++;}return true;},IntPtr.Zero);
  return count;
 }
}
'@
    $count = [TitanGuiClose]::Close($owner.Id)
    @{posted=$count;owner=$owner.Id;session=(Get-Process -Id $PID).SessionId} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'close-result.json') -Encoding UTF8
    if ($count -lt 1) { throw 'No interactive client window' }
    exit
}
if ($Action -eq 'launch') {
    if (Get-Process -Name ServerNetworkAssistClient -ErrorAction SilentlyContinue) { throw 'Old client processes must exit first' }
    $exe = Join-Path $root 'ServerNetworkAssistClient.exe'
    $refined = Join-Path $root 'refined\ServerNetworkAssistClient.exe'
    if (Test-Path -LiteralPath $refined) { $exe = $refined }
    $process = Start-Process -FilePath $exe -ArgumentList @('--data',$data) -WindowStyle Normal -PassThru
    @{launcher=$process.Id;session=(Get-Process -Id $PID).SessionId;data=$data} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'launch-result.json') -Encoding UTF8
}
