"""Client-owned Mihomo process for public proxy subscriptions."""
from __future__ import annotations

import contextlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import uuid


STATE = 'customer-public-proxy.json'
DIRECTORY = 'public-proxy'
_PROCESSES: dict[int, subprocess.Popen] = {}


def _available_port(start: int) -> int:
    for port in range(start, start + 40):
        with socket.socket() as listener:
            try:
                listener.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    raise RuntimeError('没有可用的本地代理端口')


def core_path() -> Path:
    candidates = []
    configured = os.environ.get('SNA_MIHOMO_PATH')
    if configured:
        candidates.append(Path(configured))
    bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    candidates.extend((bundle / 'server_network_assist/mihomo.exe',
                       Path(sys.executable).resolve().parent / 'mihomo.exe'))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError('澜桥缺少内置 Mihomo 核心，请重新安装完整客户端')


def _proxy(lease: dict) -> dict:
    value = lease.get('proxy')
    if not isinstance(value, dict) or value.get('type') != 'vmess':
        raise ValueError('公网代理租约格式无效')
    try:
        uuid.UUID(str(value.get('uuid', '')))
        port = int(value.get('port'))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('公网代理凭据无效') from None
    server = str(value.get('server', '')).strip()
    servername = str(value.get('servername', '')).strip()
    path = str(value.get('ws_path', '')).strip()
    if not server or not servername or not path.startswith('/') or not 1 <= port <= 65535:
        raise ValueError('公网代理端点无效')
    return value


CAPTURE_MODES = ('system_proxy', 'tun')
PROXY_MODES = ('rule', 'global', 'direct')


def _settings(capture_mode: str, proxy_mode: str) -> tuple[str, str]:
    if capture_mode not in CAPTURE_MODES:
        raise ValueError('流量接管方式无效')
    if proxy_mode not in PROXY_MODES:
        raise ValueError('代理运行模式无效')
    return capture_mode, proxy_mode


def configuration(lease: dict, mixed_port: int, controller_port: int, secret: str,
                  capture_mode: str = 'system_proxy', proxy_mode: str = 'rule') -> dict:
    capture_mode, proxy_mode = _settings(capture_mode, proxy_mode)
    value = _proxy(lease)
    path = value['ws_path'].rstrip('/')
    if path.startswith('/sna-proxy/v2/'):
        path += '/' + ('global' if proxy_mode == 'global' else 'rule')
    node = {
        'name': 'SNA Public Proxy', 'type': 'vmess', 'server': value['server'],
        'port': int(value['port']), 'uuid': value['uuid'],
        'alterId': int(value.get('alter_id', 0)), 'cipher': str(value.get('cipher', 'auto')),
        'network': 'ws', 'ws-opts': {'path': path,
        'headers': {'Host': value['servername']}}, 'tls': bool(value.get('tls', True)),
        'servername': value['servername'], 'skip-cert-verify': False,
    }
    interface = os.environ.get('SNA_PROXY_INTERFACE', '').strip()
    if interface:
        if len(interface) > 15 or not all(character.isalnum() or character in '._-' for character in interface):
            raise ValueError('代理出口接口名称无效')
        node['interface-name'] = interface
    dns_nameservers = ['https://dns.alidns.com/dns-query', 'https://doh.pub/dns-query']
    configured_dns = os.environ.get('SNA_DNS_NAMESERVERS', '').strip()
    if configured_dns:
        dns_nameservers = [str(ipaddress.ip_address(item.strip()))
                           for item in configured_dns.split(',') if item.strip()]
        if not dns_nameservers:
            raise ValueError('代理 DNS 服务器无效')
    final = 'DIRECT' if proxy_mode == 'direct' else 'SNA-PUBLIC'
    document = {
        'mixed-port': mixed_port, 'allow-lan': False, 'mode': 'rule', 'log-level': 'warning',
        'ipv6': False,
        'external-controller': f'127.0.0.1:{controller_port}', 'secret': secret,
        'tun': {
            'enable': capture_mode == 'tun', 'stack': 'mixed', 'auto-route': True,
            'strict-route': True, 'auto-detect-interface': True,
            'route-exclude-address': [
                '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
                '172.16.0.0/12', '192.168.0.0/16',
                f"{value['server']}/32" if _is_ipv4(value['server']) else '140.143.202.144/32',
            ],
            'dns-hijack': ['any:53'],
        },
        'dns': {
            'enable': capture_mode == 'tun', 'ipv6': False, 'listen': '127.0.0.1:0',
            'enhanced-mode': 'fake-ip', 'fake-ip-range': '198.18.0.1/16',
            'fake-ip-filter': ['+.lan', '+.local', '+.ysu.edu.cn', 'auth1.ysu.edu.cn'],
            'default-nameserver': ['223.5.5.5', '119.29.29.29'],
            'nameserver': dns_nameservers,
        },
        'proxies': [node],
        'proxy-groups': [{'name': 'SNA-PUBLIC', 'type': 'select', 'proxies': [node['name']]}],
        'rules': ['IP-CIDR,10.0.0.0/8,DIRECT,no-resolve',
                  'IP-CIDR,100.64.0.0/10,DIRECT,no-resolve',
                  'IP-CIDR,127.0.0.0/8,DIRECT,no-resolve',
                  'IP-CIDR,172.16.0.0/12,DIRECT,no-resolve',
                  'IP-CIDR,192.168.0.0/16,DIRECT,no-resolve',
                  f"IP-CIDR,{value['server']}/32,DIRECT,no-resolve" if _is_ipv4(value['server']) else
                  f"DOMAIN,{value['server']},DIRECT",
                  'DOMAIN-SUFFIX,ysu.edu.cn,DIRECT', f'MATCH,{final}'],
    }
    return document


