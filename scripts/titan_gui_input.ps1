param([ValidateSet('enroll','click','scroll')][string]$Action,[int]$X,[int]$Y,[int]$Delta=-600)
$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'Titan only' }
$root = 'C:\ProgramData\ServerNetworkAssist\client-native\gui-diagnostics-20260917'
$snapshot = Get-Content -LiteralPath (Join-Path $root 'windows.json') | ConvertFrom-Json
$main = $snapshot.windows | Where-Object { $_.visible -and $_.width -eq 580 -and $_.height -eq 820 } | Select-Object -First 1
if (-not $main) { throw 'No inspected client window' }
$owner = Get-Process -Id $main.pid -ErrorAction Stop
if ($owner.Path -notin @((Join-Path $root 'ServerNetworkAssistClient.exe'),(Join-Path $root 'refined\ServerNetworkAssistClient.exe'))) { throw 'Unexpected GUI owner' }
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class TitanGuiInput {
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
 [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort vk,scan;public uint flags,time;public UIntPtr extra; }
 [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int x,y;public uint data,flags,time;public UIntPtr extra; }
 [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION { [FieldOffset(0)] public KEYBDINPUT key;[FieldOffset(0)] public MOUSEINPUT mouse; }
 [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type;public INPUTUNION data; }
 [DllImport("user32.dll",SetLastError=true)] public static extern uint SendInput(uint count,INPUT[] inputs,int size);
 public static void Text(string text) {
  var inputs=new INPUT[text.Length*2];
  for(int i=0;i<text.Length;i++) {
   inputs[i*2].type=1;inputs[i*2].data.key.scan=text[i];inputs[i*2].data.key.flags=4;
   inputs[i*2+1].type=1;inputs[i*2+1].data.key.scan=text[i];inputs[i*2+1].data.key.flags=6;
  }
  if(SendInput((uint)inputs.Length,inputs,Marshal.SizeOf(typeof(INPUT)))!=inputs.Length)throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
 }
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint flags,uint x,uint y,uint data,UIntPtr extra);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [StructLayout(LayoutKind.Sequential)] public struct RECT { public int left,top,right,bottom; }
}
'@
if ([TitanGuiInput]::SetThreadDpiAwarenessContext([IntPtr](-4)) -eq [IntPtr]::Zero) { throw 'Cannot enable physical pixel coordinates' }
$handle = [IntPtr][long]$main.handle
[TitanGuiInput]::SetForegroundWindow($handle) | Out-Null
Start-Sleep -Milliseconds 200
if ([TitanGuiInput]::GetForegroundWindow() -ne $handle) { throw 'Client is not foreground; no input sent' }
$rect = New-Object TitanGuiInput+RECT
if (-not [TitanGuiInput]::GetWindowRect($handle,[ref]$rect)) { throw 'Cannot read client bounds' }
function Click-Client([int]$cx,[int]$cy) {
    if ($cx -lt 8 -or $cy -lt 35 -or $cx -ge ($rect.right-$rect.left-8) -or $cy -ge ($rect.bottom-$rect.top-8)) { throw 'Input outside client content' }
    [TitanGuiInput]::SetCursorPos(($rect.left+$cx),($rect.top+$cy)) | Out-Null
    [TitanGuiInput]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
    [TitanGuiInput]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
}
Add-Type -AssemblyName System.Windows.Forms
if ($Action -eq 'enroll') {
    $url = (Get-Content -LiteralPath (Join-Path $root 'test-enrollment.txt') -Raw).Trim()
    if ($url -notmatch '^http://10\.20\.32\.13:9182/#enroll=[A-Za-z0-9_-]{16,512}$') { throw 'Unexpected test subscription' }
    Click-Client 150 752
    [Windows.Forms.SendKeys]::SendWait('^a')
    [TitanGuiInput]::Text($url)
    [Windows.Forms.SendKeys]::SendWait('{TAB}')
    [Windows.Forms.SendKeys]::SendWait('{ENTER}')
} elseif ($Action -eq 'scroll') {
    [TitanGuiInput]::SetCursorPos(($rect.left+300),($rect.top+400)) | Out-Null
    [TitanGuiInput]::mouse_event(0x800,0,0,[uint32]($Delta -band 0xffffffffL),[UIntPtr]::Zero)
} else { Click-Client $X $Y }
@{action=$Action;session=(Get-Process -Id $PID).SessionId;owner=$owner.Id;time=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root ('input-'+$Action+'-result.json')) -Encoding UTF8
