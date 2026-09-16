"""Separate device and relay APIs for the commercial customer service."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .client_crypto import b64url_decode, verify_payload
from .client_store import ClientStore, ClientStoreError


def _wireguard_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError('WireGuard 公钥无效')
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        raise ValueError('WireGuard 公钥无效') from None
    if len(raw) != 32:
        raise ValueError('WireGuard 公钥无效')
    return value


def _signing_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError('设备签名公钥无效')
    try:
        Ed25519PublicKey.from_public_bytes(b64url_decode(value))
    except Exception:
        raise ValueError('设备签名公钥无效') from None
    return value


class ClientServiceAPI:
    def __init__(self, store: ClientStore, relay_token: str | None = None):
        self.store = store
        self.relay_token = relay_token if relay_token is not None else os.environ.get('SNA_RELAY_TOKEN', '')

    @staticmethod
    async def body(request: web.Request) -> tuple[bytes, dict]:
        raw = await request.read()
        if len(raw) > 32768:
            raise ValueError('请求过大')
        try:
            value = json.loads(raw or b'{}')
        except Exception:
            raise ValueError('请求必须是 JSON 对象') from None
        if not isinstance(value, dict):
            raise ValueError('请求必须是 JSON 对象')
        return raw, value

    def authenticate_device(self, request: web.Request, raw: bytes) -> dict:
        device_id = request.headers.get('X-Device-ID', '')
        timestamp_text = request.headers.get('X-Device-Timestamp', '')
        nonce = request.headers.get('X-Device-Nonce', '')
        signature = request.headers.get('X-Device-Signature', '')
        try:
            timestamp = int(timestamp_text)
        except ValueError:
            raise web.HTTPUnauthorized(text='设备请求时间无效') from None
        if abs(int(time.time()) - timestamp) > 120:
            raise web.HTTPUnauthorized(text='设备请求已经过期')
        device = self.store.device(device_id)
        if not device['enabled']:
            raise web.HTTPForbidden(text='设备已停用')
        if not self.store.customer(device['customer_id'])['enabled']:
            raise web.HTTPForbidden(text='客户服务已停用')
        signed = {'method': request.method, 'path': request.path, 'timestamp': timestamp,
                  'nonce': nonce, 'body_sha256': hashlib.sha256(raw).hexdigest()}
        if not verify_payload(device['public_key'], signed, signature):
            raise web.HTTPUnauthorized(text='设备签名无效')
        self.store.consume_nonce(device_id, nonce, now=timestamp)
        return device

    def authenticate_relay(self, request: web.Request) -> str:
        supplied = request.headers.get('Authorization', '').removeprefix('Bearer ')
        relay_id = request.headers.get('X-Relay-ID', '')
        configured = os.environ.get('SNA_RELAY_TOKENS', '')
        tokens = json.loads(configured) if configured else {'*': self.relay_token}
        expected = tokens.get(relay_id) or (tokens.get('*') if relay_id in ('', '*') else None)
        if not relay_id and '*' in tokens:
            relay_id = '*'
        if not expected or not hmac.compare_digest(supplied, expected):
            raise web.HTTPUnauthorized(text='中继凭据无效')
        return relay_id

    def routes(self, device: dict) -> list[dict]:
        now = int(time.time())
        result = []
        for row in self.store.list_grants(device['customer_id']):
            if not row['enabled'] or (row['expires_at'] is not None and row['expires_at'] <= now):
                continue
            result.append({'id': row['id'], 'name': row['alias'], 'available': True,
                           'endpoint': row['endpoint'], 'relay_public_key': row['relay_public_key'],
                           'expires_at': row['expires_at']})
        return result

    async def handle_client(self, request: web.Request) -> web.Response:
        path = request.path
        raw, value = await self.body(request)
        if path == '/client/v1/enroll' and request.method == 'POST':
            device = self.store.enroll_device(
                str(value.get('enrollment_token', '')), _signing_key(value.get('signing_public_key')),
                str(value.get('label', ''))[:120], wireguard_public_key=_wireguard_key(value.get('wireguard_public_key')))
            return web.json_response({'device_id': device['id'], 'customer_id': device['customer_id']})
        device = self.authenticate_device(request, raw)
        if path in ('/client/v1/routes', '/client/v1/subscription') and request.method == 'GET':
            customer = self.store.customer(device['customer_id'])
            usage = self.store.usage(customer['id'])
            response = {'device_id': device['id'],
                        'customer': {'id': customer['id'], 'display_name': customer['display_name']},
                        'routes': self.routes(device), 'usage': usage, 'server_time': int(time.time())}
            return web.json_response(response if path.endswith('subscription') else {'routes': response['routes']})
        if path == '/client/v1/usage' and request.method == 'GET':
            return web.json_response(self.store.usage(device['customer_id']))
        if path == '/client/v1/lease' and request.method == 'POST':
            lease = self.store.issue_lease(device['id'], str(value.get('grant_id', '')))
            return web.json_response({'lease': self.lease_dto(lease)})
        if path == '/client/v1/lease/renew' and request.method == 'POST':
            lease = self.store.renew_lease(device['id'], str(value.get('lease_id', '')))
            return web.json_response({'lease': self.lease_dto(lease)})
        if path == '/client/v1/lease/release' and request.method == 'POST':
            self.store.release_lease(device['id'], str(value.get('lease_id', '')))
            return web.json_response({'ok': True})
        raise web.HTTPNotFound(text='客户接口不存在')

    @staticmethod
    def lease_dto(lease: dict) -> dict:
        value = {key: lease.get(key) for key in (
            'id', 'token', 'grant_id', 'alias', 'endpoint', 'relay_public_key', 'allocated_address',
            'dns', 'allowed_ips', 'mtu', 'issued_at', 'expires_at', 'download_bps', 'upload_bps',
            'quota_bytes', 'used_bytes', 'remaining_bytes')}
        if value['remaining_bytes'] is None and value['quota_bytes'] is not None:
            value['remaining_bytes'] = max(0, value['quota_bytes'] - (value['used_bytes'] or 0))
        return value

    async def handle_relay(self, request: web.Request) -> web.Response:
        relay_id = self.authenticate_relay(request)
        if request.path == '/relay/v1/reconcile' and request.method == 'GET':
            try:
                since = max(0, int(request.query.get('since', '0')))
            except ValueError:
                raise ValueError('中继游标无效') from None
            value = self.store.lease_reconciliation(since=since)
            policies = []
            customer_subnet = os.environ.get('SNA_RELAY_CUSTOMER_SUBNET', '10.203.0.0/16')
            management = [v.strip() for v in os.environ.get('SNA_RELAY_MANAGEMENT_SUBNETS', '').split(',') if v.strip()]
            for lease in value['active']:
                if relay_id != '*' and lease['relay_interface'] != relay_id:
                    continue
                usage = self.store.usage(lease['customer_id'])
                quota = None if usage['remaining_bytes'] is None else (
                    lease['last_rx'] + lease['last_tx'] + usage['remaining_bytes'])
                policies.append({'peer_id': lease['id'], 'public_key': lease['wireguard_public_key'],
                    'address': lease['allocated_address'], 'interface': lease['relay_interface'],
                    'egress_interface': lease['egress_interface'], 'customer_subnet': customer_subnet,
                    'management_subnets': management, 'download_bps': lease['download_bps'] or 100_000_000_000,
                    'upload_bps': lease['upload_bps'] or 100_000_000_000, 'quota_bytes': quota,
                    'expires_at': lease['expires_at'], 'enabled': True})
            return web.json_response({'generated_at': value['generated_at'], 'policies': policies,
                                      'revoked': value['revoked']})
        if request.path == '/relay/v1/usage' and request.method == 'POST':
            _, value = await self.body(request)
            lease_id = str(value.get('lease_id', ''))
            if relay_id != '*' and self.store.lease_relay_id(lease_id) != relay_id:
                raise web.HTTPForbidden(text='租约不属于当前中继')
            usage = self.store.record_usage_by_lease(lease_id,
                str(value.get('report_id', '')), int(value.get('rx_total', -1)), int(value.get('tx_total', -1)))
            return web.json_response({'usage': usage})
        raise web.HTTPNotFound(text='中继接口不存在')
