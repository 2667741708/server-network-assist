"""Local commercial-source readiness and a narrowly privileged start operation."""
from __future__ import annotations

import json
import ipaddress
import platform
import subprocess

from .client_egress import validate_interface


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False)


def physical_defaults() -> dict:
    """Read the main table, never the Clash-captured effective default route."""
    if platform.system() != 'Linux':
        return {}
    try:
        result = _run(['/usr/sbin/ip', '-4', '-j', 'route', 'show', 'table', 'main', 'default'])
        rows = json.loads(result.stdout)
        choices = []
        for row in rows:
            if not row.get('gateway') or not row.get('dev'):
                continue
            interface = validate_interface(row['dev'])
            link = _run(['/usr/sbin/ip', '-j', '-d', 'link', 'show', 'dev', interface])
            values = json.loads(link.stdout)
            if not values or values[0].get('linkinfo', {}).get('info_kind') in ('tun', 'wireguard', 'ipip', 'sit', 'dummy'):
                continue
            choices.append((int(row.get('metric', 0)), interface, row['gateway']))
        choices.sort()
        if not choices or (len(choices) > 1 and choices[0][0] == choices[1][0]):
            return {}
        return dict(egress_interface=choices[0][1], egress_gateway=choices[0][2])
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def source_is_local(source: dict) -> bool:
    try:
        host = str(source['endpoint']).rsplit(':', 1)[0]
        address = ipaddress.ip_address(host)
        result = _run(['/usr/sbin/ip', '-4', '-j', 'address', 'show'])
        locals_ = {info['local'] for row in json.loads(result.stdout) for info in row.get('addr_info', [])}
        return address.version == 4 and str(address) in locals_
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False


def proxy_status(interface='Meta') -> dict:
    interface = validate_interface(interface)
    value = dict(service='mihomo.service', interface=interface, running=False,
                 tun_ready=False, available=False, reason='仅支持本机 Linux 源端的 Mihomo 服务')
    if platform.system() != 'Linux':
        return value
    try:
        value['running'] = _run(['/usr/bin/systemctl', 'is-active', 'mihomo.service']).returncode == 0
        link = _run(['/usr/sbin/ip', '-j', '-d', 'link', 'show', 'dev', interface])
        rows = json.loads(link.stdout or '[]')
        value['tun_ready'] = bool(link.returncode == 0 and rows and
            rows[0].get('linkinfo', {}).get('info_kind') == 'tun' and 'UP' in rows[0].get('flags', []))
        value['available'] = value['running'] and value['tun_ready']
        value['reason'] = ('Mihomo 与 TUN 已运行；网站可达性需另行探测' if value['available'] else
                           '源机 Mihomo 未运行' if not value['running'] else 'Mihomo 已运行，但代理出口 TUN 未就绪')
    except (OSError, ValueError, subprocess.SubprocessError):
        value['reason'] = '无法读取源机代理状态，禁止启用代理出口'
    return value


def require_proxy(interface='Meta') -> None:
    state = proxy_status(interface)
    if not state['available']:
        raise ValueError(state['reason'] + '；请先在商业管理页面启动源机代理')


def start_proxy() -> dict:
    """No arbitrary units, commands, remote hosts, passwords or sudo stdin."""
    if platform.system() != 'Linux':
        raise ValueError('仅支持本机 Linux 源端的 Mihomo 服务')
    try:
        result = subprocess.run(['/usr/bin/sudo', '-n', '/usr/local/sbin/sna-commercial-proxy', 'start'],
                                capture_output=True, text=True, check=False, timeout=25)
    except (OSError, subprocess.SubprocessError):
        raise ValueError('源机代理启动失败，请查看 mihomo.service 日志') from None
    if result.returncode:
        raise ValueError('源机代理启动失败或尚未配置受限启动权限；请检查 mihomo.service 与 TUN')
    state = proxy_status()
    if not state['available']:
        raise ValueError(state['reason'])
    return state


def grant_available(grant: dict) -> bool:
    policy = json.loads(grant.get('egress_policy', '{}'))
    return not policy.get('require_source_proxy') or (source_is_local(grant) and proxy_status(grant['egress_interface'])['available'])
