param(
    [Parameter(Mandatory=$true)][ValidatePattern('^https://')][string]$ControlUrl,
    [Parameter(Mandatory=$true)][string]$TokenFile,
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_.-]{1,80}$')][string]$RelayId,
    [string]$WireGuardInterface = 'wg-customer',
    [ValidateRange(10,3600)][int]$Interval = 10,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run as Administrator.' }
$resolvedToken = (Resolve-Path -LiteralPath $TokenFile).Path
$tokenItem = Get-Item -LiteralPath $resolvedToken
if ($tokenItem.PSIsContainer -or $tokenItem.LinkType) { throw 'TokenFile must be a regular file.' }
if (-not (Test-Path -LiteralPath (Join-Path $env:ProgramFiles 'WireGuard\wg.exe') -PathType Leaf)) { throw 'WireGuard for Windows is required.' }
& $Python -c 'import server_network_assist.windows_client_relay'
if ($LASTEXITCODE -ne 0) { throw 'The Windows commercial relay package is unavailable.' }
$data = Join-Path $env:ProgramData 'ServerNetworkAssist\commercial-relay'
New-Item -ItemType Directory -Path $data -Force | Out-Null
& icacls.exe $data '/inheritance:r' '/grant:r' 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to protect relay data directory.' }
& icacls.exe $resolvedToken '/inheritance:r' '/grant:r' 'SYSTEM:F' 'Administrators:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to protect relay token.' }
$arguments = @('-m','server_network_assist.windows_client_relay','--data',$data,'--control-url',$ControlUrl,'--token-file',$resolvedToken,'--interface',$WireGuardInterface,'--relay-id',$RelayId,'--interval',$Interval)
$quoted = ($arguments | ForEach-Object { '"' + $_.Replace('"','\"') + '"' }) -join ' '
$action = New-ScheduledTaskAction -Execute $Python -Argument $quoted
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal (New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest)
Register-ScheduledTask -TaskName 'ServerNetworkAssist-CommercialRelay' -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName 'ServerNetworkAssist-CommercialRelay'
Write-Output 'Installed and started ServerNetworkAssist-CommercialRelay.'
