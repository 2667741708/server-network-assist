$ErrorActionPreference='Stop'
if([Environment]::MachineName -ne 'DESKTOP-TD6B9GN'){throw 'D321 only'}
Unregister-ScheduledTask -TaskName 'SNA-D321-SourceStop-Off-Rescue' -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'SNA-D321-SourceStop-Action' -Confirm:$false -ErrorAction SilentlyContinue
Write-Output 'Removed only the two temporary proxy experiment tasks'
