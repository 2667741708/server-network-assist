$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'Titan only' }
$root = 'C:\ProgramData\ServerNetworkAssist\client-native\gui-diagnostics-20260917'
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Diagnostics;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class TitanGuiInspect {
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
 public delegate bool EnumProc(IntPtr h,IntPtr l);
 [StructLayout(LayoutKind.Sequential)] public struct RECT { public int left,top,right,bottom; }
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p,IntPtr l);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,StringBuilder t,int n);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h,StringBuilder t,int n);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 public static List<object> Find() {
  var result=new List<object>();var ids=new HashSet<int>();
  foreach(var p in Process.GetProcessesByName("ServerNetworkAssistClient"))ids.Add(p.Id);
  EnumWindows((h,l)=>{uint pid;GetWindowThreadProcessId(h,out pid);if(ids.Contains((int)pid)){
   var title=new StringBuilder(512);GetWindowText(h,title,512);var name=new StringBuilder(512);GetClassName(h,name,512);RECT rect;GetWindowRect(h,out rect);
   result.Add(new {handle=h.ToInt64(),pid=pid,title=title.ToString(),name=name.ToString(),visible=IsWindowVisible(h),left=rect.left,top=rect.top,width=rect.right-rect.left,height=rect.bottom-rect.top});
  }return true;},IntPtr.Zero);return result;
 }
}
'@
if ([TitanGuiInspect]::SetThreadDpiAwarenessContext([IntPtr](-4)) -eq [IntPtr]::Zero) { throw 'Cannot enable physical pixel coordinates' }
$windows = [TitanGuiInspect]::Find()
@{session=(Get-Process -Id $PID).SessionId;windows=@($windows)} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root 'windows.json') -Encoding UTF8
$main = $windows | Where-Object { $_.visible -and $_.width -ge 300 -and $_.height -ge 300 } | Select-Object -First 1
if ($main) {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $element = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$main.handle)
    $elements = $element.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition)
    $controls = @($elements | ForEach-Object {
        $current = $_.Current
        @{id=$current.AutomationId;name=$current.Name;type=$current.ControlType.ProgrammaticName;enabled=$current.IsEnabled;offscreen=$current.IsOffscreen;invoke=$_.GetCurrentPropertyValue([Windows.Automation.AutomationElement]::IsInvokePatternAvailableProperty);value=$_.GetCurrentPropertyValue([Windows.Automation.AutomationElement]::IsValuePatternAvailableProperty)}
    })
    $controls | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root 'controls.json') -Encoding UTF8
    Add-Type -AssemblyName System.Drawing
    $bitmap = New-Object System.Drawing.Bitmap $main.width,$main.height
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen($main.left,$main.top,0,0,$bitmap.Size)
        $bitmap.Save((Join-Path $root 'native-window.png'),[Drawing.Imaging.ImageFormat]::Png)
    } finally { $graphics.Dispose();$bitmap.Dispose() }
}
