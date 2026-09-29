"""Administrator-declared source egress options; user clients select grants, not NICs."""
from __future__ import annotations
import ipaddress
import re
from .egress_contract import canonical_egress


def validate_egresses(value: object, relay_interface: str) -> dict:
    if not isinstance(value, dict) or not value or len(value) > 2:
        raise ValueError('egresses must declare one or two source exits')
    result = {}
    for key, config in value.items():
        mode = canonical_egress(key)
        if mode in result or not isinstance(config, dict) or set(config) - {'interface', 'gateway'}:
            raise ValueError('Duplicate or invalid source egress')
        interface = config.get('interface')
        if not isinstance(interface, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', interface) or interface == relay_interface:
            raise ValueError('Invalid source egress interface')
        gateway = config.get('gateway', '')
        if mode == 'source_physical':
            if not isinstance(gateway, str):
                raise ValueError('Physical source gateway must be an IPv4 string')
            try:
                address = ipaddress.ip_address(gateway)
            except (ValueError, TypeError):
                raise ValueError('Physical source must declare its IPv4 gateway') from None
            if address.version != 4 or address.is_unspecified or address.is_multicast or address.is_loopback:
                raise ValueError('Invalid physical source gateway')
        elif gateway:
            raise ValueError('Source proxy cannot declare a physical gateway')
        result[mode] = {'interface': interface, 'gateway': str(gateway)}
    return result


def expand_source_egresses(source: dict, requested: list[str] | None) -> list[dict]:
    """Expand only declared alternatives; never invent a proxy from its interface name."""
    if requested is not None and (not isinstance(requested, list) or not requested or len(requested) > 2):
        raise ValueError('Select one or two explicit egress modes')
    default = canonical_egress(source.get('egress_policy', source.get('egress_mode', 'source_physical')))
    modes = list(dict.fromkeys(canonical_egress(mode) for mode in requested)) if requested else [default]
    declared = validate_egresses(source['egresses'], source['relay_interface']) if 'egresses' in source else None
    expanded = []
    for mode in modes:
        if declared is not None:
            if mode not in declared:
                raise ValueError('Selected egress is not declared by this source')
            config = declared[mode]
        else:
            if mode != default:
                raise ValueError('Source must explicitly declare additional egress choices')
            config = {'interface': source['egress_interface'], 'gateway': source.get('egress_gateway', '')}
        item = dict(source, egress_policy=mode, egress_interface=config['interface'], egress_gateway=config['gateway'])
        if requested:
            item['name'] += ' · ' + ('物理出口' if mode == 'source_physical' else '源机代理')
        expanded.append(item)
    return expanded

def bind_relay_policy(policy: dict, bindings: dict) -> dict:
    """Resolve one canonical control policy against this relay's local source binding."""
    if not isinstance(policy, dict) or not isinstance(bindings, dict):
        raise ValueError("Relay policy and bindings must be objects")
    relay_interface = policy.get("interface")
    if not isinstance(relay_interface, str) or relay_interface not in bindings:
        raise ValueError("Relay egress bindings do not cover this interface")
    mode = canonical_egress(policy.get("egress_policy"))
    declared = validate_egresses(bindings[relay_interface], relay_interface)
    if mode not in declared:
        raise ValueError("Requested egress is not configured on this relay")
    binding = declared[mode]
    if policy.get("egress_interface") != binding["interface"]:
        raise ValueError("Control and local egress interface disagree")
    runtime = "physical" if mode == "source_physical" else "source_proxy"
    return dict(policy, egress_policy=mode, egress_mode=runtime,
                egress_gateway=binding["gateway"])
