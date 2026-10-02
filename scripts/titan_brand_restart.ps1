$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'D321 only' }
$data = 'C:\ProgramData\ServerNetworkAssist\client\S-1-5-21-1446874470-693334550-1715965273-1001\gui-window-test-20260917'
$info = Get-Content -Raw -LiteralPath (Join-Path $data 'client-instance.json') | ConvertFrom-Json
$base = 'http://127.0.0.1:' + [int]$info.port
$state = Invoke-RestMethod -Uri ($base + '/api/state') -Headers @{'X-Client-Token'=$info.token} -TimeoutSec 5
if ($state.active -or $state.recovering) { throw 'Leave borrowing and verify recovery before restart' }
if (Test-Path -LiteralPath (Join-Path $data 'customer-owned-tunnel.json')) { throw 'Owned tunnel still present' }
$owner = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$info.pid)
$programs = 'C:\Users\86133\AppData\Local\Programs\PureNetworkClient\'
if (-not $owner.ExecutablePath.StartsWith($programs, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected client owner' }
$processes = @(Get-CimInstance Win32_Process -Filter "Name = 'ServerNetworkAssistClient.exe'" | Where-Object { $_.ExecutablePath -eq $owner.ExecutablePath })
foreach ($process in $processes) {
    if ($process.SessionId -ne 1 -or $process.CommandLine -notlike ('*' + $data + '*')) { throw 'Unexpected process data/session' }
}
foreach ($process in $processes | Where-Object { $_.CommandLine -like '*--guard*' }) {
    Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
}
foreach ($process in $processes | Where-Object { $_.CommandLine -notlike '*--guard*' }) {
    Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
}
$target = 'C:\Users\86133\AppData\Local\Programs\PureNetworkClient\gui-329256e07ee9\ServerNetworkAssistClient.exe'
if ((Get-FileHash -LiteralPath $target).Hash -ne '329256E07EE93FD75B4F3EC541DDC0EDD5D0F4FACF94C180AA2352FFD645EB96') { throw 'New client hash mismatch' }
$name = 'SNA-Brand-D321-20260917'
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { throw 'Launch task exists; inspect before replacement' }
$action = New-ScheduledTaskAction -Execute $target -Argument ('--data "' + $data + '"')
$principal = New-ScheduledTaskPrincipal -UserId 'd321-titan' -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $name
Write-Output 'Started verified brand client; no automatic borrowing by launch'
