$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'src\server_network_assist\windows_helper.ps1') -FunctionsOnly
function Assert-True($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
$state = @{ profile_id='test'; interface='na1234567890'; subnet='10.213.1.0/24'; port=51919; proxy_mode='direct'; forwarding=@() }
$script:changes = @()
function Get-NetIPInterface { param($InterfaceAlias,$InterfaceIndex,$AddressFamily,$ErrorAction) return [pscustomobject]@{InterfaceIndex=15;Forwarding='Disabled'} }
function Get-GatewayUplink { return 15 }
function Save-State($State) {}
function Set-NetIPInterface { param($InterfaceIndex,$AddressFamily,$Forwarding) $script:changes += $Forwarding }
function New-NetNat { param($Name,$InternalIPInterfaceAddressPrefix) Assert-True $state.nat_owned 'NAT must be journaled first'; throw 'simulated NAT failure' }
$failed = $false
try { Enable-Gateway $state } catch { $failed = $_.Exception.Message -eq 'simulated NAT failure' }
Assert-True $failed 'Expected simulated NAT failure'
Assert-True ($state.forwarding.Count -eq 2) 'Both forwarding changes must be journaled'
function Get-NetNat { param($Name,$ErrorAction) return $null }
Restore-Gateway $state
Assert-True (-not $state.nat_owned -and $state.forwarding.Count -eq 0) 'Cleanup must clear successful NAT/forwarding journals'
Assert-True ($script:changes[-1] -eq 'Disabled') 'Forwarding must be restored'
Write-Output 'PASS gateway partial failure restores forwarding and NAT ownership'
$state.nat_owned = $true
function Get-NetNat { param($Name,$ErrorAction) return [pscustomobject]@{InternalIPInterfaceAddressPrefix='10.222.0.0/24'} }
function Remove-NetNat { throw 'Must not remove changed NAT' }
$failed = $false
try { Restore-Gateway $state } catch { $failed = $_.Exception.Message -like 'Owned NAT changed*' }
Assert-True $failed 'Changed NAT must not be deleted'
Assert-True $state.nat_owned 'Failed cleanup must retain journal'
Write-Output 'PASS changed NAT is preserved for operator review'
function Set-ItemProperty { throw 'Direct mode must not change user proxy settings' }
Set-ClientProxy @{proxy_mode='direct'}
Write-Output 'PASS direct mode preserves user proxy'
