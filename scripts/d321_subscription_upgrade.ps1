param([ValidateSet('snapshot','import','close','verify','leave')][string]$Action)
$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'D321 only' }
$work = 'C:\Users\86133\sna-local-gui-goal-20260917'
$data = 'C:\ProgramData\ServerNetworkAssist\client\S-1-5-21-1446874470-693334550-1715965273-1001\gui-window-test-20260917'
trap {
    Save-Report @{ok=$false;error=$_.Exception.Message}
    exit 1
}
function Private-Panel {
    $info = Get-Content -LiteralPath (Join-Path $data 'client-instance.json') -Raw | ConvertFrom-Json
    $base = 'http://127.0.0.1:' + [int]$info.port
    $headers = @{'X-Client-Token'=$info.token;Origin=$base}
    $health = Invoke-RestMethod -Uri ($base+'/api/health') -Headers $headers -TimeoutSec 5
    if ($health.pid -ne $info.pid) { throw 'Panel identity mismatch' }
    $state = Invoke-RestMethod -Uri ($base+'/api/state') -Headers $headers -TimeoutSec 10
    return @{info=$info;base=$base;headers=$headers;health=$health;state=$state}
}
function Network-State {
    $routes = @(Get-NetRoute | Select-Object DestinationPrefix,InterfaceIndex,NextHop,RouteMetric | Sort-Object DestinationPrefix,InterfaceIndex,NextHop,RouteMetric)
    $dns = @(Get-DnsClientServerAddress | Select-Object InterfaceIndex,AddressFamily,ServerAddresses | Sort-Object InterfaceIndex,AddressFamily)
    $proxy = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' | Select-Object ProxyEnable,ProxyServer,ProxyOverride,AutoConfigURL
    return @{routes=$routes;dns=$dns;proxy=$proxy}
}
function Save-Report($value) {
    $value | ConvertTo-Json -Depth 8
    try { $value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $work ('upgrade-'+$Action+'.json')) -Encoding UTF8 }
    catch { Write-Warning 'Optional report could not be saved' }
}
if ($Action -eq 'leave') {
    $panel = Private-Panel
    $result = Invoke-RestMethod -Uri ($panel.base+'/api/network/leave') -Method Post -Headers $panel.headers -ContentType 'application/json' -Body '{}' -TimeoutSec 45
    $after = Private-Panel
    if ($after.state.active -or $after.state.recovering) { throw 'Leave/recovery incomplete; upgrade stopped' }
    Save-Report @{ok=$true;active=$after.state.active;recovering=$after.state.recovering;release_error=$result.release_error}
    exit
}
if ($Action -eq 'snapshot') {
    if (Test-Path -LiteralPath (Join-Path $data 'client-instance.json')) {
        $panel = Private-Panel
    } else {
        if (Get-Process -Name ServerNetworkAssistClient -ErrorAction SilentlyContinue) { throw 'Unexpected running client without instance' }
        foreach ($file in @('customer-owned-tunnel.json','customer-leaving-network.json','customer-active-line.json')) {
            if (Test-Path -LiteralPath (Join-Path $data $file)) { throw 'Owned state remains' }
        }
        $panel = @{state=@{active=$null;recovering=$false};health=@{revision='not-running'}}
    }
    if ($panel.state.active -or $panel.state.recovering) { throw 'Client must be fully disconnected before this copy-only upgrade' }
    $network = Network-State
    $network | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $work 'upgrade-network-before.json') -Encoding UTF8
    Save-Report @{ok=$true;revision=$panel.health.revision;active=$panel.state.active;recovering=$panel.state.recovering}
    exit
}
if ($Action -eq 'import') {
    $panel = Private-Panel
    if ($panel.state.recovering) { throw 'Recovery incomplete' }
    $identityPath = Join-Path $data 'customer-online-service.json'
    $before = (Get-FileHash -LiteralPath $identityPath).Hash
    $result = Invoke-RestMethod -Uri ($panel.base+'/api/online/subscriptions/import') -Method Post -Headers $panel.headers -ContentType 'application/json' -Body '{}' -TimeoutSec 15
    if ((Get-FileHash -LiteralPath $identityPath).Hash -ne $before) { throw 'Import changed selected identity' }
    Save-Report @{ok=$true;imported=$result.imported;skipped=$result.skipped;selected_unchanged=$true;subscriptions=$result.subscriptions}
    exit
}
if ($Action -eq 'close') {
    $panel = Private-Panel
    if ($panel.state.active -or $panel.state.recovering) { throw 'No force-close of an active/recovering client' }
    foreach ($file in @('customer-owned-tunnel.json','customer-leaving-network.json','customer-active-line.json')) {
        if (Test-Path -LiteralPath (Join-Path $data $file)) { throw 'Owned network state remains; no close sent' }
    }
    $owner = Get-Process -Id $panel.info.pid -ErrorAction Stop
    if (-not $owner.Path.StartsWith('C:\Users\86133\AppData\Local\Programs\PureNetworkClient\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected application' }
    if ($owner.SessionId -ne (Get-Process -Id $PID).SessionId) { throw 'Close must run in the user desktop session' }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class SubscriptionGuiClose {
 public delegate bool EnumProc(IntPtr h,IntPtr l);
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p,IntPtr l);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
 public static int Close(int id) {int count=0;EnumWindows((h,l)=>{uint pid;GetWindowThreadProcessId(h,out pid);if(pid==id && GetWindowTextLength(h)>0){if(PostMessage(h,0x10,IntPtr.Zero,IntPtr.Zero))count++;}return true;},IntPtr.Zero);return count;}
}
'@
    $posted = [SubscriptionGuiClose]::Close($owner.Id)
    if ($posted -lt 1) { throw 'No client window; no forced termination performed' }
    Save-Report @{ok=$true;posted=$posted;pid=$owner.Id;force_killed=$false}
    exit
}
$panel = Private-Panel
if ($panel.health.revision -ne '20260917-subscription-fold') { throw 'Wrong running revision' }
$owner = Get-Process -Id $panel.info.pid -ErrorAction Stop
if ($owner.SessionId -ne (Get-Process -Id $PID).SessionId) { throw 'Verify must run in the user desktop session' }
Invoke-RestMethod -Uri ($panel.base+'/api/ui/show') -Method Post -Headers $panel.headers -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
Start-Sleep -Milliseconds 700
$owner.Refresh()
if ($owner.MainWindowHandle -eq 0) { throw 'No native main window' }
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class SubscriptionGuiFocus {
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h,int command);
}
'@
[SubscriptionGuiFocus]::ShowWindowAsync($owner.MainWindowHandle,3) | Out-Null
[SubscriptionGuiFocus]::SetForegroundWindow($owner.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 300
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$element = [Windows.Automation.AutomationElement]::FromHandle($owner.MainWindowHandle)
$descendants = $element.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition)
$controls = @($descendants | ForEach-Object { @{name=$_.Current.Name;offscreen=$_.Current.IsOffscreen;enabled=$_.Current.IsEnabled} })
$visible = @($controls | Where-Object { -not $_.offscreen } | ForEach-Object { $_.name })
$heading = -join ([char[]]@(0x6211,0x7684,0x8BA2,0x9605))
$allNames = @($controls | ForEach-Object { $_.name })
$listVisible = [bool]($visible | Where-Object { $_ -eq $heading -or $_.Contains($heading) })
$listPresent = [bool]($allNames | Where-Object { $_ -eq $heading -or $_.Contains($heading) })
$before = Get-Content -LiteralPath (Join-Path $work 'upgrade-network-before.json') -Raw | ConvertFrom-Json
$network = Network-State
$report = @{ok=$true;revision=$panel.health.revision;active=$panel.state.active;recovering=$panel.state.recovering;subscriptions=$panel.state.saved_subscriptions;native_window=$owner.MainWindowHandle.ToInt64();subscription_heading_visible=$listVisible;visible_controls=$visible;routes_equal=(($before.routes|ConvertTo-Json -Depth 8 -Compress) -ceq ($network.routes|ConvertTo-Json -Depth 8 -Compress));dns_equal=(($before.dns|ConvertTo-Json -Depth 8 -Compress) -ceq ($network.dns|ConvertTo-Json -Depth 8 -Compress));proxy_equal=(($before.proxy|ConvertTo-Json -Depth 8 -Compress) -ceq ($network.proxy|ConvertTo-Json -Depth 8 -Compress));management_running=((Get-Service -Name 'WireGuardTunnel$fleet-titan').Status -eq 'Running')}
Save-Report $report
$report.subscription_heading_present = $listPresent
Save-Report $report
try {
    Add-Type -AssemblyName System.Drawing
    $rect = $element.Current.BoundingRectangle
    $bitmap = New-Object System.Drawing.Bitmap ([int]$rect.Width),([int]$rect.Height)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen([int]$rect.X,[int]$rect.Y,0,0,$bitmap.Size)
        $bitmap.Save((Join-Path $work 'subscription-fold-native.png'),[Drawing.Imaging.ImageFormat]::Png)
    } finally { $graphics.Dispose();$bitmap.Dispose() }
} catch { Write-Warning 'Optional GUI screenshot failed' }
if (-not $listPresent) { throw 'Subscription heading missing from native GUI' }
if (-not $report.routes_equal -or -not $report.dns_equal -or -not $report.proxy_equal) { throw 'Network state changed during idle upgrade' }