def _is_ipv4(value: str) -> bool:
    try:
        socket.inet_aton(value)
        return value.count('.') == 3
    except OSError:
        return False


def _write_private(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(value, encoding='utf-8')
    if os.name != 'nt':
        temporary.chmod(0o600)
    temporary.replace(path)


def _healthy(state: dict) -> bool:
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{int(state['controller_port'])}/version",
            headers={'Authorization': 'Bearer ' + state['controller_secret']})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def start(data: Path, lease: dict, *, capture_mode: str = 'system_proxy',
          proxy_mode: str = 'rule') -> dict:
    if lease.get('access_mode') != 'public_proxy':
        raise ValueError('租约不是仅公网代理模式')
    import yaml
    data = Path(data)
    runtime = data / DIRECTORY
    runtime.mkdir(parents=True, exist_ok=True)
    mixed_port = _available_port(17897)
    controller_port = _available_port(max(17950, mixed_port + 1))
    secret = secrets.token_urlsafe(32)
    capture_mode, proxy_mode = _settings(capture_mode, proxy_mode)
    document = configuration(lease, mixed_port, controller_port, secret, capture_mode, proxy_mode)
    config_path = runtime / 'config.yaml'
    _write_private(config_path, yaml.safe_dump(document, allow_unicode=True, sort_keys=False))
    command = [str(core_path()), '-d', str(runtime), '-f', str(config_path)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=flags)
    _PROCESSES[process.pid] = process
    state = {'schema_version': 1, 'pid': process.pid, 'lease_id': lease['id'],
             'mixed_port': mixed_port, 'controller_port': controller_port,
             'controller_secret': secret, 'capture_mode': capture_mode,
             'proxy_mode': proxy_mode, 'started_at': int(time.time())}
    _write_private(data / STATE, json.dumps(state, ensure_ascii=False, separators=(',', ':')))
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        if _healthy(state):
            from .client_original_network import enable_explicit_proxy
            try:
                if capture_mode == 'system_proxy':
                    enable_explicit_proxy(data, mixed_port)
            except Exception:
                stop(data)
                raise
            return {'active': True, 'port': mixed_port, 'pid': process.pid,
                    'capture_mode': capture_mode, 'proxy_mode': proxy_mode}
        time.sleep(.2)
    stop(data)
    raise RuntimeError('澜桥内置代理未能启动')


def status(data: Path) -> dict | None:
    path = Path(data) / STATE
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return {'active': _healthy(state), 'port': state.get('mixed_port'),
            'pid': state.get('pid'), 'lease_id': state.get('lease_id'),
            'capture_mode': state.get('capture_mode', 'system_proxy'),
            'proxy_mode': state.get('proxy_mode', 'rule')}


def ensure(data: Path, lease: dict, *, capture_mode: str = 'system_proxy',
           proxy_mode: str = 'rule', force: bool = False) -> dict:
    capture_mode, proxy_mode = _settings(capture_mode, proxy_mode)
    current = status(data)
    if (not force and current and current['active'] and current['lease_id'] == lease.get('id')
            and current['capture_mode'] == capture_mode and current['proxy_mode'] == proxy_mode):
        return current
    stop(data)
    from .client_original_network import restore
    restore(data)
    return start(data, lease, capture_mode=capture_mode, proxy_mode=proxy_mode)


def stop(data: Path) -> None:
    data = Path(data)
    path = data / STATE
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = None
    if state:
        pid = int(state.get('pid', 0))
        process = _PROCESSES.pop(pid, None)
        if process:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            if process.poll() is None:
                process.kill()
        elif _healthy(state) and os.name == 'nt' and pid > 0:
            subprocess.run(['taskkill.exe', '/PID', str(pid), '/T', '/F'],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        elif _healthy(state) and pid > 0:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, 15)
    path.unlink(missing_ok=True)
    runtime = data / DIRECTORY
    for name in ('config.yaml', 'cache.db', 'cache.db-shm', 'cache.db-wal'):
        (runtime / name).unlink(missing_ok=True)
