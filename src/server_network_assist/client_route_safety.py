"""Deterministic Windows route preflight for a customer WireGuard tunnel."""
from __future__ import annotations

import hashlib
import ipaddress
import json

from .client_subscription import _safe_host_port


def _integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{label} 缺失或无效')
    return value


def _route(row):
    if not isinstance(row, dict):
        raise ValueError('Windows 路由快照无效')
    try:
        network = ipaddress.ip_network(str(row['destination_prefix']), strict=True)
        next_hop = str(ipaddress.ip_address(str(row['next_hop'])))
    except (KeyError, ValueError) as exc:
        raise ValueError('Windows 路由目标或下一跳无效') from exc
    if network.version != 4 or ipaddress.ip_address(next_hop).version != 4:
        raise ValueError('源网安全检查只接受 IPv4 路由')
    state = row.get('connection_state')
    connected = state in (1, 'Connected', 'connected')
    return {
        'destination_prefix': str(network),
        'next_hop': next_hop,
        'interface_index': _integer(row.get('interface_index'), '路由接口'),
        'route_metric': _integer(row.get('route_metric'), '路由 metric'),
        'interface_metric': _integer(row.get('interface_metric'), '接口 metric'),
        'connection_state': 'connected' if connected else 'disconnected',
        'interface_alias': str(row.get('interface_alias') or ''),
    }


