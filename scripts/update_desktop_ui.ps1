param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$ExpectedHostname
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator required' }
if ([Net.Dns]::GetHostName() -ne $ExpectedHostname) { throw 'Host identity mismatch' }
$sourceRoot = (Resolve-Path -LiteralPath $Source).Path
$installRoot = 'C:\ProgramData\ServerNetworkAssist\desktop\0.2.0'
$packageRoot = Join-Path $installRoot 'packages\server_network_assist'
if (-not (Test-Path -LiteralPath (Join-Path $packageRoot 'desktop.py'))) { throw 'Expected desktop installation not found' }
$manifest = Get-Content -LiteralPath (Join-Path $sourceRoot 'manifest.json') -Raw | ConvertFrom-Json
$allowed = @('desktop.py','desktop_ui/index.html','desktop_ui/desktop.js','desktop_ui/desktop.css')
if ($manifest.files.Count -ne $allowed.Count) { throw 'Unexpected update file count' }
foreach ($file in $manifest.files) {
    if ($file.path -notin $allowed) { throw 'Unexpected update path' }
    $candidate = Join-Path $sourceRoot $file.path
    $hash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash
    if ($hash -ne $file.sha256) { throw 'Update checksum mismatch' }
}
$taskName = 'ServerNetworkAssist-Desktop'
$task = Get-ScheduledTask -TaskName $taskName
if ($task.Actions.Execute -ne (Join-Path $installRoot 'pythonw.exe')) { throw 'Unexpected desktop task executable' }
$folders = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'
$dataRoot = Join-Path $folders.'Local AppData' 'ServerNetworkAssist'
$stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID
$backup = Join-Path 'C:\ProgramData\ServerNetworkAssist\desktop-ui-backups' $stamp
[IO.Directory]::CreateDirectory($backup) | Out-Null
& icacls.exe $backup /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect update backup' }
foreach ($file in $manifest.files) {
    $original = Join-Path $packageRoot $file.path
    if (Test-Path -LiteralPath $original) {
        $destination = Join-Path $backup $file.path
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
        Copy-Item -LiteralPath $original -Destination $destination
    }
}
function Stop-Panel {
    Stop-ScheduledTask -TaskName $taskName
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        if ((Get-ScheduledTask -TaskName $taskName).State -ne 'Running') { return }
        Start-Sleep -Milliseconds 200
    }
    throw 'Desktop task did not stop'
}
function Test-Panel {
    $instanceFile = Join-Path $dataRoot 'desktop-instance.json'
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try {
            $state = Get-Content -LiteralPath $instanceFile -Raw | ConvertFrom-Json
            $baseUrl = 'http://127.0.0.1:' + $state.port
            $headers = @{ 'X-Desktop-Token' = $state.token }
            $health = Invoke-RestMethod -Uri ($baseUrl + '/api/health') -Headers $headers -TimeoutSec 2
            if ($health.app -ne 'server-network-assist-desktop') { throw 'Wrong application' }
            $css = Invoke-WebRequest -Uri ($baseUrl + '/desktop.css') -UseBasicParsing -TimeoutSec 2
            if ($css.StatusCode -ne 200) { throw 'New UI stylesheet not served' }
            return
        } catch { Start-Sleep -Milliseconds 250 }
    }
    throw 'Updated desktop did not pass health and UI asset checks'
}
Stop-Panel
try {
    foreach ($file in $manifest.files) {
        $destination = Join-Path $packageRoot $file.path
        Copy-Item -LiteralPath (Join-Path $sourceRoot $file.path) -Destination $destination -Force
        if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $file.sha256) { throw 'Installed checksum mismatch' }
    }
    Start-ScheduledTask -TaskName $taskName
    Test-Panel
} catch {
    $failure = $_
    Stop-Panel
    foreach ($file in $manifest.files) {
        $original = Join-Path $backup $file.path
        if (Test-Path -LiteralPath $original) {
            Copy-Item -LiteralPath $original -Destination (Join-Path $packageRoot $file.path) -Force
        }
    }
    Start-ScheduledTask -TaskName $taskName
    throw $failure
}
@{ updated = $true; backup = $backup; files = $manifest.files.Count; wireguardChanged = $false } | ConvertTo-Json -Compress
