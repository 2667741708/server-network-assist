$ErrorActionPreference = 'Stop'
$errors = $null
$tokens = $null
$file = Join-Path $PSScriptRoot 'customer_install_windows.ps1'
[System.Management.Automation.Language.Parser]::ParseFile($file,[ref]$tokens,[ref]$errors) | Out-Null
if ($errors.Count) { throw ($errors.Message -join '; ') }
Write-Output 'Customer installer helper: syntax OK'