def _routes(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('supported') is not True:
        raise ValueError('Windows 网络快照不完整')
    rows = snapshot.get('routes')
    if not isinstance(rows, list):
        raise ValueError('Windows 网络快照缺少完整路由')
    return [_route(row) for row in rows]


def _physical_links(snapshot):
    links = snapshot.get('links')
    if not isinstance(links, list):
        raise ValueError('Windows 网络快照缺少物理接口')
    result = []
    for row in links:
        if not isinstance(row, dict):
            raise ValueError('Windows 物理接口快照无效')
        index = _integer(row.get('id'), '物理接口索引')
        addresses = []
        for value in row.get('addresses', []):
            try:
                address = ipaddress.ip_address(str(value))
            except ValueError as exc:
                raise ValueError('Windows 物理接口地址无效') from exc
            if address.version == 4:
                addresses.append(str(address))
        result.append({
            'id': index,
            'connected': row.get('connected') is True,
            'addresses': sorted(addresses),
            'name': str(row.get('name') or ''),
            'profile': str(row.get('profile') or ''),
            'wifi': row.get('wifi') is True,
            'defaults': sorted((str(item.get('gateway') or ''), item.get('metric'))
                               for item in row.get('defaults', []) if isinstance(item, dict)),
            'defaults6': sorted((str(item.get('gateway') or ''), item.get('metric'))
                                for item in row.get('defaults6', []) if isinstance(item, dict)),
        })
    return result


def endpoint_ipv4(endpoint):
    host, _ = _safe_host_port(str(endpoint))
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError('源网 WireGuard Endpoint 必须是可审计的 IPv4 地址') from exc
    if address.version != 4:
        raise ValueError('源网 WireGuard Endpoint 必须使用 IPv4')
    return address


def endpoint_binding(snapshot, endpoint, campus_cidrs):
    """Return the unique active best route to an endpoint.

    Windows chooses longest prefix first and then the sum of route and
    interface metrics. Equal best paths are rejected instead of guessed.
    """
    address = endpoint_ipv4(endpoint)
    networks = tuple(ipaddress.ip_network(value, strict=True) for value in campus_cidrs)
    if not networks:
        raise ValueError('缺少受信任校园网段')
    links = _physical_links(snapshot)
    candidates = []
    for row in _routes(snapshot):
        network = ipaddress.ip_network(row['destination_prefix'])
        if row['connection_state'] == 'connected' and address in network:
            candidates.append((network.prefixlen,
                               row['route_metric'] + row['interface_metric'], row))
    if not candidates:
        raise ValueError('没有到源网 Endpoint 的活动路由')
    best_prefix = max(item[0] for item in candidates)
    best_metric = min(item[1] for item in candidates if item[0] == best_prefix)
    best = [item[2] for item in candidates
            if item[0] == best_prefix and item[1] == best_metric]
    paths = {(row['destination_prefix'], row['next_hop'], row['interface_index']) for row in best}
    if len(paths) != 1:
        raise ValueError('源网 Endpoint 存在等价最佳路由，不能证明唯一物理出口')
    selected = best[0]
    link = next((row for row in links
                 if row['id'] == selected['interface_index'] and row['connected']), None)
    if link is None:
        raise ValueError('源网 Endpoint 当前被 VPN/TUN 或非物理接口接管')
    campus = any(ipaddress.ip_address(value) in network
                 for value in link['addresses'] for network in networks)
    if not campus:
        raise ValueError('源网 Endpoint 的最佳路径不是校园物理接口')
    competing = [row for row in links if row['connected'] and row['id'] != link['id']
                 and (row['defaults'] or row['defaults6'])]
    if competing:
        raise ValueError('检测到并行物理默认出口，不能安全启动源网隧道')
    return {
        'endpoint_ip': str(address),
        'interface_index': link['id'],
        'adapter': {
            'id': link['id'], 'addresses': link['addresses'], 'name': link['name'],
            'profile': link['profile'], 'wifi': link['wifi'],
        },
        'route': selected,
        'total_metric': best_metric,
    }


def _route_key(row):
    return (row['destination_prefix'], row['next_hop'], row['interface_index'],
            row['route_metric'], row['interface_metric'], row['connection_state'])


def snapshot_fingerprint(snapshot):
    value = {
        'routes': sorted(_route_key(row) for row in _routes(snapshot)),
        'links': sorted((row['id'], row['connected'], tuple(row['addresses']),
                         tuple(row['defaults']), tuple(row['defaults6']))
                        for row in _physical_links(snapshot)),
    }
    encoded = json.dumps(value, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _protected_records(snapshot):
    records = []
    seen = set()
    for row in _routes(snapshot):
        network = ipaddress.ip_network(row['destination_prefix'])
        # /1 routes are split defaults owned by a full-tunnel product, not a
        # dedicated management/authentication path. Existing Clash coexistence
        # handles those broad capture routes separately.
        if row['connection_state'] != 'connected' or network.prefixlen <= 1:
            continue
        key = _route_key(row)
        if key not in seen:
            seen.add(key)
            records.append(row)
    if len(records) > 512:
        raise ValueError('既有专用路由过多，拒绝生成不可审计的分流配置')
    return sorted(records, key=_route_key)


def build_route_guard(before, after, endpoint, campus_cidrs):
    """Validate the second snapshot and return a JSON-safe protection ledger."""
    original_binding = endpoint_binding(before, endpoint, campus_cidrs)
    current_binding = endpoint_binding(after, endpoint, campus_cidrs)
    if original_binding != current_binding or snapshot_fingerprint(before) != snapshot_fingerprint(after):
        raise ValueError('租约签发期间物理接口、Endpoint 路径或路由表发生变化，请重新连接')
    records = _protected_records(before)
    endpoint_host = ipaddress.ip_network(f'{original_binding["endpoint_ip"]}/32')
    protected = [ipaddress.ip_network(row['destination_prefix']) for row in records]
    protected.append(endpoint_host)
    cidrs = [str(item) for item in ipaddress.collapse_addresses(protected)]
    return {
        'schema_version': 1,
        'snapshot_sha256': snapshot_fingerprint(before),
        'endpoint': original_binding,
        'protected_routes': records,
        'protected_cidrs': cidrs,
    }


def verify_protected_paths(guard, current, campus_cidrs):
    if not isinstance(guard, dict) or guard.get('schema_version') != 1:
        raise ValueError('客户隧道路由保护记录无效')
    expected_binding = guard.get('endpoint')
    endpoint = str(expected_binding.get('endpoint_ip')) + ':1' if isinstance(expected_binding, dict) else ''
    if endpoint_binding(current, endpoint, campus_cidrs) != expected_binding:
        raise ValueError('源网 Endpoint 的物理出口发生变化')
    current_routes = {_route_key(row) for row in _routes(current)}
    missing = [row['destination_prefix'] for row in guard.get('protected_routes', [])
               if _route_key(_route(row)) not in current_routes]
    if missing:
        raise ValueError('既有专用路由已变化或消失：' + ', '.join(missing[:6]))
    return True
