"""Classify the active physical network without changing adapters or routes."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from .client_attachment import connected, preferred
from .client_campus import CAMPUS_CIDRS


_CAMPUS_NETWORKS = tuple(ipaddress.ip_network(value) for value in CAMPUS_CIDRS)


def _campus_address(link: dict | None) -> bool:
    if not link:
        return False
    for value in link.get('addresses', []):
        try:
            if any(ipaddress.ip_address(value) in network for network in _CAMPUS_NETWORKS):
                return True
        except ValueError:
            continue
    return False


def endpoint_is_private(value: str) -> bool:
    """Return whether a URL/host:port points at a literal private address."""
    text = str(value or '').strip()
    host = urlsplit(text).hostname if '://' in text else text.rsplit(':', 1)[0]
    try:
        address = ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        return False
    return address.is_private or address.is_link_local or address.is_loopback


def classify(attachment: dict, *, authentication: str = 'unknown',
             service_reachable: bool | None = None) -> dict:
    """Describe where the client is attached and which service family is safe.

    This deliberately combines address ownership, physical medium, competing
    defaults and an endpoint probe. A 10/8 address by itself is not proof of a
    campus attachment because home routers commonly use the same range.
    """
    link, route = preferred(attachment)
    active = connected(attachment)
    if not link or not route:
        return {
            'kind': 'offline', 'label': '当前离线', 'interface': None,
            'campus': False, 'wifi': False, 'authenticated': False,
            'internet_proxy': False, 'source_wireguard': False,
            'reason': '没有检测到带默认路由的物理网络。',
        }

    campus = _campus_address(link)
    wifi = bool(link.get('wifi'))
    competing = []
    for candidate in active.values():
        if candidate.get('id') == link.get('id'):
            continue
        if candidate.get('defaults') or candidate.get('defaults6'):
            competing.append(candidate)
    ipv6_default = bool(link.get('defaults6'))
    mixed = bool(competing) or (campus and ipv6_default)
    interface = str(link.get('profile') or link.get('name') or link.get('id'))

    if not campus:
        return {
            'kind': 'public_internet', 'label': '普通公网网络', 'interface': interface,
            'campus': False, 'wifi': wifi, 'authenticated': False,
            'internet_proxy': True, 'source_wireguard': False,
            'reason': '可使用公网代理；校园源网仅在校园私网可达时开放。',
        }

    if mixed:
        return {
            'kind': 'mixed_network', 'label': '多出口网络', 'interface': interface,
            'campus': True, 'wifi': wifi, 'authenticated': authentication == 'online',
            'internet_proxy': True, 'source_wireguard': False,
            'reason': '检测到并行热点或未配置的 IPv6 默认出口；为保护原网络，暂停校园源网。',
        }

    reachable = service_reachable is not False
    if wifi:
        ready = authentication == 'online' and reachable
        return {
            'kind': 'campus_wifi_authenticated' if ready else 'campus_wifi_auth_required',
            'label': '校园 Wi-Fi 已认证' if ready else '校园 Wi-Fi 待认证',
            'interface': interface, 'campus': True, 'wifi': True,
            'authenticated': authentication == 'online', 'internet_proxy': True,
            'source_wireguard': ready,
            'reason': ('校园私网与源网服务均可达。' if ready else
                       '校园 Wi-Fi 需先完成个人运营商认证，并确认源网服务可达。'),
        }

    ready = reachable
    return {
        'kind': 'campus_wired', 'label': '校园有线网络', 'interface': interface,
        'campus': True, 'wifi': False, 'authenticated': authentication == 'online',
        'internet_proxy': True, 'source_wireguard': ready,
        'reason': ('校园有线私网可达，可在未认证公网时使用源网。' if ready else
                   '校园有线已连接，但当前无法到达源网服务。'),
    }


def route_availability(route: dict, network: dict) -> tuple[bool, str]:
    if route.get('access_mode') == 'public_proxy':
        return bool(network.get('internet_proxy')), (
            '' if network.get('internet_proxy') else '当前没有可用公网连接')
    if route.get('access_mode', 'wireguard') == 'wireguard':
        return bool(network.get('source_wireguard')), (
            '' if network.get('source_wireguard') else str(network.get('reason') or '校园源网当前不可用'))
    return False, '客户端不支持此线路类型'
