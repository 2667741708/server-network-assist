# Open the shortcut in the signed-in user's interactive desktop from an SSH session.
$ErrorActionPreference = 'Stop'
$folders = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'
$name = -join ([char[]]@(0x670D,0x52A1,0x5668,0x7F51,0x7EDC,0x52A9,0x624B))
$shortcutPath = Join-Path $folders.Desktop ($name + '.lnk')
if (-not (Test-Path -LiteralPath $shortcutPath)) { throw 'Desktop shortcut is not installed' }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $shortcut.TargetPath -Argument $shortcut.Arguments -WorkingDirectory $shortcut.WorkingDirectory
$identity = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$task = 'ServerNetworkAssist-OpenWindow'
Register-ScheduledTask -TaskName $task -Action $action -Principal $identity -Force | Out-Null
try {
    Start-ScheduledTask -TaskName $task
    Start-Sleep -Seconds 3
    Get-ScheduledTaskInfo -TaskName $task | Select-Object LastTaskResult | ConvertTo-Json -Compress
} finally {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false
}
