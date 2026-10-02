$ErrorActionPreference = 'Stop'
$root = 'C:\Users\hmw20\.ssh\gui-d321-control-20260917'
foreach ($name in @('inspect.ps1','input.ps1','invoke.ps1','launch.ps1')) {
    $tokens = $null
    $errors = $null
    $path = Join-Path $root $name
    [System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors) | Out-Null
    if ($errors.Count) { throw ($name + ': ' + ($errors.Message -join '; ')) }
    Write-Output ($name + ': syntax OK')
}
foreach ($name in @('local_gui_safety_exit.ps1','titan_local_gui_resume_task.ps1','audit_local_gui_network_baseline.ps1')) {
    $tokens = $null
    $errors = $null
    $path = Join-Path $PSScriptRoot $name
    [System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors) | Out-Null
    if ($errors.Count) { throw ($name + ': ' + ($errors.Message -join '; ')) }
    Write-Output ($name + ': syntax OK')
}
