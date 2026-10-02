param(
    [Parameter(Mandatory=$true)][string]$SourceExe,
    [Parameter(Mandatory=$true)][string]$ExpectedHash,
    [Parameter(Mandatory=$true)][string]$ResultFile,
    [string]$DataDirectory,
    [string]$DesktopDirectory,
    [string]$ProgramsDirectory
)
$ErrorActionPreference = 'Stop'
$result = [ordered]@{ installed=$false; old_backend_stopped=$false; client_started=$false }
function Save-Result {
    try { $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ResultFile -Encoding UTF8 }
    catch { Write-Warning 'Optional install report could not be written.' }
}
function Network-Fingerprint {
    $routes = @(Get-NetRoute | Select-Object DestinationPrefix,InterfaceIndex,NextHop,RouteMetric | Sort-Object DestinationPrefix,InterfaceIndex,NextHop,RouteMetric)
    $dns = @(Get-DnsClientServerAddress | Select-Object InterfaceIndex,AddressFamily,ServerAddresses | Sort-Object InterfaceIndex,AddressFamily)
    $proxy = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' | Select-Object ProxyEnable,ProxyServer,ProxyOverride,AutoConfigURL
    $json = @{ routes=$routes; dns=$dns; proxy=$proxy } | ConvertTo-Json -Depth 8 -Compress
    $sha = [Security.Cryptography.SHA256]::Create()
    try { [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($json))) }
    finally { $sha.Dispose() }
}
try {
    if ((Get-FileHash -LiteralPath $SourceExe -Algorithm SHA256).Hash -ne $ExpectedHash) { throw 'EXE hash mismatch' }
    $before = Network-Fingerprint
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $clientRoot = Join-Path $env:ProgramData 'ServerNetworkAssist\client'
    $oldData = Join-Path $clientRoot $sid
    $data = Join-Path $oldData 'gui-protected-20260917'
    if ($DataDirectory) { $data = $DataDirectory }
    $data = [IO.Path]::GetFullPath($data)
    $programs = Join-Path $env:LOCALAPPDATA 'Programs\PureNetworkClient'
    if ($ProgramsDirectory) { $programs = $ProgramsDirectory }
    $programs = [IO.Path]::GetFullPath($programs)
    $installed = Join-Path $programs ('gui-' + $ExpectedHash.Substring(0,12))
    [IO.Directory]::CreateDirectory($installed) | Out-Null
    $target = Join-Path $installed 'ServerNetworkAssistClient.exe'
    Copy-Item -LiteralPath $SourceExe -Destination $target -Force
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $ExpectedHash) { throw 'Installed EXE hash mismatch' }
    # Legacy idle API responses do not authorize killing that backend.
    # Never start/stop processes, tasks or network services during installation.
    $desktop = [Environment]::GetFolderPath('Desktop')
    if ($DesktopDirectory) { $desktop = $DesktopDirectory }
    if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) { throw 'Specify the actual desktop directory; SSH may not load shell folders' }
    $shell = New-Object -ComObject WScript.Shell
    $name = -join ([char[]]@(0x7EAF,0x4EAB,0x5165,0x7F51))
    $paths = @((Join-Path $desktop ($name + '.lnk')))
    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $previous = $shell.CreateShortcut($path)
            if (-not $previous.TargetPath.StartsWith(($programs + '\'), [StringComparison]::OrdinalIgnoreCase)) { throw 'Existing shortcut belongs to another application' }
        }
        $shortcut = $shell.CreateShortcut($path)
        $shortcut.TargetPath = $target
        $shortcut.Arguments = '--data "' + $data + '"'
        $shortcut.WorkingDirectory = $installed
        $shortcut.IconLocation = $target + ',0'
        $shortcut.Description = 'Protected GUI client. Subscribe and connect manually.'
        $shortcut.Save()
    }
    # Retire only two previously created shortcuts pointing to our installed programs.
    # Never delete an unrelated user shortcut or touch the running client.
    $oldName = -join ([char[]]@(0x501F,0x7F51,0x5BA2,0x6237,0x7AEF))
    $clientName = -join ([char[]]@(0x5165,0x7F51,0x5BA2,0x6237,0x7AEF))
    $oldPaths = @((Join-Path $desktop ($oldName + '.lnk')), (Join-Path $desktop ($clientName + '.lnk')), (Join-Path $desktop 'Borrow Network Client - Monthly Test.lnk'))
    $retired = @()
    foreach ($oldPath in $oldPaths) {
        if (-not (Test-Path -LiteralPath $oldPath -PathType Leaf)) { continue }
        $oldShortcut = $shell.CreateShortcut($oldPath)
        $owned = $oldShortcut.TargetPath.StartsWith(($programs + '\'), [StringComparison]::OrdinalIgnoreCase)
        if ($owned) {
            Remove-Item -LiteralPath $oldPath
            $retired += $oldPath
        }
    }
    $after = Network-Fingerprint
    $result.network_fingerprint_unchanged = ($before -eq $after)
    $result.installed = $true
    $result.exe = $target
    $result.data = $data
    $result.sha256 = $ExpectedHash
    $result.desktop_shortcut = $paths[0]
    $result.retired_shortcuts = $retired
    Save-Result
} catch {
    $result.error = $_.Exception.Message
    Save-Result
    throw
}
