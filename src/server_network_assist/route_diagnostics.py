"""Read-only routing evidence and profile runtime summaries."""
from __future__ import annotations

import ipaddress
import time


def return_target(value):
    if not value:
        return ''
    address = ipaddress.ip_address(str(value))
    if address.version != 4 or address.is_unspecified or address.is_multicast:
        raise ValueError('回程测试地址必须是有效 IPv4 地址')
    return str(address)


def routing_evidence(routes, public_route='', return_route=''):
    split_tunnels = {r.get('dev', '') for r in routes
                     if r.get('dst') in ('0.0.0.0/1', '128.0.0.0/1') and r.get('dev')}
    # Private-network-excluding clients use many prefixes instead of /1.
    networks = {}
    for route in routes:
        try:
            network = ipaddress.ip_network(route.get('dst', ''), strict=False)
        except ValueError:
            continue
        if network.version == 4 and network.prefixlen and route.get('dev'):
            networks.setdefault(route['dev'], []).append(network)
    segmented = {dev for dev, values in networks.items()
                 if sum(n.num_addresses for n in ipaddress.collapse_addresses(values)) >= 2 ** 31}
    tunnels = sorted(split_tunnels | segmented)
    warnings = []
    if split_tunnels:
        warnings.append('主路由表存在 VPN 全局 /1 路由；跨子网内网回程可能被接管，请检查回程路径。')
    if len(tunnels) > 1:
        warnings.append('多套 VPN 同时安装全局路由，存在出口冲突。')
    if return_route and any(f'dev {dev} ' in return_route + ' ' for dev in tunnels):
        warnings.append('指定地址的回程正在走全局 VPN；若它应通过原局域网访问，需要修复路由。')
    return {'global_tunnels': tunnels, 'route_warnings': warnings,
            'public_route': public_route, 'return_route': return_route}


def runtime_profiles(profiles, probes, now=None):
    now = int(time.time()) if now is None else now
    output = []
    for profile in profiles:
        nodes = []
        for host_id in [profile['gateway_id'], *profile['client_ids']]:
            probe = probes.get(host_id, {})
            fresh = probe.get('checked_at', 0) >= now - 120
            assist = next((s for s in probe.get('assist', [])
                           if s.get('profile_id') == profile['id']), None)
            status = 'unknown'
            if fresh and probe.get('ssh') and probe.get('helper'):
                if assist is None:
                    status = 'not_configured'
                elif assist.get('suspended'):
                    status = 'suspended'
                elif assist.get('active') and assist.get('desired'):
                    status = 'enabled'
                elif not assist.get('active') and not assist.get('desired'):
                    status = 'disabled'
                else:
                    status = 'inconsistent'
            nodes.append({'host_id': host_id, 'status': status,
                          'checked_at': probe.get('checked_at', 0),
                          'external_tunnels': [dev for dev in probe.get('global_tunnels', [])
                                               if dev != profile.get('interface')],
                          'last_error': (assist or {}).get('last_error', '')})
        statuses = {node['status'] for node in nodes}
        runtime = next(iter(statuses)) if len(statuses) == 1 else 'mixed'
        output.append({**profile, 'runtime': {'status': runtime, 'nodes': nodes,
                       'mismatch': any(node['status'] not in ('unknown', profile['state']) for node in nodes)}})
    return output
