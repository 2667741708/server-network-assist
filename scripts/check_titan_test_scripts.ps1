$ErrorActionPreference = 'Stop'
foreach ($name in @('titan_restore_test_adapter.ps1','titan_drop_test_adapter.ps1','titan_schedule_link_loss.ps1')) {
 $parseErrors = $null
 $tokens = $null
 $path = Join-Path $PSScriptRoot $name
 [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$parseErrors) | Out-Null
 if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
 Write-Output "$name syntax OK"
}
