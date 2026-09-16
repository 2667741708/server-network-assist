param([string]$Version = '0.6.0')
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this installer as Administrator' }
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid version' }
$runtime = Join-Path 'C:\ProgramData\ServerNetworkAssist\desktop' $Version
$pythonw = Join-Path $runtime 'pythonw.exe'
$python = Join-Path $runtime 'python.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw 'Install the matching Server Network Assist desktop runtime first' }
$folders = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$data = Join-Path 'C:\ProgramData\ServerNetworkAssist\client' $userSid
[IO.Directory]::CreateDirectory($data) | Out-Null
& icacls.exe $data /inheritance:r /grant:r "*$($userSid):(OI)(CI)RX" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect client data directory' }
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '-m server_network_assist.client --serve --data "' + $data + '"'
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $runtime
$principalTask = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$taskName = 'ServerNetworkAssist-Client'
$oldTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$oldTaskXml = if ($oldTask) { Export-ScheduledTask -TaskName $taskName } else { $null }
try {
    if ($oldTask) { Stop-ScheduledTask -TaskName $taskName }
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principalTask -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    & $python -m server_network_assist.client_install_check --data $data --version $Version
    if ($LASTEXITCODE -ne 0) { throw 'Customer client health check failed' }
} catch {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($oldTaskXml) {
        Register-ScheduledTask -TaskName $taskName -Xml $oldTaskXml -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
    } else {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    throw
}
$name = -join ([char[]]@(0x501F,0x7F51,0x5BA2,0x6237,0x7AEF))
$shortcutPath = Join-Path $folders.Desktop ($name + '.lnk')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '-m server_network_assist.client --data "' + $data + '"'
$shortcut.WorkingDirectory = $runtime
$shortcut.Description = 'Server Network Assist customer client'
$shortcut.Save()
@{ ok = $true; task = $taskName; shortcut = $shortcutPath; data = $data } | ConvertTo-Json -Compress
