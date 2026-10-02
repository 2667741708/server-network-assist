param(
    [Parameter(Mandatory=$true)][string]$BundlePath,
    [switch]$Elevated,
    [switch]$SkipWebView2
)
$ErrorActionPreference = 'Stop'
$bundle = [IO.Path]::GetFullPath($BundlePath)
$architecture = $env:PROCESSOR_ARCHITEW6432
if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITECTURE }
if ($architecture -ne 'AMD64') { throw 'This offline package requires Windows x64.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$administrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $administrator) {
    if ($Elevated) { throw 'Administrator approval was not granted.' }
    $powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),'-BundlePath',('"' + $bundle + '"'),'-Elevated')
    if ($SkipWebView2) { $arguments += '-SkipWebView2' }
    $process = Start-Process -FilePath $powerShell -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -notin @(0,3010)) { throw 'Dependency setup failed or administrator approval was cancelled.' }
    exit $process.ExitCode
}
$manifestName = -join ([char[]]@(0x6587,0x4ef6,0x6821,0x9a8c))
$manifestPath = Join-Path $bundle ($manifestName + '.json')
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
function Get-VerifiedInstaller([string]$Name,[string]$SignerPattern) {
    $path = Join-Path $bundle ($Name.Replace('/','\'))
    $expected = $manifest.files.PSObject.Properties[$Name].Value
    if (-not $expected) { throw 'Missing dependency checksum.' }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    if ($actual -ne $expected) { throw 'Dependency checksum mismatch.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch $SignerPattern) { throw 'Dependency publisher signature is not valid.' }
    return $path
}
# Verify both files before starting either installer. No client tunnel is installed.
$wireguard = Get-VerifiedInstaller 'dependencies/wireguard-amd64.msi' '(CN|O)=WireGuard LLC(?:,|$)'
$webview = Get-VerifiedInstaller 'dependencies/MicrosoftEdgeWebView2RuntimeInstallerX64.exe' '(CN|O)=Microsoft Corporation(?:,|$)'
$wgDirectory = Join-Path $env:ProgramFiles 'WireGuard'
$wgMissing = (-not (Test-Path -LiteralPath (Join-Path $wgDirectory 'wireguard.exe') -PathType Leaf)) -or (-not (Test-Path -LiteralPath (Join-Path $wgDirectory 'wg.exe') -PathType Leaf))
if ($wgMissing) {
    $msiexec = Join-Path $env:SystemRoot 'System32\msiexec.exe'
    $process = Start-Process -FilePath $msiexec -ArgumentList @('/i',('"' + $wireguard + '"'),'/qn','/norestart','DO_NOT_LAUNCH=1') -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -notin @(0,3010)) { throw ('WireGuard installation failed: ' + $process.ExitCode) }
    if ($process.ExitCode -eq 3010) { exit 3010 }
}
$runtimeKeys = @('HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}','HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')
$runtimeInstalled = $false
foreach ($key in $runtimeKeys) {
    $item = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
    if ($item -and $item.pv -and $item.pv -ne '0.0.0.0') { $runtimeInstalled = $true }
}
if (-not $runtimeInstalled -and -not $SkipWebView2) {
    $process = Start-Process -FilePath $webview -ArgumentList @('/silent','/install') -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -notin @(0,3010)) { throw ('WebView2 installation failed: ' + $process.ExitCode) }
    if ($process.ExitCode -eq 3010) { exit 3010 }
}
exit 0
