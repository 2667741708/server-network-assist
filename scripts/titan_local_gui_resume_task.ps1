$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne 'DESKTOP-TD6B9GN') { throw 'Titan only' }
$root = 'C:\Users\86133\sna-local-gui-goal-20260917'
$old = Get-ScheduledTask -TaskName 'SNA-Codex-Local-Gui-20260917'
if ($old.State -eq 'Running') { throw 'Old actor still running; do not duplicate' }
$name = 'SNA-Codex-Local-Gui-Resume-20260917'
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq 'Running') { throw 'Resume actor already running' }
$action = New-ScheduledTaskAction -Execute 'C:\Users\86133\.codex\tools\Python313\pythonw.exe' -Argument (Join-Path $root 'runner-resume.py')
$principal = New-ScheduledTaskPrincipal -UserId 'd321-titan' -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Output 'Authorized Codex GUI actor resume started; no boot trigger'
