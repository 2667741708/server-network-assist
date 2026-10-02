$ErrorActionPreference = 'Stop'
# Display an already-running window only. Never start the client or network recovery.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ExistingClientWindow {
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr window, int command);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
}
'@
$windows = @(Get-Process -Name ServerNetworkAssistClient -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 })
foreach ($process in $windows) {
    $window = $process.MainWindowHandle
    $restored = [ExistingClientWindow]::ShowWindowAsync($window, 9)
    $focused = [ExistingClientWindow]::SetForegroundWindow($window)
    [pscustomobject]@{ pid=$process.Id; restored=$restored; focused=$focused; visible=[ExistingClientWindow]::IsWindowVisible($window) }
}
if ($windows.Count -eq 0) { throw 'No existing client window; no client was started.' }
