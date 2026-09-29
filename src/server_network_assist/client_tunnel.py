"""Install only client-owned WireGuard configurations from a validated lease."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
from os import fsync as _fsync
from os.path import normcase as _normcase
import json
from pathlib import Path
import subprocess
import time
import uuid

from .client_subscription import _safe_host_port

OWNED = 'customer-owned-tunnel.json'
LEDGER_VERSION = 2
LEDGER_STATES = {'preparing', 'connected', 'leaving', 'tunnel_removed', 'rollback_pending'}


def _valid_name(name):
    return (isinstance(name, str) and len(name) == 15 and name.startswith('sna')
            and all(c in '0123456789abcdef' for c in name[3:]))


def _atomic_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8', newline='\n') as output:
        output.write(value)
        output.flush()
        _fsync(output.fileno())
    if os.name != 'nt':
        temporary.chmod(0o600)
    temporary.replace(path)


def _ledger_path(data):
    if data is None:
        raise ValueError('客户隧道操作缺少所有权目录')
    return Path(data) / OWNED


def read_owned(data):
    path = _ledger_path(data)
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError('客户隧道所有权记录无效')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or not _valid_name(value.get('tunnel')):
        raise ValueError('客户隧道所有权记录无效')
    # Version 1 contained only the tunnel name. It remains readable so a
    # verified legacy service can be cleaned up, but every new session is v2.
    if value.get('schema_version') is None and set(value) == {'tunnel'}:
        return {'schema_version': 1, 'tunnel': value['tunnel']}
    if value.get('schema_version') != LEDGER_VERSION or value.get('state') not in LEDGER_STATES:
        raise ValueError('客户隧道所有权记录版本无效')
    if value.get('service') != 'WireGuardTunnel$' + value['tunnel']:
        raise ValueError('客户隧道服务所有权记录无效')
    digest = value.get('config_sha256')
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('客户隧道配置哈希记录无效')
    if not isinstance(value.get('config_path'), str) or not value['config_path']:
        raise ValueError('客户隧道配置路径记录无效')
    return value


def _write_owned(data, value):
    if value.get('schema_version') != LEDGER_VERSION or not _valid_name(value.get('tunnel')):
        raise ValueError('拒绝写入无效客户隧道所有权记录')
    value = {**value, 'updated_at': int(time.time())}
    _atomic_text(_ledger_path(data), json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                  separators=(',', ':')))
    return value


def record_owned(data, name, **fields):
    if 'config_path' not in fields or 'config_sha256' not in fields:
        _atomic_text(_ledger_path(data), json.dumps({'tunnel': name}, separators=(',', ':')))
        return {'schema_version': 1, 'tunnel': name}
    now = int(time.time())
    value = {
        'schema_version': LEDGER_VERSION,
        'tunnel': name,
        'service': 'WireGuardTunnel$' + name,
        'state': 'preparing',
        'desired': True,
        'created_at': now,
        'updated_at': now,
        'heartbeat_monotonic': time.monotonic(),
        'checks': {},
        **fields,
    }
    return _write_owned(data, value)


def update_owned(data, name, **changes):
    value = read_owned(data)
    if value is None or value.get('schema_version') != LEDGER_VERSION or value.get('tunnel') != name:
        raise ValueError('客户隧道所有权记录不匹配')
    value.update(changes)
    return _write_owned(data, value)


def mark_rollback_pending(data, name, reason):
    try:
        update_owned(data, name, state='rollback_pending', desired=False,
                     reason=('恢复尚未确认：' + str(reason))[:500])
    except Exception:
        pass


def finalize_owned(data, name):
    value = read_owned(data)
    if value is None:
        return
    if value.get('tunnel') != name:
        raise ValueError('客户隧道所有权记录不匹配')
    if value.get('schema_version') == LEDGER_VERSION and value.get('state') not in {
            'tunnel_removed', 'leaving', 'rollback_pending'}:
        raise ValueError('客户隧道仍处于活动状态，不能清除所有权记录')
    if value.get('schema_version') == LEDGER_VERSION:
        if Path(value['config_path']).exists():
            raise RuntimeError('客户隧道配置仍然存在，不能确认恢复完成')
        if os.name == 'nt':
            status = subprocess.run(['sc.exe', 'query', value['service']], capture_output=True,
                                    text=True, timeout=15, creationflags=0x08000000)
            if status.returncode == 0:
                raise RuntimeError('客户隧道服务仍然存在，不能确认恢复完成')
            if status.returncode != 1060:
                raise RuntimeError('无法确认客户隧道服务已经移除')
    _ledger_path(data).unlink(missing_ok=True)


def recovery_reason(data, *, stale_seconds=45, monotonic_now=None, wall_now=None):
    """Read-only reason used by the independent process guard."""
    value = read_owned(data)
    if not value or value.get('schema_version') != LEDGER_VERSION:
        return None
    if value.get('state') not in {'preparing', 'connected'} or not value.get('desired'):
        return None
    monotonic_now = time.monotonic() if monotonic_now is None else monotonic_now
    wall_now = time.time() if wall_now is None else wall_now
    heartbeat = value.get('heartbeat_monotonic')
    if not isinstance(heartbeat, (int, float)) or monotonic_now < heartbeat or monotonic_now - heartbeat > stale_seconds:
        return '客户心跳失效'
    expires = value.get('lease_expires_at')
    if isinstance(expires, (int, float)) and wall_now >= expires:
        return '源网租约已到期'
    return None


DIRECT_IPV4 = ('0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
               '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16', '224.0.0.0/3',
               '222.30.148.40/32', '124.124.124.124/32', '202.206.240.0/24')


def split_allowed_ips(value: str, direct_cidrs=()) -> str:
    """Subtract local destinations from leased IPv4 routes; keep IPv6 as leased."""
    routes = [ipaddress.ip_network(item.strip(), strict=True) for item in value.split(',')]
    for cidr in (*DIRECT_IPV4, *direct_cidrs):
        direct = ipaddress.ip_network(cidr, strict=True)
        updated = []
        for route in routes:
            if route.version != direct.version or not route.overlaps(direct):
                updated.append(route)
            elif direct.subnet_of(route):
                updated.extend(route.address_exclude(direct))
            # A leased route contained by a direct network is removed entirely.
        routes = updated
    return ', '.join(str(route) for route in ipaddress.collapse_addresses(
        [r for r in routes if r.version == 4])) + (
        ', ' if any(r.version == 4 for r in routes) and any(r.version == 6 for r in routes) else '') + ', '.join(
        str(r) for r in ipaddress.collapse_addresses([r for r in routes if r.version == 6]))


def configuration(lease: dict, private_key: str, direct_cidrs=()) -> tuple[str, str]:
    grant = str(lease.get('grant_id', ''))
    if not grant:
        raise ValueError('租约缺少节点编号')
    for key in (private_key, str(lease.get('relay_public_key', ''))):
        if len(base64.b64decode(key, validate=True)) != 32:
            raise ValueError('WireGuard 密钥无效')
    endpoint = str(lease.get('endpoint', ''))
    _safe_host_port(endpoint)
    address = str(ipaddress.ip_interface(str(lease.get('allocated_address', ''))))
    dns = ', '.join(str(ipaddress.ip_address(item.strip())) for item in str(lease.get('dns') or '223.5.5.5').split(','))
    allowed = split_allowed_ips(str(lease.get('allowed_ips', '')), direct_cidrs)
    if not allowed:
        raise ValueError('分流后租约没有可借网的目标地址')
    mtu = int(lease.get('mtu') or 1420)
    if not 576 <= mtu <= 9000:
        raise ValueError('租约 MTU 无效')
    # A grant can be installed by different devices/users; their tunnels must
    # never collide and remove one another during an installation rollback.
    name = 'sna' + hashlib.sha256((grant + ':' + private_key).encode()).hexdigest()[:12]
    text = f'[Interface]\nPrivateKey = {private_key}\nAddress = {address}\nDNS = {dns}\nMTU = {mtu}\n\n[Peer]\nPublicKey = {lease["relay_public_key"]}\nEndpoint = {endpoint}\nAllowedIPs = {allowed}\nPersistentKeepalive = 25\n'
    return name, text


def _run(argv: list[str]) -> None:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=45,
                            creationflags=0x08000000 if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError('客户隧道操作失败：' + result.stderr.strip()[:300])


def verify_windows_owner(name, data):
    import winreg
    import ctypes
    from ctypes import wintypes
    if data is None:
        raise ValueError('退出客户隧道必须指定其所有权目录')
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
            'SYSTEM\\CurrentControlSet\\Services\\WireGuardTunnel$' + name) as key:
        command = winreg.QueryValueEx(key, 'ImagePath')[0]
    shell = ctypes.windll.shell32
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(os.path.expandvars(command), ctypes.byref(count))
    if not pointer:
        raise RuntimeError('无法验证客户服务配置路径')
    try:
        argv = [pointer[i] for i in range(count.value)]
    finally:
        kernel = ctypes.windll.kernel32
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(pointer)
    expected_path = (Path(data) / (name + '.conf')).resolve()
    expected = _normcase(str(expected_path))
    if len(argv) != 3 or argv[1].lower() != '/tunnelservice' or _normcase(str(Path(argv[2]).resolve())) != expected:
        raise ValueError('客户服务不属于此数据目录，不会修改其他客户端的网络')
    ledger = read_owned(data)
    if ledger is None or ledger.get('tunnel') != name:
        raise ValueError('客户服务缺少匹配的所有权账本')
    if ledger.get('schema_version') == 1:
        if not expected_path.is_file():
            raise ValueError('旧客户服务配置已经缺失，不能迁移所有权')
        digest = hashlib.sha256(expected_path.read_bytes()).hexdigest()
        ledger = record_owned(
            data, name,
            state='leaving', desired=False,
            config_path=str(expected_path), config_sha256=digest,
            endpoint='', relay_public_key='', route_guard=None,
            lease_expires_at=0, reason='升级旧版所有权记录后执行恢复',
        )
    if ledger.get('service') != 'WireGuardTunnel$' + name:
        raise ValueError('客户服务名称与所有权账本不匹配')
    if _normcase(str(Path(ledger['config_path']).resolve())) != expected:
        raise ValueError('客户配置路径与所有权账本不匹配')
    if not expected_path.is_file() or hashlib.sha256(expected_path.read_bytes()).hexdigest() != ledger['config_sha256']:
        raise ValueError('客户配置内容与所有权账本哈希不匹配')
    return ledger


def wait_handshake(name: str, relay_public_key: str, timeout=30, heartbeat=None) -> None:
    """Do not report a connected subscription when its LAN relay cannot be reached."""
    wg = str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'WireGuard' / 'wg.exe') if os.name == 'nt' else 'wg'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if heartbeat is not None:
            heartbeat()
        result = subprocess.run([wg, 'show', name, 'latest-handshakes'],
                                capture_output=True, text=True, timeout=5,
                                creationflags=0x08000000 if os.name == 'nt' else 0)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0] == relay_public_key and parts[1].isdigit():
                    if int(parts[1]) > 0 and 0 <= time.time() - int(parts[1]) < 180:
                        return
        time.sleep(.5)
    raise ValueError('源网未完成 WireGuard 握手。请检查校园接入、源机 UDP 端口及本机其他 VPN/TUN；不能仅凭订阅可下载就认定可借网')


def _wg_path():
    return (str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'WireGuard' / 'wg.exe')
            if os.name == 'nt' else 'wg')


def _peer_values(name, command, relay_public_key):
    result = subprocess.run([_wg_path(), 'show', name, command], capture_output=True,
                            text=True, timeout=5,
                            creationflags=0x08000000 if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError('无法读取客户 WireGuard 运行状态')
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == relay_public_key:
            return parts[1:]
    raise RuntimeError('客户 WireGuard peer 身份与租约不匹配')


def _recent_handshake(name, relay_public_key):
    values = _peer_values(name, 'latest-handshakes', relay_public_key)
    return (len(values) == 1 and values[0].isdigit() and int(values[0]) > 0
            and 0 <= time.time() - int(values[0]) < 180)


def _transfer(name, relay_public_key):
    values = _peer_values(name, 'transfer', relay_public_key)
    if len(values) != 2 or not all(value.isdigit() for value in values):
        raise RuntimeError('客户 WireGuard 流量计数无效')
    return int(values[0]), int(values[1])


def _heartbeat(data, name, lease_expires_at=None, *, force=False):
    value = read_owned(data)
    if value is None or value.get('schema_version') != LEDGER_VERSION or value.get('tunnel') != name:
        raise ValueError('客户隧道所有权记录不匹配')
    now = time.monotonic()
    previous = value.get('heartbeat_monotonic')
    if not force and isinstance(previous, (int, float)) and 0 <= now - previous < 5:
        return value
    changes = {'heartbeat_monotonic': now}
    if isinstance(lease_expires_at, (int, float)):
        changes['lease_expires_at'] = lease_expires_at
    return update_owned(data, name, **changes)


def verify_windows_connectivity(data, name, lease, route_guard):
    """Require real traffic through the tunnel before reporting connected."""
    relay = str(lease['relay_public_key'])
    expires = lease.get('expires_at')
    tick = lambda: _heartbeat(data, name, expires, force=True)
    wait_handshake(name, relay, heartbeat=tick)
    before_received, before_sent = _transfer(name, relay)
    client_ip = str(ipaddress.ip_interface(str(lease['allocated_address'])).ip)
    sites = {}
    for label, url in (
        ('baidu', 'https://www.baidu.com/'),
        ('google', 'https://www.google.com/generate_204'),
    ):
        tick()
        result = subprocess.run([
            str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'curl.exe'),
            '--disable', '-4', '--interface', client_ip, '--noproxy', '*',
            '--connect-timeout', '4', '--max-time', '8', '-sS', '-o', 'NUL',
            '-w', '%{http_code}', url,
        ], capture_output=True, text=True, timeout=10, creationflags=0x08000000)
        code = result.stdout.strip()
        sites[label] = result.returncode == 0 and code.isdigit() and 200 <= int(code) < 400
        if sites[label]:
            break
    tick()
    after_received, after_sent = _transfer(name, relay)
    from .client_attachment import snapshot
    from .client_campus import CAMPUS_CIDRS
    from .client_route_safety import verify_protected_paths
    protected = verify_protected_paths(route_guard, snapshot(), CAMPUS_CIDRS)
    checks = {
        'handshake': _recent_handshake(name, relay),
        'received_growth': after_received > before_received,
        'sent_growth': after_sent > before_sent,
        'https': any(sites.values()),
        'https_sites': sites,
        'protected_paths': protected,
        'checked_at': int(time.time()),
        'received': after_received,
        'sent': after_sent,
    }
    if not all(checks[key] for key in (
            'handshake', 'received_growth', 'sent_growth', 'https', 'protected_paths')):
        failed = [key for key in ('handshake', 'received_growth', 'sent_growth', 'https', 'protected_paths')
                  if not checks[key]]
        raise ValueError('源网联合连通性检查未通过：' + ', '.join(failed))
    return checks


def health(data, lease, name, *, max_age=20):
    """Refresh the durable heartbeat and periodically re-run joint checks."""
    value = _heartbeat(data, name, lease.get('expires_at'))
    if value.get('state') != 'connected' or not value.get('desired'):
        raise RuntimeError('客户隧道不在可维持的连接状态')
    if (value.get('endpoint') != str(lease.get('endpoint'))
            or value.get('relay_public_key') != str(lease.get('relay_public_key'))):
        raise RuntimeError('续租改变了客户隧道身份，拒绝静默切换')
    verify_windows_owner(name, data)
    checked = value.get('checks', {}).get('checked_at')
    if isinstance(checked, int) and 0 <= time.time() - checked < max_age:
        return value.get('checks', {})
    checks = verify_windows_connectivity(data, name, lease, value.get('route_guard'))
    update_owned(data, name, checks=checks, heartbeat_monotonic=time.monotonic(), reason='')
    return checks


def install(data: Path, lease: dict, private_key: str, network_before=None) -> str:
    policy = Path(data) / 'client-routing.json'
    settings = json.loads(policy.read_text(encoding='utf-8')) if policy.exists() else {}
    direct = settings.get('direct_cidrs', [])
    if 'mtu' in settings:
        lease = {**lease, 'mtu': int(settings['mtu'])}
    if not isinstance(direct, list) or any(not isinstance(item, str) for item in direct):
        raise ValueError('本机直连网段配置无效')
    name, _ = configuration(lease, private_key, direct)
    route_guard = None
    protected = []
    if os.name == 'nt':
        probe = subprocess.run(['powershell.exe','-NoProfile','-Command',
            "Get-NetRoute -AddressFamily IPv4 | Where-Object { $_.DestinationPrefix -in @('0.0.0.0/1','128.0.0.0/1') } | Select-Object -ExpandProperty InterfaceAlias"],
            capture_output=True, text=True, timeout=15, creationflags=0x08000000)
        if probe.returncode:
            raise ValueError('无法检查现有借网路由')
        from . import client_clash_coexist
        for item in probe.stdout.splitlines():
            alias = item.strip()
            if alias and alias != name and not client_clash_coexist.is_clash_route(alias):
                raise ValueError('本机已存在其他完整借网隧道，请先退出旧借网再启动商业节点')
        target = Path(data) / (name + '.conf')
        status = subprocess.run(['sc.exe', 'query', 'WireGuardTunnel$' + name],
                                capture_output=True, text=True, timeout=15, creationflags=0x08000000)
        if status.returncode == 0:
            raise ValueError('同名客户隧道已存在，请先退出原客户端；不会修改现有服务')
        if status.returncode != 1060:
            raise ValueError('无法确认客户隧道服务所有权，保持现有网络')
        if network_before is None:
            raise ValueError('缺少租约签发前的 Windows 路由快照，保持原网络')
        from .client_attachment import snapshot
        from .client_campus import CAMPUS_CIDRS
        from .client_route_safety import build_route_guard
        route_guard = build_route_guard(network_before, snapshot(), str(lease['endpoint']), CAMPUS_CIDRS)
        protected = route_guard['protected_cidrs']
    else:
        if os.geteuid() != 0:
            raise ValueError('请通过已安装的管理员客户端启动借网')
        target = Path('/etc/wireguard') / (name + '.conf')
    name, text = configuration(lease, private_key, (*direct, *protected))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or _ledger_path(data).exists():
        raise ValueError('发现尚未恢复的客户隧道配置或账本，请先完成退出恢复')
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    record_owned(
        data, name,
        config_path=str(target.resolve()), config_sha256=digest,
        endpoint=str(lease['endpoint']), relay_public_key=str(lease['relay_public_key']),
        route_guard=route_guard, lease_expires_at=lease.get('expires_at'), reason='',
    )
    # A successful service start alone does not establish connectivity.
    try:
        _atomic_text(target, text)
        from .client_original_network import capture
        capture(data)
        tick = lambda: _heartbeat(data, name, lease.get('expires_at'), force=True)
        if os.name != 'nt':
            target.chmod(0o600)
            _run(['systemctl', 'start', '--', f'wg-quick@{name}.service'])
            wait_handshake(name, str(lease['relay_public_key']), heartbeat=tick)
            checks = {'handshake': True, 'checked_at': int(time.time())}
        else:
            wireguard = str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'WireGuard' / 'wireguard.exe')
            # Never independently auto-start a commercial tunnel without a lease.
            _run([wireguard, '/installtunnelservice', str(target.resolve())])
            _run(['sc.exe', 'config', 'WireGuardTunnel$' + name, 'start=', 'demand'])
            wait_handshake(name, str(lease['relay_public_key']), heartbeat=tick)
            from .client_original_network import campus_bypass
            campus_bypass(data)
            client_clash_coexist.ensure(data, name, [r.strip() for r in split_allowed_ips(
                str(lease['allowed_ips']), (*direct, *protected)).split(',')], (*direct, *protected))
            checks = verify_windows_connectivity(data, name, lease, route_guard)
        update_owned(data, name, state='connected', desired=True, checks=checks,
                     heartbeat_monotonic=time.monotonic(), reason='')
    except Exception as failure:
        # Service installers may partially activate a tunnel before returning an error.
        # Durable ownership permits an idempotent cleanup even in that failure branch.
        rollback_error = None
        try:
            stop(name, data)
        except Exception as rollback:
            rollback_error = rollback
        from .client_original_network import restore
        try:
            restore(data)
        except Exception as rollback:
            rollback_error = rollback
        if rollback_error:
            mark_rollback_pending(data, name, rollback_error)
            raise RuntimeError('借网未成功，自动回退也未完成，请停止本客户隧道并检查原网络：' + str(rollback_error)[:200]) from failure
        finalize_owned(data, name)
        raise
    return name


def stop(name: str, data: Path | None = None) -> None:
    if not _valid_name(name):
        raise ValueError('不是客户专用隧道')
    ledger = read_owned(data) if data is not None else None
    if data is not None and (ledger is None or ledger.get('tunnel') != name):
        raise ValueError('客户隧道缺少匹配的所有权账本，不会修改其他网络')
    if ledger and ledger.get('schema_version') == LEDGER_VERSION:
        update_owned(data, name, state='leaving', desired=False)
        ledger = read_owned(data)
    try:
        if os.name == 'nt':
            status = subprocess.run(['sc.exe', 'query', 'WireGuardTunnel$' + name],
                                    capture_output=True, text=True, timeout=15, creationflags=0x08000000)
            if status.returncode == 0:
                ledger = verify_windows_owner(name, data)
            elif status.returncode != 1060:
                raise RuntimeError('无法确认客户隧道服务是否已经移除')
            restore_error = None
            if data is not None:
                from . import client_clash_coexist
                try:
                    client_clash_coexist.restore(data, name)
                except Exception as exc:
                    restore_error = exc
            wireguard = str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'WireGuard' / 'wireguard.exe')
            if status.returncode == 0:
                _run([wireguard, '/uninstalltunnelservice', name])
                for _ in range(40):
                    check = subprocess.run(['sc.exe', 'query', 'WireGuardTunnel$' + name],
                                           capture_output=True, text=True, timeout=5,
                                           creationflags=0x08000000)
                    if check.returncode == 1060:
                        break
                    if check.returncode not in (0, 1060):
                        raise RuntimeError('无法确认客户隧道服务移除结果')
                    time.sleep(.1)
                else:
                    raise RuntimeError('客户隧道服务卸载后仍然存在')
            if restore_error:
                raise RuntimeError('借网隧道已退出，但 Clash 配置恢复失败；请重启 Clash 并检查兼容配置') from restore_error
        else:
            marker = Path('/etc/server-network-assist/customer-tun.json')
            if marker.exists():
                state = json.loads(marker.read_text(encoding='utf-8'))
                # Fixed service allowlist, root-owned marker, never accept a command from a lease.
                if (state.get('tunnel') == name and state.get('service') == 'd408-mihomo-tun.service'
                        and marker.stat().st_uid == 0 and not marker.stat().st_mode & 0o022):
                    _run(['systemctl', 'stop', '--', 'd408-mihomo-tun.service'])
            _run(['systemctl', 'stop', '--', f'wg-quick@{name}.service'])
        if data is not None:
            target = (Path(data) if os.name == 'nt' else Path('/etc/wireguard')) / (name + '.conf')
            if ledger and ledger.get('schema_version') == LEDGER_VERSION:
                if _normcase(str(target.resolve())) != _normcase(str(Path(ledger['config_path']).resolve())):
                    raise ValueError('客户配置路径与所有权账本不匹配')
                if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != ledger['config_sha256']:
                    raise ValueError('客户配置内容发生外部修改，不会删除')
            target.unlink(missing_ok=True)
            if ledger and ledger.get('schema_version') == LEDGER_VERSION:
                update_owned(data, name, state='tunnel_removed', desired=False,
                             heartbeat_monotonic=0, reason='')
    except Exception as exc:
        if data is not None:
            mark_rollback_pending(data, name, exc)
        raise


def refresh_coexist(data, lease, name):
    if os.name == 'nt':
        from . import client_clash_coexist
        policy = Path(data) / 'client-routing.json'
        direct = json.loads(policy.read_text(encoding='utf-8')).get('direct_cidrs', []) if policy.exists() else []
        ledger = read_owned(data)
        if ledger is None or ledger.get('schema_version') != LEDGER_VERSION or ledger.get('tunnel') != name:
            raise ValueError('客户隧道所有权账本缺失，拒绝刷新路由')
        guard = ledger.get('route_guard') if isinstance(ledger.get('route_guard'), dict) else {}
        protected = guard.get('protected_cidrs', [])
        return client_clash_coexist.ensure(data, name, [r.strip() for r in split_allowed_ips(
            str(lease['allowed_ips']), (*direct, *protected)).split(',')], (*direct, *protected))
