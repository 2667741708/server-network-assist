param(
    [Parameter(Mandatory=$true)][ValidateSet('EnsureGateway','ApplyPeer','RemovePeer')][string]$Action,
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_.-]{1,80}$')][string]$PeerId,
    [Parameter(Mandatory=$true)][string]$Interface,
    [Parameter(Mandatory=$true)][string]$EgressInterface,
    [Parameter(Mandatory=$true)][string]$Address,
    [Parameter(Mandatory=$true)][string]$CustomerSubnet,
    [string]$ManagementSubnets = ''
)
$ErrorActionPreference = 'Stop'
$natName = 'SNA-CommercialRelay'
$rulePrefix = 'SNA-Commercial-' + $PeerId
$clientAddress = $Address.Split('/')[0]

function Ensure-Gateway {
    $adapter = Get-NetAdapter -Name $Interface -ErrorAction Stop
    $egress = Get-NetAdapter -Name $EgressInterface -ErrorAction Stop
    Set-NetIPInterface -InterfaceIndex $adapter.InterfaceIndex -AddressFamily IPv4 -Forwarding Enabled
    Set-NetIPInterface -InterfaceIndex $egress.InterfaceIndex -AddressFamily IPv4 -Forwarding Enabled
    $owned = Get-NetNat -Name $natName -ErrorAction SilentlyContinue
    $all = @(Get-NetNat -ErrorAction Stop)
    if (-not $owned -and $all.Count -gt 0) {
        throw 'An existing WinNAT is present; refusing to modify or reuse it.'
    }
    if ($owned -and $owned.InternalIPInterfaceAddressPrefix -ne $CustomerSubnet) {
        throw 'The owned WinNAT uses a different customer subnet.'
    }
    if (-not $owned) {
        New-NetNat -Name $natName -InternalIPInterfaceAddressPrefix $CustomerSubnet | Out-Null
    }
}

function Remove-PeerRules {
    Get-NetFirewallRule -Name ($rulePrefix + '-*') -ErrorAction SilentlyContinue | Remove-NetFirewallRule
}

function Apply-PeerRules {
    Remove-PeerRules
    New-NetFirewallRule -Name ($rulePrefix + '-clients') -DisplayName 'SNA commercial client isolation' -Direction Inbound -Action Block -InterfaceAlias $Interface -RemoteAddress $clientAddress -LocalAddress $CustomerSubnet -Profile Any | Out-Null
    $networks = @($ManagementSubnets.Split(',') | Where-Object { $_ })
    $index = 0
    foreach ($network in $networks) {
        New-NetFirewallRule -Name ($rulePrefix + '-management-' + $index) -DisplayName 'SNA commercial management isolation' -Direction Inbound -Action Block -InterfaceAlias $Interface -RemoteAddress $clientAddress -LocalAddress $network -Profile Any | Out-Null
        $index++
    }
}

if ($Action -eq 'EnsureGateway') { Ensure-Gateway }
elseif ($Action -eq 'ApplyPeer') { Apply-PeerRules }
elseif ($Action -eq 'RemovePeer') { Remove-PeerRules }
