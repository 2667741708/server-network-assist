param([ValidateSet('Register','Start','Status')][string]$Action)
$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'D321 only' }
$name = 'SNA-Codex-D321-Wired-Gui-20260917'
switch ($Action) {
    'Register' {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { throw 'Task already exists; inspect before replacing' }
        $runner = 'C:\Users\86133\sna-d321-wired-gui-20260917\titan_wired_codex_runner.py'
        $command = New-ScheduledTaskAction -Execute 'C:\Users\86133\.codex\tools\Python313\pythonw.exe' -Argument $runner
        $principal = New-ScheduledTaskPrincipal -UserId 'd321-titan' -LogonType Interactive -RunLevel Highest
        Register-ScheduledTask -TaskName $name -Action $command -Principal $principal | Out-Null
        Write-Output 'Registered interactive manual task; not started'
    }
    'Start' { Start-ScheduledTask -TaskName $name }
    'Status' { Get-ScheduledTask -TaskName $name | Select-Object TaskName,State | ConvertTo-Json -Compress }
}
