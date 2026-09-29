"""Canonical egress contract at persistence/API boundaries; never infer from a NIC name."""
from __future__ import annotations
import json

ALIASES = {
    'physical': 'source_physical', 'direct': 'source_physical',
    'source-physical': 'source_physical', 'source_physical': 'source_physical',
    'source-nat': 'source_physical', 'managed-proxy': 'source_proxy',
    'managed_proxy': 'source_proxy', 'source-proxy': 'source_proxy',
    'source_proxy': 'source_proxy', 'source-managed-proxy': 'source_proxy',
    'source_managed_proxy': 'source_proxy',
}
LEGACY_FIELDS = {'egress_mode', 'egress_policy', 'egress_gateway', 'source_id', 'require_source_proxy'}

def canonical_egress(value: object) -> str:
    """Accept explicit legacy JSON policy objects and canonical enums, fail closed otherwise."""
    if isinstance(value, str) and value.lstrip().startswith('{'):
        if len(value) > 4096:
            raise ValueError('Egress policy is too large')
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise ValueError('Invalid legacy egress policy JSON') from None
    if isinstance(value, dict):
        if set(value) - LEGACY_FIELDS:
            raise ValueError('Unknown legacy egress policy field')
        modes = [value[key] for key in ('egress_mode', 'egress_policy') if key in value]
        if not modes or any(not isinstance(mode, str) or mode.lstrip().startswith('{') for mode in modes):
            raise ValueError('Legacy egress policy must declare its mode')
        normalized = [canonical_egress(mode) for mode in modes]
        if len(set(normalized)) != 1:
            raise ValueError('Conflicting legacy egress modes')
        requested_proxy = value.get('require_source_proxy')
        if requested_proxy is not None and (type(requested_proxy) is not bool or requested_proxy != (normalized[0] == 'source_proxy')):
            raise ValueError('Conflicting source proxy requirement')
        if normalized[0] == 'source_proxy' and value.get('egress_gateway'):
            raise ValueError('Source proxy policy cannot carry a physical gateway')
        return normalized[0]
    if not isinstance(value, str):
        raise ValueError('Egress policy must explicitly declare a supported mode')
    mode = ALIASES.get(value.strip().lower().replace(' ', '-'))
    if mode is None:
        raise ValueError('Unsupported egress policy')
    return mode
