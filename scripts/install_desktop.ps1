param(
    [Parameter(Mandatory=$true)][string]$RuntimeSource,
    [Parameter(Mandatory=$true)][string]$Wheel,
    [string]$Version = '0.2.0'
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this installer as Administrator' }
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid version' }
$source = (Resolve-Path -LiteralPath $RuntimeSource).Path
$wheelPath = (Resolve-Path -LiteralPath $Wheel).Path
$stdlib = Get-ChildItem -LiteralPath $source -Filter 'python3*.zip' -File | Select-Object -First 1
if (-not $stdlib -or -not (Test-Path -LiteralPath (Join-Path $source 'pythonw.exe'))) { throw 'Use a Windows embeddable Python runtime containing python3xx.zip and pythonw.exe' }
$base = 'C:\ProgramData\ServerNetworkAssist\desktop'
$target = Join-Path $base $Version
[IO.Directory]::CreateDirectory($target) | Out-Null
& icacls.exe $target /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect installation directory' }
$taskName = 'ServerNetworkAssist-Desktop'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName
}
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
& $python -m zipfile -e $wheelPath $packages
if ($LASTEXITCODE -ne 0) { throw 'Cannot extract application wheel' }
& $python -c 'import server_network_assist.desktop; print(server_network_assist.__version__)'
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
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $identity -Settings $settings -Force | Out-Null
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
Start-ScheduledTask -TaskName $taskName
@{ ok = $true; version = $Version; installation = $target; shortcut = $shortcutPath; data = $data; user = $user } | ConvertTo-Json -Compress
