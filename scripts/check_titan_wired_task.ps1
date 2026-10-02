$ErrorActionPreference = 'Stop'
$path = Join-Path $PSScriptRoot 'titan_wired_codex_task.ps1'
$tokens = $null
$parseErrors = $null
[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$parseErrors) | Out-Null
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
Write-Output 'Task script syntax valid'
