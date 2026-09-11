param(
    [Parameter(Mandatory=$true)][string]$RuntimeSource,
    [string]$Wheel,
    [string]$PackageArchive,
    [string]$PackageSha256,
    [string]$Version = '0.3.0'
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this installer as Administrator' }
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid version' }
$source = (Resolve-Path -LiteralPath $RuntimeSource).Path
if ($PackageArchive) {
    $archivePath = (Resolve-Path -LiteralPath $PackageArchive).Path
    if ($PackageSha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'Supply the reviewed package SHA256' }
    if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash -ne $PackageSha256) { throw 'Package hash mismatch' }
} elseif ($Wheel -and $Version -eq '0.2.0') {
    $archivePath = (Resolve-Path -LiteralPath $Wheel).Path
} else { throw 'Version 0.3.0 requires the full offline PackageArchive and PackageSha256' }
$stdlib = Get-ChildItem -LiteralPath $source -Filter 'python3*.zip' -File | Select-Object -First 1
if (-not $stdlib -or -not (Test-Path -LiteralPath (Join-Path $source 'pythonw.exe'))) { throw 'Use a Windows embeddable Python runtime containing python3xx.zip and pythonw.exe' }
$base = 'C:\ProgramData\ServerNetworkAssist\desktop'
$target = Join-Path $base $Version
if (Test-Path -LiteralPath $target) { throw 'This version directory already exists; inspect it before reinstalling' }
[IO.Directory]::CreateDirectory($target) | Out-Null
& icacls.exe $target /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect installation directory' }
$taskName = 'ServerNetworkAssist-Desktop'
$oldTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$oldTaskXml = if ($oldTask) { Export-ScheduledTask -TaskName $taskName } else { $null }
# Copy only the embedded interpreter and stdlib, not credentials or user packages.
foreach ($file in Get-ChildItem -LiteralPath $source -File) {
    if ($file.Extension -in @('.exe','.dll','.pyd','.zip','.cat') -or $file.Name -eq 'LICENSE.txt') {
        $destination = Join-Path $target $file.Name
        if (Test-Path -LiteralPath $destination) {
            $oldHash = (Get-FileHash -LiteralPath $destination).Hash
            $newHash = (Get-FileHash -LiteralPath $file.FullName).Hash
            if ($oldHash -eq $newHash) { continue }
        }
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }
}
$pathFile = Join-Path $target ($stdlib.BaseName + '._pth')
[IO.File]::WriteAllText($pathFile, "$($stdlib.Name)`n.`npackages`n", [Text.UTF8Encoding]::new($false))
$python = Join-Path $target 'python.exe'
$pythonw = Join-Path $target 'pythonw.exe'
$packages = Join-Path $target 'packages'
& $python -m zipfile -e $archivePath $packages
if ($LASTEXITCODE -ne 0) { throw 'Cannot extract application wheel' }
if ($PackageArchive) {
    & $python -c 'import sys, server_network_assist.desktop, server_network_assist.app, pystray, PIL; assert server_network_assist.__version__ == sys.argv[1]; print(server_network_assist.__version__)' $Version
} else {
    & $python -c 'import sys, server_network_assist.desktop; assert server_network_assist.__version__ == sys.argv[1]; print(server_network_assist.__version__)' $Version
}
if ($LASTEXITCODE -ne 0) { throw 'Desktop import check failed' }
$folders = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'
$data = Join-Path $folders.'Local AppData' 'ServerNetworkAssist'
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
[IO.Directory]::CreateDirectory($data) | Out-Null
& icacls.exe $data /inheritance:r /grant:r "*$($userSid):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect desktop user data' }
foreach ($name in @('desktop.lock','desktop-instance.json','desktop-instance.tmp')) {
    $file = Join-Path $data $name
    if (Test-Path -LiteralPath $file) {
        & icacls.exe $file /reset | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot restore current user access to desktop state' }
    }
}
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '-m server_network_assist.desktop --serve --data "' + $data + '"'
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $target
$identity = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$previousPid = $null
$instanceFile = Join-Path $data 'desktop-instance.json'
if (Test-Path -LiteralPath $instanceFile) {
    try { $previousPid = (Get-Content -LiteralPath $instanceFile -Raw | ConvertFrom-Json).pid } catch { }
}
# Stage and import-check everything before replacing the running backend.
try {
    if ($oldTask) {
        Stop-ScheduledTask -TaskName $taskName
        # Stop-ScheduledTask can return before the process releases desktop.lock.
        if ($previousPid -and (Get-Process -Id $previousPid -ErrorAction SilentlyContinue)) {
            Wait-Process -Id $previousPid -Timeout 15 -ErrorAction Stop
        }
    }
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $identity -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    if ($PackageArchive) {
        & $python -m server_network_assist.desktop_install_check --data $data --version $Version
        if ($LASTEXITCODE -ne 0) { throw 'New desktop backend health check failed' }
    }
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
$name = -join ([char[]]@(0x670D,0x52A1,0x5668,0x7F51,0x7EDC,0x52A9,0x624B))
$shortcutPath = Join-Path $folders.Desktop ($name + '.lnk')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '-m server_network_assist.desktop --data "' + $data + '"'
$shortcut.WorkingDirectory = $target
$shortcut.Description = 'Server Network Assist - local network desktop panel'
$shortcut.IconLocation = Join-Path $packages 'server_network_assist\desktop_ui\icon.ico'
$shortcut.Save()
# Native tray starts at interactive logon and when opening the shortcut.
$trayAction = New-ScheduledTaskAction -Execute $pythonw -Argument ('-m server_network_assist.desktop_tray --data "' + $data + '"') -WorkingDirectory $target
$trayIdentity = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$trayTaskName = 'ServerNetworkAssist-Tray'
$oldTray = Get-ScheduledTask -TaskName $trayTaskName -ErrorAction SilentlyContinue
$oldTrayXml = if ($oldTray) { Export-ScheduledTask -TaskName $trayTaskName } else { $null }
try {
    if ($oldTray) { Stop-ScheduledTask -TaskName $trayTaskName }
    Register-ScheduledTask -TaskName $trayTaskName -Action $trayAction -Trigger $trigger -Principal $trayIdentity -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $trayTaskName
} catch {
    Write-Warning ('Optional tray setup failed; the verified desktop backend remains running: ' + $_.Exception.Message)
    if ($oldTrayXml) {
        try {
            Register-ScheduledTask -TaskName $trayTaskName -Xml $oldTrayXml -Force | Out-Null
            Start-ScheduledTask -TaskName $trayTaskName
        } catch { Write-Warning ('Previous tray task could not be restored: ' + $_.Exception.Message) }
    }
}
@{ ok = $true; version = $Version; installation = $target; shortcut = $shortcutPath; data = $data; user = $user } | ConvertTo-Json -Compress
