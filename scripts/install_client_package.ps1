param(
    [Parameter(Mandatory=$true)][string]$RuntimeSource,
    [Parameter(Mandatory=$true)][string]$PackageArchive,
    [Parameter(Mandatory=$true)][string]$PackageSha256,
    [string]$Version = '0.7.0'
)
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run as Administrator' }
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid version' }
$archive = (Resolve-Path -LiteralPath $PackageArchive).Path
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $PackageSha256) { throw 'Package hash mismatch' }
$source = (Resolve-Path -LiteralPath $RuntimeSource).Path
$stdlib = Get-ChildItem -LiteralPath $source -Filter 'python3*.zip' -File | Select-Object -First 1
if (-not $stdlib) { throw 'Embedded Python runtime required' }
$target = Join-Path 'C:\ProgramData\ServerNetworkAssist\desktop' $Version
if (Test-Path -LiteralPath $target) { throw 'Runtime already exists; inspect before replacing' }
[IO.Directory]::CreateDirectory($target) | Out-Null
& icacls.exe $target /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot secure runtime' }
foreach ($file in Get-ChildItem -LiteralPath $source -File) {
    Copy-Item -LiteralPath $file.FullName -Destination $target -Force
}
$pathFile = Join-Path $target ($stdlib.BaseName + '._pth')
[IO.File]::WriteAllText($pathFile, "$($stdlib.Name)`n.`npackages`n", [Text.UTF8Encoding]::new($false))
$python = Join-Path $target 'python.exe'
& $python -m zipfile -e $archive (Join-Path $target 'packages')
if ($LASTEXITCODE -ne 0) { throw 'Cannot extract package' }
& $python -c 'import server_network_assist.client, server_network_assist.client_tunnel'
if ($LASTEXITCODE -ne 0) { throw 'Client imports failed' }
& (Join-Path $PSScriptRoot 'install_client.ps1') -Version $Version
