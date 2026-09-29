"""Read physical attachment and yield to user network changes; never edit adapters."""
import json
import os
from pathlib import Path
import socket
import subprocess
from urllib.parse import urlsplit


def snapshot():
    if os.name != 'nt':
        return {'supported': False, 'links': []}
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', str(Path(__file__).with_name('client_attachment_windows.ps1'))],
        capture_output=True, encoding='utf-8', errors='replace', timeout=12,
        creationflags=0x08000000)
    if result.returncode:
        raise RuntimeError('无法读取物理网络接入，请检查网卡状态')
    value = json.loads(result.stdout.lstrip('\ufeff'))
    if value.get('supported') is not True or not isinstance(value.get('links'), list):
        raise RuntimeError('物理网络接入检测返回无效状态')
    return value


def connected(value):
    return {link['id']: link for link in value.get('links', []) if link.get('connected')}


def preferred(value):
    choices = [(route['metric'], str(link['id']), link, route) for link in connected(value).values()
               for route in link.get('defaults', []) + link.get('defaults6', [])]
    return min(choices, key=lambda row: row[:2])[2:] if choices else (None, None)


def change_reason(before, after):
    if not before or not after.get('supported'):
        return None
    old, new = connected(before), connected(after)
    if old and not new:
        return 'attachment_lost'
    if old.keys() - new.keys():
        return 'attachment_lost'
    if new.keys() - old.keys():
        return 'network_changed'
    for key in new:
        if sorted(new[key].get('addresses', [])) != sorted(old[key].get('addresses', [])) or new[key].get('profile') != old[key].get('profile'):
            return 'network_changed'
        if sorted(new[key].get('addresses6', [])) != sorted(old[key].get('addresses6', [])):
            return 'network_changed'
        for family in ('defaults', 'defaults6'):
            def routes(link):
                return sorted((route.get('gateway'), route.get('metric'))
                              for route in link.get(family, []))
            if routes(new[key]) != routes(old[key]):
                return 'network_changed'
    a, ar = preferred(before)
    b, br = preferred(after)
    if (a and (a['id'], ar['gateway'])) != (b and (b['id'], br['gateway'])):
        return 'network_changed'
    return None


def validate_selected_access(value, base_url):
    """The campus API must be reachable using the user's physical default address.

    A second Ethernet path must not let borrowing steal a selected hotspot.
    This is endpoint reachability, not a guess based on Wi-Fi spelling.
    """
    if not value.get('supported'):
        return
    link, route = preferred(value)
    if not link:
        raise ValueError('没有可用的原网络出口，请先连接校园 Wi-Fi 或有线网络')
    parts = urlsplit(base_url)
    if not parts.hostname:
        raise ValueError('校园服务地址无效')
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            if os.name == 'nt':
                connection.setsockopt(socket.IPPROTO_IP, 31, socket.htonl(int(link['id'])))
            connection.bind((link['addresses'][0], 0))
            connection.connect((parts.hostname, port))
    except OSError as exc:
        raise ValueError('系统当前选用的网络无法直接连接校园订阅服务；保持你的热点或原网络出口，不启动借网') from exc
