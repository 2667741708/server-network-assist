param([string]$DesktopDirectory)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$desktopPath = [Environment]::GetFolderPath('Desktop')
if ($DesktopDirectory) { $desktopPath = $DesktopDirectory }
if (-not $desktopPath) { throw 'Specify the verified desktop; inherited SSH profile variables may be stale' }
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcutFiles = Get-ChildItem -LiteralPath $desktopPath -Filter '*.lnk'
foreach ($file in $shortcutFiles) {
    $shortcut = $shortcutShell.CreateShortcut($file.FullName)
    if ($shortcut.TargetPath -notmatch 'ServerNetworkAssist|Borrow|network-assist') { continue }
    $targetHash = $null
    if (Test-Path -LiteralPath $shortcut.TargetPath -PathType Leaf) {
        $targetHash = (Get-FileHash -LiteralPath $shortcut.TargetPath -Algorithm SHA256).Hash
    }
    [pscustomobject]@{Shortcut=$file.FullName;Target=$shortcut.TargetPath;Arguments=$shortcut.Arguments;SHA256=$targetHash} | ConvertTo-Json -Compress
}
