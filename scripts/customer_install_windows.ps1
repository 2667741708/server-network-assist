param(
    [Parameter(Mandatory=$true)][ValidateSet('prepare','shortcut')][string]$Action,
    [Parameter(Mandatory=$true)][string]$DataPath,
    [string]$Executable
)
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($Action -eq 'prepare') {
    if (-not (Test-Path -LiteralPath $DataPath)) {
        New-Item -ItemType Directory -Path $DataPath | Out-Null
    }
    & icacls.exe $DataPath /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot protect customer configuration directory.' }
    exit 0
}
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { throw 'Installed EXE not found.' }
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$name = -join ([char[]]@(0x7eaf,0x4eab,0x5165,0x7f51))
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $desktop ($name + '.lnk')))
$link.TargetPath = $Executable
$link.Arguments = '--data "' + $DataPath + '"'
$link.WorkingDirectory = Split-Path -Parent $Executable
$link.IconLocation = $Executable + ',0'
$link.Save()
