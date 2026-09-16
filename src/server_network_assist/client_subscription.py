"""Signed customer subscription handling with a deliberately small schema."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import ssl
import time
import urllib.request
import urllib.error
from urllib.parse import parse_qs, urlsplit, urlunsplit
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


MAX_SUBSCRIPTION_BYTES = 1024 * 1024


def _decode(value: str) -> bytes:
    value += '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode(value.encode('ascii'))


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _subscription_url(value: str) -> tuple[str, bytes]:
    parts = urlsplit(value.strip())
    if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password:
        raise ValueError('订阅地址必须是有效的 HTTPS 地址')
    fragment = parse_qs(parts.fragment, strict_parsing=True)
    keys = fragment.get('key', [])
    if len(keys) != 1:
        raise ValueError('订阅地址缺少签名公钥片段 #key=...')
    try:
        key = _decode(keys[0])
        Ed25519PublicKey.from_public_bytes(key)
    except Exception:
        raise ValueError('订阅签名公钥无效') from None
    clean = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))
    return clean, key


def _safe_host_port(endpoint: str) -> tuple[str, int]:
    value = endpoint.strip()
    if value.startswith('['):
        end = value.find(']')
        if end < 0 or end + 2 > len(value):
            raise ValueError('线路接入地址无效')
        host, port = value[1:end], value[end + 2:]
    else:
        host, separator, port = value.rpartition(':')
        if not separator:
            raise ValueError('线路接入地址无效')
    if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError('线路接入地址无效')
    return host, int(port)


def validate_payload(value: object, now: int | None = None, allow_expired=False) -> dict:
    if not isinstance(value, dict):
        raise ValueError('订阅正文必须是 JSON 对象')
    now = int(time.time()) if now is None else now
    if value.get('schema_version') != 1 or not isinstance(value.get('version'), int) or value['version'] < 1:
        raise ValueError('订阅协议版本无效')
    if not isinstance(value.get('device_id'), str) or not 8 <= len(value['device_id']) <= 128:
        raise ValueError('订阅未绑定有效设备')
    if not isinstance(value.get('nonce'), str) or not 16 <= len(value['nonce']) <= 128:
        raise ValueError('订阅随机标识无效')
    issued, expires = value.get('issued_at'), value.get('expires_at')
    if not isinstance(issued, int) or not isinstance(expires, int):
        raise ValueError('订阅缺少签发时间或到期时间')
    if issued > now + 300 or (expires <= now and not allow_expired) or expires - issued > 31 * 86400:
        raise ValueError('订阅尚未生效、已经过期或有效期过长')
    if not isinstance(value.get('subscription_id'), str) or not 8 <= len(value['subscription_id']) <= 128:
        raise ValueError('订阅编号无效')
    customer = value.get('customer')
    if not isinstance(customer, dict) or not isinstance(customer.get('display_name'), str):
        raise ValueError('客户信息无效')
    lines = value.get('lines')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 32:
        raise ValueError('订阅线路数量无效')
    cleaned = []
    ids = set()
    for row in lines:
        if not isinstance(row, dict):
            raise ValueError('线路信息无效')
        line_id, name, tunnel, endpoint = (row.get(k) for k in ('id', 'name', 'tunnel', 'endpoint'))
        if (not isinstance(line_id, str) or not 1 <= len(line_id) <= 80 or line_id in ids
                or not isinstance(name, str) or not 1 <= len(name) <= 80
                or not isinstance(tunnel, str) or not tunnel or len(tunnel) > 128
                or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in tunnel)
                or not isinstance(endpoint, str)):
            raise ValueError('线路标识、名称或本机隧道映射无效')
        _safe_host_port(endpoint)
        ids.add(line_id)
        quota = row.get('quota_bytes')
        used = row.get('used_bytes')
        if quota is not None and (not isinstance(quota, int) or quota < 0):
            raise ValueError('线路流量额度无效')
        if used is not None and (not isinstance(used, int) or used < 0):
            raise ValueError('线路已用流量无效')
        cleaned.append({
            'id': line_id, 'name': name, 'tunnel': tunnel, 'endpoint': endpoint,
            'available': row.get('available') is not False,
            'download_bps': row.get('download_bps') if isinstance(row.get('download_bps'), int) else None,
            'upload_bps': row.get('upload_bps') if isinstance(row.get('upload_bps'), int) else None,
            'quota_bytes': quota, 'used_bytes': used,
            'expires_at': row.get('expires_at') if isinstance(row.get('expires_at'), int) else expires,
        })
    return {**value, 'lines': cleaned}


def local_device_id() -> str:
    """Return a stable opaque binding without exposing the underlying machine id."""
    raw = None
    if __import__('os').name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography') as key:
                raw = str(winreg.QueryValueEx(key, 'MachineGuid')[0])
        except OSError:
            pass
    else:
        for path in (Path('/etc/machine-id'), Path('/var/lib/dbus/machine-id')):
            try:
                raw = path.read_text(encoding='ascii').strip()
                if raw:
                    break
            except OSError:
                pass
    if not raw:
        raw = f'{uuid.getnode():012x}'
    return 'device-' + hashlib.sha256(('sna-client-v1\0' + raw).encode()).hexdigest()[:32]


class SubscriptionStore:
    def __init__(self, data: Path):
        self.path = Path(data) / 'customer-subscription.json'

    def configured(self) -> bool:
        return self.path.is_file()

    def save_url(self, value: str) -> None:
        url, key = _subscription_url(value)
        record = {'url': url, 'public_key': base64.urlsafe_b64encode(key).decode().rstrip('='),
                  'updated_at': int(time.time())}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(record), encoding='utf-8')
        if __import__('os').name != 'nt':
            temporary.chmod(0o600)
        temporary.replace(self.path)

    def _record(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except Exception:
            raise ValueError('尚未配置有效订阅') from None

    def summary(self) -> dict:
        record = self._record()
        host = urlsplit(record['url']).hostname or ''
        return {'configured': True, 'provider': host,
                'key_fingerprint': hashlib.sha256(_decode(record['public_key'])).hexdigest()[:16],
                'device_id': local_device_id()}

    def _verify(self, envelope, record, now=None, allow_expired=False):
        try:
            payload, signature = envelope['payload'], _decode(envelope['signature'])
            Ed25519PublicKey.from_public_bytes(_decode(record['public_key'])).verify(signature, _canonical(payload))
        except Exception:
            raise ValueError('订阅签名验证失败') from None
        payload = validate_payload(payload, now=now, allow_expired=allow_expired)
        if not secrets.compare_digest(payload['device_id'], local_device_id()):
            raise ValueError('订阅未绑定到当前设备')
        return payload

    def update(self, opener=None, now=None) -> dict:
        record = self._record()
        request = urllib.request.Request(record['url'], headers={
            'Accept': 'application/json', 'User-Agent': 'ServerNetworkAssist-Client/1'})
        opener = opener or urllib.request.build_opener()
        try:
            with opener.open(request, timeout=15) as response:
                final = urlsplit(response.url)
                original = urlsplit(record['url'])
                if final.scheme != 'https' or final.hostname != original.hostname:
                    raise ValueError('订阅发生了不受信任的跨域跳转')
                body = response.read(MAX_SUBSCRIPTION_BYTES + 1)
        except ValueError:
            raise
        except (OSError, urllib.error.URLError):
            raise ValueError('订阅下载失败；请检查配置地址和当前网络') from None
        if len(body) > MAX_SUBSCRIPTION_BYTES:
            raise ValueError('订阅响应过大')
        try:
            envelope = json.loads(body)
        except Exception:
            raise ValueError('订阅响应不是有效 JSON') from None
        payload = self._verify(envelope, record, now=now)
        cached = self.path.with_name('customer-subscription-cache.json')
        temporary = cached.with_suffix('.tmp')
        temporary.write_text(json.dumps(envelope, ensure_ascii=False), encoding='utf-8')
        if __import__('os').name != 'nt':
            temporary.chmod(0o600)
        temporary.replace(cached)
        return payload

    def cached(self, now=None, allow_expired=False) -> dict:
        path = self.path.with_name('customer-subscription-cache.json')
        try:
            return self._verify(json.loads(path.read_text(encoding='utf-8')), self._record(), now=now,
                                allow_expired=allow_expired)
        except FileNotFoundError:
            raise ValueError('订阅尚未更新') from None

    def remove(self) -> None:
        for path in (self.path, self.path.with_name('customer-subscription-cache.json')):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def probe_line(line: dict, timeout=3.0) -> dict:
    host, port = _safe_host_port(line['endpoint'])
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return {'id': line['id'], 'reachable': True,
                'latency_ms': max(1, round((time.monotonic() - started) * 1000))}
    except OSError as exc:
        return {'id': line['id'], 'reachable': False, 'latency_ms': None,
                'reason': type(exc).__name__}
