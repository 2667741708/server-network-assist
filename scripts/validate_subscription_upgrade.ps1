$ErrorActionPreference = 'Stop'
foreach ($name in @('d321_subscription_upgrade.ps1','d321_subscription_task.ps1')) {
    $errors = $null
    $tokens = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $name),[ref]$tokens,[ref]$errors) | Out-Null
    if ($errors.Count) { throw ($name+': '+($errors.Message -join '; ')) }
    Write-Output ($name+': syntax OK')
}
