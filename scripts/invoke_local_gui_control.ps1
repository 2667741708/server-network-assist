param([ValidateSet('launch','inspect','click','enroll','scroll','verify')][string]$Action,[int]$X,[int]$Y,[int]$Delta=-600)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
if ([Environment]::MachineName -ne 'WHM-SAVE') { throw 'WHM-SAVE only' }
$root = 'C:\Users\hmw20\.ssh\gui-d321-control-20260917'
if ($Action -eq 'verify') {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal $identity
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Read verification requires the authorized administrator SSH context; protected customer records must not be reported missing' }
    $settings = Get-Content -LiteralPath (Join-Path $root 'gui-config.json') -Raw | ConvertFrom-Json
    $data = $settings.data
    $active = Join-Path $data 'customer-active-line.json'
    $tunnel = $null
    if (Test-Path -LiteralPath $active) { $record = Get-Content -LiteralPath $active -Raw | ConvertFrom-Json; $tunnel = $record.tunnel }
    $service = $null
    $handshake = $null
    if ($tunnel -match '^sna[a-f0-9]{12}$') {
        $service = [string](Get-Service -Name ('WireGuardTunnel$' + $tunnel) -ErrorAction SilentlyContinue).Status
        $handshake = & 'C:\Program Files\WireGuard\wg.exe' show $tunnel latest-handshakes
    }
    @{active=(Test-Path -LiteralPath $active);recovering=(Test-Path -LiteralPath (Join-Path $data 'customer-leaving-network.json'));configured=(Test-Path -LiteralPath (Join-Path $data 'customer-online-service.json'));owned=(Test-Path -LiteralPath (Join-Path $data 'customer-owned-tunnel.json'));service=$service;handshake=$handshake;time=(Get-Date).ToString('o')} | ConvertTo-Json -Compress
    exit
}
$name = 'SNA-Local-Gui-D321-20260917-' + $Action
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq 'Running') { throw 'Existing same GUI action still running; inspect rather than retry' }
$script = Join-Path $root 'input.ps1'
$guiArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File ' + $script + ' -Action ' + $Action + ' -X ' + $X + ' -Y ' + $Y + ' -Delta ' + $Delta
if ($Action -eq 'inspect') { $guiArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File ' + (Join-Path $root 'inspect.ps1') }
if ($Action -eq 'launch') { $guiArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File ' + (Join-Path $root 'launch.ps1') }
$command = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument $guiArguments
$principal = New-ScheduledTaskPrincipal -UserId hmw20 -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $name -Action $command -Principal $principal -Force | Out-Null
$started = Get-Date
Start-ScheduledTask -TaskName $name
do {
    Start-Sleep -Milliseconds 300
    $task = Get-ScheduledTask -TaskName $name
    $info = Get-ScheduledTaskInfo -TaskName $name
} while ((($info.LastRunTime -lt $started.AddSeconds(-1)) -or $task.State -eq 'Running') -and ((Get-Date) - $started).TotalSeconds -lt 15)
if ($task.State -eq 'Running') { throw 'GUI action still running; inspect specific task before retry' }
if ($info.LastTaskResult -ne 0) { throw ('GUI task failed: ' + $info.LastTaskResult) }
if ($Action -eq 'inspect') {
    $windows = Get-Content -LiteralPath (Join-Path $root 'windows.json') -Raw | ConvertFrom-Json
    if ((Get-Item -LiteralPath (Join-Path $root 'windows.json')).LastWriteTime -lt $started) { throw 'Stale screenshot' }
    $picture = $null
    if ($windows.windows | Where-Object { $_.visible -and $_.width -ge 300 -and $_.height -ge 300 }) { $picture = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root 'native-window.png'))) }
    @{windows=$windows;png=$picture;time=(Get-Date).ToString('o')} | ConvertTo-Json -Depth 6 -Compress
} else { @{action=$Action;completed=$true;time=(Get-Date).ToString('o')} | ConvertTo-Json -Compress }
