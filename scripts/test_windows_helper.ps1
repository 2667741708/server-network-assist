$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'src\server_network_assist\windows_helper.ps1') -FunctionsOnly

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
$script:journal = @()
$script:created = @()
function Get-NetRoute { return $null }
function Find-NetRoute { return [pscustomobject]@{ NextHop = '192.0.2.1'; InterfaceIndex = 15 } }
function Save-State($State) { $script:journal += ,@($State.added_routes) }
function New-NetRoute {
    param($DestinationPrefix, $InterfaceIndex, $NextHop, $RouteMetric, $PolicyStore)
    Assert-True ($script:journal[-1][-1].prefix -eq $DestinationPrefix) 'Route must be journaled before mutation'
    if ($DestinationPrefix -eq '198.51.100.2/32') { throw 'simulated route creation failure' }
    $script:created += $DestinationPrefix
}
$state = @{ profile_id = 'test'; preserve_routes = @('198.51.100.1/32','198.51.100.2/32'); endpoint = '192.0.2.9'; added_routes = @() }
$failed = $false
try { Preserve-Routes $state } catch { $failed = $_.Exception.Message -eq 'simulated route creation failure' }
Assert-True $failed 'Expected partial route failure'
Assert-True ($state.added_routes.Count -eq 2) 'Failed route and prior route must remain journaled'
Assert-True ($script:created.Count -eq 1) 'Only the first route should exist'

function Read-State { return $state }
function Remove-Task {}
function Get-Service { return $null }
$script:removed = @()
function Get-NetRoute {
    param($DestinationPrefix, $InterfaceIndex, $NextHop, $PolicyStore, $ErrorAction)
    if ($DestinationPrefix -in $script:created) { return [pscustomobject]@{ prefix = $DestinationPrefix } }
}
function Remove-NetRoute {
    param([Parameter(ValueFromPipeline)]$Route, [switch]$Confirm)
    process { $script:removed += $Route.prefix }
}
$state.interface = 'na1234567890'
$state.active = $true
$state.desired = $true
Disable-Profile 'test'
Assert-True ($script:removed.Count -eq 1) 'Rollback must only remove routes that exist'
Assert-True (-not $state.active -and -not $state.desired) 'Rollback must disable profile'
Assert-True ($state.added_routes.Count -eq 0) 'Successful rollback must clear the journal'
Write-Output 'PASS partial route failure journals and rolls back only owned routes'

$state.added_routes = @(@{prefix = '198.51.100.1/32'; index = 15; hop = '192.0.2.1'})
function Remove-NetRoute { throw 'simulated cleanup failure' }
$failed = $false
try { Disable-Profile 'test' } catch { $failed = $true }
Assert-True $failed 'Cleanup failures must be visible'
Assert-True ($state.added_routes.Count -eq 1) 'Failed cleanup must keep the route journal for retry'
Write-Output 'PASS failed cleanup retains its recovery journal'
