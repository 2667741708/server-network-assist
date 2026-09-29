"""Explicit legacy relay adapter. Importing this module never changes the network."""
from __future__ import annotations
from .egress_contract import canonical_egress
from .source_egress import validate_egresses

LEGACY_FIELDS = {'peer_id','public_key','address','interface','egress_interface',
                 'customer_subnet','management_subnets','download_bps','upload_bps',
                 'quota_bytes','expires_at','enabled','egress_mode','egress_gateway'}

def legacy_egress_policy(policy: dict, bindings: dict) -> dict:
    """Map enum-v1 to an explicitly configured legacy target, without dropping enforcement fields.

    The caller must select this adapter for a verified legacy relay release.
    Newer expiry/accounting fields intentionally fail rather than disappear.
    """
    if not isinstance(policy, dict) or not isinstance(bindings, dict):
        raise ValueError('Policy and local bindings must be objects')
    row = dict(policy)
    if 'egress_policy' not in row:
        raise ValueError('Control policy must explicitly declare egress_policy')
    requested = canonical_egress(row.pop('egress_policy'))
    if set(row) - LEGACY_FIELDS:
        raise ValueError('Relay upgrade required: unsupported enforcement fields')
    interface = row.get('interface')
    declared = validate_egresses(bindings.get(interface), interface)
    if requested not in declared:
        raise ValueError('Requested egress is not bound on this relay')
    binding = declared[requested]
    if row.get('egress_interface') != binding['interface']:
        raise ValueError('Control and local egress interface disagree')
    mode = 'physical' if requested == 'source_physical' else 'source_proxy'
    if row.get('egress_mode', mode) != mode or row.get('egress_gateway', binding['gateway']) != binding['gateway']:
        raise ValueError('Conflicting legacy egress binding')
    row.update(egress_mode=mode, egress_gateway=binding['gateway'])
    return row
