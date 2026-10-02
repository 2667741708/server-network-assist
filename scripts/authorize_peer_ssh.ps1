param([string]$PeerKeyPath,[string]$ExpectedMachine,[string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ne $ExpectedMachine) { throw 'Unexpected machine' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrative file authorization requires elevation' }
$key = (Get-Content -LiteralPath $PeerKeyPath -Raw).Trim()
if ($key -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+ [A-Za-z0-9._-]+$') { throw 'Expected dedicated public key' }
$target = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
$existing = ''
if (Test-Path -LiteralPath $target) { $existing = Get-Content -LiteralPath $target -Raw }
$parts = $key.Split(' ')
if ($existing -notmatch [regex]::Escape($parts[1])) {
    if (Test-Path -LiteralPath $target) { Copy-Item -LiteralPath $target -Destination ($target + '.peer-backup-' + (Get-Date -Format 'yyyyMMddHHmmss')) }
    $content = $existing.TrimEnd() + "`r`n" + 'no-agent-forwarding,no-X11-forwarding ' + $key + "`r`n"
    [IO.File]::WriteAllText($target,$content,[Text.UTF8Encoding]::new($false))
}
& icacls.exe $target /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Authorization ACL failed' }
$hostKey = Get-Content -LiteralPath (Join-Path $env:ProgramData 'ssh\ssh_host_ed25519_key.pub') -Raw
[IO.File]::WriteAllText((Join-Path $OutputDirectory 'peer-server-host.pub'),$hostKey,[Text.UTF8Encoding]::new($false))
$check = (Get-Content -LiteralPath $target -Raw) -match [regex]::Escape($parts[1])
@{authorized=$check;machine=[Environment]::MachineName;sshd_running=((Get-Service sshd).Status -eq 'Running');network_settings_changed=$false;time=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $OutputDirectory 'peer-authorization-result.json') -Encoding UTF8
