"""Separate device and relay APIs for the commercial customer service."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .client_crypto import b64url_decode, verify_payload
from .client_relay import (
    DEFAULT_MAX_ACTIVE_PEERS, DEFAULT_MAX_OFFLINE_SECONDS, relay_policy_fingerprint,
)
from .client_store import (ClientAuthenticationError, ClientStore, ClientStoreError,
                           ProbeCapacityBusy, ProbeVersionUnsupported)
from .control_plane import ControlPlaneError, ControlPlaneService


def _probe_error(status: int, code: str, phase: str, message: str,
                 *, retryable: bool = False, next_action: str = '检查订阅和服务端配置后重试',
                 operation_id: str | None = None,
                 operation_status: str = 'rejected') -> web.Response:
    payload = {'protocol_version': 1, 'error_code': code,
        'component': 'source_service', 'phase': phase, 'error': message,
        'retryable': retryable, 'next_action': next_action,
        'correlation_id': secrets.token_hex(12),
        'operation_status': operation_status}
    if operation_id is not None:
        payload['operation_id'] = operation_id
    return web.json_response(payload, status=status)


def _probe_operation(store: ClientStore, device_id: str, probe_id: object) -> tuple[str | None, str]:
    """Expose a safe operation handle and its durable state, never the secret probe ID."""
    if not isinstance(probe_id, str) or not re.fullmatch(r'[0-9a-f]{64}', probe_id):
        return None, 'rejected'
    operation_id = hashlib.sha256(('lanbridge-probe-operation-v1:' + probe_id).encode()).hexdigest()[:24]
    try:
        state = store.probe_lease(device_id, probe_id)['status']
    except Exception:
        state = 'unknown'
    return operation_id, 'rejected' if state == 'absent' else state


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


from .egress_contract import canonical_egress


def _canonical_egress_mode(value: object) -> str:
    try:
        return canonical_egress(value)
    except ValueError:
        raise ValueError('公网出口类型不兼容，请联系管理员核对源服务器与中继版本') from None


class ClientServiceAPI:
    DEVICE_AUTH_FAILURE = '设备凭据无效'

    def __init__(self, store: ClientStore, relay_token: str | None = None,
                 control_plane: ControlPlaneService | None = None, access_log=None):
        self.store = store
        self.relay_token = relay_token if relay_token is not None else os.environ.get('SNA_RELAY_TOKEN', '')
        # Keep old callers working while allowing the application to provide a
        # persistent directory signing key and URL explicitly.
        self.control_plane = control_plane or ControlPlaneService(store)
        # Best-effort admin visibility into client API activity; never fails a request.
        self.access_log = access_log

    def _record_access(self, request: web.Request, device_id: str, customer_id: str) -> None:
        if self.access_log is None:
            return
        self.access_log.record(device_id, customer_id, request.remote or '',
                               request.method, request.path,
                               request.headers.get('User-Agent', ''))

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
            self._audit_device_auth_failure(device_id, 'timestamp_invalid')
            raise web.HTTPUnauthorized(text=self.DEVICE_AUTH_FAILURE) from None
        if abs(int(time.time()) - timestamp) > 120:
            self._audit_device_auth_failure(device_id, 'timestamp_expired')
            raise web.HTTPUnauthorized(text=self.DEVICE_AUTH_FAILURE)
        signed = {'method': request.method, 'path': request.path, 'timestamp': timestamp,
                  'nonce': nonce, 'body_sha256': hashlib.sha256(raw).hexdigest()}
        try:
            return self.store.authenticate_device(
                device_id, signed, signature,
                lambda row, payload, supplied: verify_payload(row['public_key'], payload, supplied),
                now=timestamp,
            )
        except ClientAuthenticationError as exc:
            self._audit_device_auth_failure(device_id, exc.reason)
            raise web.HTTPUnauthorized(text=self.DEVICE_AUTH_FAILURE) from None

    def _audit_device_auth_failure(self, device_id: str, reason: str) -> None:
        """Keep rejection detail internal without making audit availability part of auth."""
        try:
            self.control_plane.audit_event(
                'device_auth_failed', 'device', device_id,
                {'reason': str(reason)}, now=int(time.time()))
        except Exception:
            # Authentication must remain a safe generic 401 even if logging is
            # temporarily unavailable.
            pass

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

    @staticmethod
    def relay_capabilities(relay_id: str) -> dict:
        """Read an operator-declared capability contract for one relay.

        Request headers are deliberately not accepted as capability claims.
        The map is server configuration, keyed by the authenticated relay id;
        an absent or unknown capability is treated as unsupported.
        """
        raw = os.environ.get('SNA_RELAY_CAPABILITIES', '')
        if not raw:
            return {}
        try:
            mapping = json.loads(raw)
        except Exception:
            raise ValueError('中继能力配置无效') from None
        if not isinstance(mapping, dict):
            raise ValueError('中继能力配置无效')
        value = mapping.get(relay_id, mapping.get('*', {}))
        if not isinstance(value, dict):
            raise ValueError('中继能力配置无效')
        return value

    @staticmethod
    def relay_max_active_peers(capabilities: dict) -> int:
        value = capabilities.get('max_active_peers', DEFAULT_MAX_ACTIVE_PEERS)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 4096:
            raise ValueError('中继活跃 peer 容量配置无效')
        return value

    @staticmethod
    def relay_max_offline_seconds(capabilities: dict) -> int:
        value = capabilities.get('max_offline_seconds', DEFAULT_MAX_OFFLINE_SECONDS)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 86400:
            raise ValueError('中继最大失联授权时间配置无效')
        return value

    @staticmethod
    def require_wireguard_relay_contract(capabilities: dict) -> None:
        """Reject a lease unless the operator declared the execution contract."""
        requirements = {
            'peer_enforcement': {'enforced', True},
            'expiry_enforcement': {'kernel_timeout_watchdog', 'enforced', True},
            'isolation_enforcement': {'nft_scoped', 'verified', True},
        }
        missing = [name for name, accepted in requirements.items()
                   if capabilities.get(name) not in accepted]
        if missing:
            raise ClientStoreError('中继未声明已验证的 WireGuard 执行能力：' + ','.join(missing))

    def require_grant_relay_contract(self, grant_id: str, capabilities: dict) -> None:
        grant = self.store.line_grant(grant_id)
        if grant.get('access_mode', 'wireguard') == 'wireguard':
            self.require_wireguard_relay_contract(capabilities)

    def routes(self, device: dict) -> list[dict]:
        now = int(time.time())
        customer = self.store.customer(device['customer_id'])
        plan = self.store.get_plan(customer['plan_id'])
        result = []
        for row in self.store.list_grants(device['customer_id']):
            if not row['enabled'] or (row['expires_at'] is not None and row['expires_at'] <= now):
                continue
            route = {'id': row['id'], 'name': row['alias'], 'available': True,
                     'access_mode': row.get('access_mode', 'wireguard'),
                     'endpoint': row['endpoint'], 'relay_public_key': row['relay_public_key'],
                     'download_bps': plan['download_bps'], 'upload_bps': plan['upload_bps'],
                     'quota_bytes': plan['quota_bytes'],
                     'expires_at': row['expires_at']}
            if row.get('egress_policy') is not None:
                route['egress_mode'] = _canonical_egress_mode(row['egress_policy'])
                route['egress_policy'] = route['egress_mode']
            result.append(route)
        return result

    def _directory_url(self, request: web.Request) -> str:
        configured = (self.control_plane.directory_url or os.environ.get('SNA_DIRECTORY_URL', '')).strip()
        if configured:
            parsed = urlsplit(configured)
            if (parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or
                    parsed.fragment or parsed.query):
                raise ValueError('节点目录地址必须使用不含凭据和查询的 HTTPS 地址')
            return configured.rstrip('/')
        if request.scheme != 'https':
            raise ValueError('节点目录地址必须使用 HTTPS；请配置 SNA_DIRECTORY_URL')
        # ``request.url`` is intentionally not used here: aiohttp rejects a
        # Host header containing a colon in some test and reverse-proxy setups.
        return f"{request.scheme}://{request.host}/client/v1/node-directory"

    def subscription(self, request: web.Request, device: dict) -> dict:
        """Return a logical subscription when every active grant has a ref.

        Existing customers remain on the old direct ``routes`` shape until an
        administrator records a logical grant reference.  The new shape never
        carries the current endpoint, so rotating an address only republishes
        the signed directory.
        """
        if self.control_plane.has_logical_references(device['customer_id']):
            return self.control_plane.subscription_payload(device, directory_url=self._directory_url(request))
        customer = self.store.customer(device['customer_id'])
        usage = self.store.usage(customer['id'])
        return {'device_id': device['id'],
                'customer': {'id': customer['id'], 'display_name': customer['display_name']},
                'routes': self.routes(device), 'usage': usage, 'server_time': int(time.time())}

    def _lease_endpoint(self, device: dict, grant_id: str, lease: dict) -> dict:
        if not self.control_plane.has_logical_references(device['customer_id']):
            return lease
        resolved = self._resolve_authorized_choice(grant_id, lease.get('transport_id'))
        lease = dict(lease)
        lease['endpoint'] = resolved['endpoint']
        lease['node_id'] = resolved['node_id']
        lease['transport_id'] = resolved['transport_id']
        lease['egress_id'] = resolved['egress_id']
        mode = _canonical_egress_mode(resolved['egress_kind'])
        lease['egress_mode'] = mode
        lease['egress_policy'] = mode
        lease['directory_revision'] = self.control_plane.current_revision()
        return lease

    def _resolve_authorized_choice(self, grant_id: str, transport_id: str | None) -> dict:
        resolved = self.control_plane.resolve_grant_endpoint(
            grant_id, transport_id=transport_id)
        grant = self.store.line_grant(grant_id)
        if _canonical_egress_mode(grant['egress_policy']) != _canonical_egress_mode(resolved['egress_kind']):
            raise ClientStoreError('所选出口与线路授权策略不一致')
        expected_transport = 'wireguard' if grant['access_mode'] == 'wireguard' else 'wss'
        if resolved['transport_kind'] != expected_transport:
            raise ClientStoreError('所选入口与线路接入协议不一致')
        return resolved

    @staticmethod
    def _tunnel_probe_address(relay_id: str | None = None) -> str:
        raw = os.environ.get('SNA_TUNNEL_PROBE_ADDRESSES', '')
        try:
            mapping = json.loads(raw) if raw else {}
            if not isinstance(mapping, dict):
                raise ValueError()
            configured = mapping.get(relay_id, mapping.get('*',
                os.environ.get('SNA_TUNNEL_PROBE_ADDRESS', '')))
            if not isinstance(configured, str):
                raise ValueError()
        except ValueError:
            raise web.HTTPServiceUnavailable(text='源机检测地址配置无效') from None
        try:
            address = ipaddress.ip_address(configured)
            if address.version != 4 or not address.is_private:
                raise ValueError()
        except ValueError:
            raise web.HTTPServiceUnavailable(text='源机尚未启用隧道内检测地址') from None
        return configured

    @staticmethod
    def _tunnel_probe_port() -> int:
        try:
            port = int(os.environ.get('SNA_TUNNEL_PROBE_PORT', '9183'))
            if not 1 <= port <= 65535:
                raise ValueError()
        except ValueError:
            raise web.HTTPServiceUnavailable(text='源机检测端口配置无效') from None
        return port

    @staticmethod
    def _probe_ipv4_allowed_ips(value: object) -> str:
        """Return only IPv4 destinations already authorized by the grant.

        Probe v2 has one IPv4 peer address and does not verify IPv6 egress.
        This is a narrowing of the stored grant, never a new permission.
        """
        if not isinstance(value, str):
            raise ClientStoreError('检测租约缺少有效的 IPv4 出网授权')
        parts = value.split(',')
        if not 1 <= len(parts) <= 32:
            raise ClientStoreError('检测租约缺少有效的 IPv4 出网授权')
        try:
            routes = [ipaddress.ip_network(item.strip(), strict=True)
                      for item in parts]
            ipv4 = list(ipaddress.collapse_addresses(
                route for route in routes if route.version == 4))
        except ValueError:
            raise ClientStoreError('检测租约缺少有效的 IPv4 出网授权') from None
        if ipv4 != [ipaddress.ip_network('0.0.0.0/0')]:
            raise ClientStoreError('检测租约缺少有效的 IPv4 出网授权')
        return ','.join(str(route) for route in ipv4)

    def _probe_payload(self, device: dict, probe_id: str) -> dict:
        value = self.store.probe_lease(device['id'], probe_id)
        if value['status'] != 'active':
            return value
        identity = {key: value['lease'].get(key) for key in
                    ('id', 'grant_id', 'transport_id', 'expires_at', 'probe_version')}
        if (value['lease'].get('transport_id') is not None and
                not self.control_plane.has_logical_references(device['customer_id'])):
            # A previously issued logical probe must never silently become a
            # legacy grant if its signed directory binding is withdrawn.
            return {'status': 'unauthorized', 'lease': identity}
        try:
            lease = self._lease_endpoint(device, value['lease']['grant_id'], value['lease'])
        except (ControlPlaneError, ClientStoreError):
            # A revoked or altered grant reference is no longer a usable
            # probe, even if other logical references still exist.
            return {'status': 'unauthorized', 'lease': identity}
        if lease['access_mode'] != 'wireguard':
            raise web.HTTPConflict(text='此授权线路不是 WireGuard 接入')
        dto = self.lease_dto(lease)
        selected = {key: dto.get(key) for key in (
            'id', 'grant_id', 'node_id', 'transport_id', 'egress_id',
            'directory_revision', 'endpoint', 'access_mode', 'relay_public_key',
            'allocated_address', 'dns', 'allowed_ips', 'mtu', 'relay_interface',
            'egress_interface', 'egress_mode', 'egress_policy', 'egress_capability',
            'issued_at', 'expires_at') if key in dto}
        selected['probe_version'] = lease.get('probe_version')
        selected['wireguard_public_key'] = lease.get('wireguard_public_key')
        if lease.get('probe_version') == 2:
            selected['allowed_ips'] = self._probe_ipv4_allowed_ips(dto['allowed_ips'])
            selected['probe_ip_families'] = ['ipv4']
            selected['ipv6_verified'] = False
            selected['probe_echo_addresses'] = lease.get('probe_echo_addresses')
        selected['probe_address'] = (lease.get('probe_address')
            if lease.get('probe_version') == 2 else
            self._tunnel_probe_address(lease['relay_interface']))
        selected['probe_port'] = self._tunnel_probe_port()
        if lease.get('probe_version') == 2:
            selected['relay_readiness'] = self.store.relay_readiness(lease['id'])
        return {'status': 'active', 'lease': selected}

    def _tunnel_challenge(self, request: web.Request, value: dict) -> web.Response:
        probe_id = request.headers.get('X-Probe-ID', '')
        device_id = request.headers.get('X-Device-ID', '')
        nonce = value.get('nonce')
        if not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{32}', nonce):
            return _probe_error(400, 'PROBE_CHALLENGE_INVALID', 'challenge', '检测挑战无效')
        try:
            lease = self.store.probe_lease(device_id, probe_id)
        except ClientStoreError:
            return _probe_error(401, 'PROBE_LEASE_INVALID', 'challenge', '检测租约无效')
        if lease['status'] != 'active':
            return _probe_error(401, 'PROBE_LEASE_INACTIVE', 'challenge', '检测租约已失效')
        try:
            expected = (lease['lease'].get('probe_address')
                if lease['lease'].get('probe_version') == 2 else
                self._tunnel_probe_address(lease['lease']['relay_interface']))
        except web.HTTPServiceUnavailable:
            return _probe_error(503, 'PROBE_ADDRESS_UNAVAILABLE', 'challenge',
                '源机尚未启用隧道内检测地址', retryable=True,
                next_action='联系管理员配置源机隧道检测地址')
        local = request.transport.get_extra_info('sockname') if request.transport else None
        allocated = ipaddress.ip_interface(lease['lease']['allocated_address']).ip
        if not local or local[0] != expected or request.remote != str(allocated):
            return _probe_error(403, 'PROBE_TUNNEL_REQUIRED', 'challenge',
                                '检测请求未经过授权隧道')
        lease_id = lease['lease']['id']
        proof = hmac.new(bytes.fromhex(probe_id),
                         f'lanbridge-tunnel-probe-v1|{nonce}|{lease_id}'.encode(),
                         hashlib.sha256).hexdigest()
        return web.json_response({'ok': True, 'lease_id': lease_id,
                                  'nonce': nonce, 'proof': proof,
                                  'server_time': int(time.time())})

    async def handle_tunnel_probe(self, request: web.Request) -> web.Response:
        if request.path != '/client/v1/tunnel-probe' or request.method != 'POST':
            raise web.HTTPNotFound()
        try:
            _, value = await self.body(request)
        except ValueError:
            return _probe_error(400, 'PROBE_BODY_INVALID', 'challenge', '检测请求格式无效')
        return self._tunnel_challenge(request, value)

    async def handle_client(self, request: web.Request) -> web.Response:
        path = request.path
        raw, value = await self.body(request)
        if path == '/client/v1/tunnel-probe' and request.method == 'POST':
            return self._tunnel_challenge(request, value)
        # The signed directory is public metadata.  It must be fetchable by a
        # client before it has a usable endpoint and therefore does not use
        # device request authentication.
        if path in ('/client/v1/node-directory', '/client/v1/directory', '/client/v1/nodes') and request.method == 'GET':
            return web.json_response(self.control_plane.publisher.ensure_published())
        if path == '/client/v1/subscription-preview' and request.method == 'POST':
            token = value.get('enrollment_token')
            if not isinstance(token, str) or not 16 <= len(token) <= 512:
                raise web.HTTPUnauthorized(text='开户地址无效或已过期')
            try:
                customer_id = self.store.enrollment_customer_id(token)
            except ClientStoreError:
                raise web.HTTPUnauthorized(text='开户地址无效或已过期') from None
            if self.control_plane.has_logical_references(customer_id):
                payload = self.control_plane.subscription_payload(
                    {'id': 'preview', 'customer_id': customer_id},
                    directory_url=self._directory_url(request))
                self._record_access(request, '', customer_id)
                return web.json_response({key: payload[key] for key in ('node_directory', 'node_refs')})
            self._record_access(request, '', customer_id)
            return web.json_response({'routes': [
                {key: row[key] for key in ('id', 'name', 'endpoint', 'access_mode') if key in row}
                for row in self.routes({'customer_id': customer_id})]})
        if path == '/client/v1/enroll' and request.method == 'POST':
            direct_private = False
            if os.environ.get('SNA_ALLOW_PRIVATE_ENROLLMENT') == '1' and request.scheme == 'http':
                try:
                    host = ipaddress.ip_address(urlsplit('http://' + request.host).hostname)
                    peer = ipaddress.ip_address(request.remote or '')
                    networks = [ipaddress.ip_network(value) for value in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8')]
                    direct_private = all(any(address in network for network in networks) for address in (host, peer))
                except (ValueError, TypeError):
                    pass
            if direct_private:
                customer_id = self.store.enrollment_customer_id(str(value.get('enrollment_token', '')))
                direct_private = not self.control_plane.has_logical_references(customer_id)
            # Direct private grants do not require a public discovery service.
            # Logical-node subscriptions keep their HTTPS signed-directory contract.
            directory_url = None if direct_private else self._directory_url(request)
            device = self.control_plane.enroll_device(
                str(value.get('enrollment_token', '')), _signing_key(value.get('signing_public_key')),
                value.get('label', ''), wireguard_public_key=_wireguard_key(value.get('wireguard_public_key')))
            result = {'device_id': device['id'], 'customer_id': device['customer_id']}
            self._record_access(request, device['id'], device['customer_id'])
            term = self.store.customer_service_term(device['customer_id'])
            if term is not None:
                result.update(service_started_at=term['started_at'],
                              service_expires_at=term['expires_at'])
            if directory_url is not None:
                result.update(directory_url=directory_url,
                              directory_public_key=self.control_plane.publisher.public_key)
            return web.json_response(result)
        device = self.authenticate_device(request, raw)
        self._record_access(request, device['id'], device['customer_id'])
        if path == '/client/v1/probe-lease/status' and request.method == 'GET':
            try:
                return web.json_response(self._probe_payload(device, request.headers.get('X-Probe-ID', '')))
            except ClientStoreError as exc:
                return _probe_error(400, 'PROBE_STATUS_INVALID', 'status', str(exc))
            except web.HTTPServiceUnavailable:
                return _probe_error(503, 'PROBE_ADDRESS_UNAVAILABLE', 'status',
                    '源机尚未启用隧道内检测地址', retryable=True,
                    next_action='联系管理员配置源机隧道检测地址')
        if path == '/client/v1/probe-lease' and request.method == 'POST':
            probe_id = value.get('probe_id')
            grant_id = str(value.get('grant_id', ''))
            requested_transport = value.get('transport_id')
            try:
                logical = self.control_plane.has_logical_references(device['customer_id'])
                if requested_transport is not None and not logical:
                    raise ClientStoreError('当前订阅不支持独立选择接入入口')
                selected_transport = (self._resolve_authorized_choice(grant_id, requested_transport)['transport_id']
                                      if logical else None)
                grant = self.store.line_grant(grant_id)
                if grant['access_mode'] != 'wireguard':
                    raise ClientStoreError('此授权线路不是 WireGuard 接入')
                if value.get('probe_version', 1) == 2:
                    self._probe_ipv4_allowed_ips(grant['allowed_ips'])
                relay_id = self.store.grant_relay_id(grant_id)
                if value.get('probe_version', 1) == 1:
                    self._tunnel_probe_address(relay_id)
                self._tunnel_probe_port()
                capabilities = self.relay_capabilities(relay_id)
                self.require_grant_relay_contract(grant_id, capabilities)
                self.store.issue_probe_lease(device['id'], grant_id, probe_id,
                                             relay_capabilities=capabilities,
                                             transport_id=selected_transport,
                                             probe_version=value.get('probe_version', 1),
                                             wireguard_public_key=value.get('wireguard_public_key'))
                return web.json_response(self._probe_payload(device, probe_id))
            except ProbeVersionUnsupported as exc:
                operation_id, state = _probe_operation(self.store, device['id'], probe_id)
                if state != 'rejected':
                    return _probe_error(409, 'PROBE_LEASE_RESULT_UNAVAILABLE', 'lease_issue',
                        '检测租约状态已记录，但响应未能确认；请查询原检测编号的状态',
                        next_action='用原检测编号查询状态，确认后释放；不要重新申请',
                        operation_id=operation_id, operation_status=state)
                return _probe_error(400, 'PROBE_VERSION_UNSUPPORTED', 'lease_issue', str(exc),
                    next_action='用原检测编号确认状态不存在后，可使用旧版串行检测',
                    operation_id=operation_id)
            except ProbeCapacityBusy as exc:
                operation_id, state = _probe_operation(self.store, device['id'], probe_id)
                if state != 'rejected':
                    return _probe_error(409, 'PROBE_LEASE_RESULT_UNAVAILABLE', 'lease_issue',
                        '检测租约状态已记录，但响应未能确认；请查询原检测编号的状态',
                        next_action='用原检测编号查询状态，确认后释放；不要重新申请',
                        operation_id=operation_id, operation_status=state)
                return _probe_error(429, 'PROBE_CAPACITY_BUSY', 'lease_issue', str(exc),
                    retryable=True, next_action='稍后用新的检测编号重试，不要降级到旧版检测',
                    operation_id=operation_id)
            except (ClientStoreError, ControlPlaneError) as exc:
                operation_id, state = _probe_operation(self.store, device['id'], probe_id)
                if state != 'rejected':
                    return _probe_error(409, 'PROBE_LEASE_RESULT_UNAVAILABLE', 'lease_issue',
                        '检测租约状态已记录，但响应未能确认；请查询原检测编号的状态',
                        next_action='用原检测编号查询状态，确认后释放；不要重新申请',
                        operation_id=operation_id, operation_status=state)
                return _probe_error(409, 'PROBE_LEASE_DENIED', 'lease_issue', str(exc),
                    operation_id=operation_id)
            except web.HTTPServiceUnavailable:
                operation_id, state = _probe_operation(self.store, device['id'], probe_id)
                return _probe_error(503, 'PROBE_ADDRESS_UNAVAILABLE', 'lease_issue',
                    '源机尚未启用隧道内检测地址', retryable=True,
                    next_action='联系管理员配置源机隧道检测地址',
                    operation_id=operation_id, operation_status=state)
        if path == '/client/v1/probe-lease/release' and request.method == 'POST':
            probe_id = value.get('probe_id')
            try:
                self.store.release_probe_lease(device['id'], probe_id)
                return web.json_response(self._probe_payload(device, probe_id))
            except ClientStoreError as exc:
                operation_id, state = _probe_operation(self.store, device['id'], probe_id)
                return _probe_error(409, 'PROBE_RELEASE_DENIED', 'lease_release', str(exc),
                    operation_id=operation_id, operation_status=state)
            except web.HTTPServiceUnavailable:
                operation_id, state = _probe_operation(self.store, device['id'], probe_id)
                return _probe_error(503, 'PROBE_ADDRESS_UNAVAILABLE', 'lease_release',
                    '源机尚未启用隧道内检测地址', retryable=True,
                    next_action='联系管理员配置源机隧道检测地址',
                    operation_id=operation_id, operation_status=state)
        if path in ('/client/v1/routes', '/client/v1/subscription') and request.method == 'GET':
            if path.endswith('subscription'):
                return web.json_response(self.subscription(request, device))
            if self.control_plane.has_logical_references(device['customer_id']):
                raise web.HTTPGone(text='该客户已迁移到逻辑订阅，请使用 /client/v1/subscription')
            return web.json_response({'routes': self.routes(device)})
        if path == '/client/v1/usage' and request.method == 'GET':
            return web.json_response(self.store.usage(device['customer_id']))
        if path == '/client/v1/lease' and request.method == 'POST':
            grant_id = str(value.get('grant_id', ''))
            requested_transport = value.get('transport_id')
            try:
                logical = self.control_plane.has_logical_references(device['customer_id'])
                if requested_transport is not None and not logical:
                    raise ClientStoreError('当前订阅不支持独立选择接入入口')
                selected_transport = None
                if logical:
                    resolved = self._resolve_authorized_choice(grant_id, requested_transport)
                    selected_transport = resolved['transport_id']
                relay_id = self.store.grant_relay_id(grant_id)
                capabilities = self.relay_capabilities(relay_id)
                self.require_grant_relay_contract(grant_id, capabilities)
                lease = self.store.issue_lease(
                    device['id'], grant_id,
                    relay_capabilities=capabilities, transport_id=selected_transport)
            except (ClientStoreError, ControlPlaneError) as exc:
                raise web.HTTPConflict(text=str(exc)) from exc
            lease = self._lease_endpoint(device, grant_id, lease)
            return web.json_response({'lease': self.lease_dto(lease)})
        if path == '/client/v1/lease/renew' and request.method == 'POST':
            lease_id = str(value.get('lease_id', ''))
            try:
                relay_id = self.store.lease_relay_id(lease_id)
                capabilities = self.relay_capabilities(relay_id)
                lease_record = next((row for row in self.store.list_leases() if row['id'] == lease_id), None)
                if not lease_record:
                    raise ClientStoreError('租约不存在')
                if self.control_plane.has_logical_references(device['customer_id']):
                    self._resolve_authorized_choice(
                        lease_record['grant_id'], lease_record.get('transport_id'))
                self.require_grant_relay_contract(lease_record['grant_id'], capabilities)
                lease = self.store.renew_lease(
                    device['id'], lease_id,
                    relay_capabilities=capabilities,
                    current_token=value.get('current_token'))
            except ClientStoreError as exc:
                raise web.HTTPConflict(text=str(exc)) from exc
            lease = self._lease_endpoint(device, lease['grant_id'], lease)
            return web.json_response({'lease': self.lease_dto(lease)})
        if path == '/client/v1/lease/release' and request.method == 'POST':
            self.store.release_lease(device['id'], str(value.get('lease_id', '')))
            return web.json_response({'ok': True})
        if path == '/client/v1/device/rotate' and request.method == 'POST':
            rotated = self.control_plane.rotate_device_credentials(
                device['id'], _signing_key(value.get('signing_public_key')),
                wireguard_public_key=_wireguard_key(value['wireguard_public_key'])
                if value.get('wireguard_public_key') is not None else None)
            return web.json_response({'device_id': rotated['id'], 'customer_id': rotated['customer_id'],
                                      'public_key': rotated['public_key'],
                                      'wireguard_public_key': rotated['wireguard_public_key']})
        raise web.HTTPNotFound(text='客户接口不存在')

    @staticmethod
    def lease_dto(lease: dict) -> dict:
        access_mode = lease.get('access_mode')
        default_egress_mode = {
            'wireguard': 'source_physical',
            'public_proxy': 'source_proxy',
        }.get(access_mode)
        value = {key: lease.get(key) for key in (
            'id', 'token', 'grant_id', 'alias', 'tunnel', 'endpoint', 'relay_public_key', 'allocated_address',
            'dns', 'allowed_ips', 'mtu', 'issued_at', 'expires_at', 'download_bps', 'upload_bps',
            'quota_bytes', 'used_bytes', 'remaining_bytes', 'access_mode', 'relay_interface',
            'egress_interface')}
        for key in ('node_id', 'transport_id', 'egress_id', 'directory_revision'):
            if key in lease:
                value[key] = lease[key]
        raw_mode = lease.get('egress_mode')
        raw_policy = lease.get('egress_policy', default_egress_mode)
        mode = _canonical_egress_mode(raw_mode) if raw_mode is not None else None
        policy = _canonical_egress_mode(raw_policy) if raw_policy is not None else None
        if mode is None:
            mode = policy
        if policy is None:
            policy = mode
        if mode is not None and policy is not None and mode != policy:
            raise ValueError('租约公网出口契约不一致')
        value['egress_mode'] = mode
        value['egress_policy'] = policy
        capability = lease.get('egress_capability')
        if capability is None:
            capability = (access_mode == 'wireguard' and
                          bool(str(lease.get('egress_interface') or '').strip()))
        value['egress_capability'] = capability is True
        if lease.get('access_mode') == 'public_proxy':
            config = json.loads(lease.get('proxy_config') or '{}')
            path_token = lease.get('proxy_path_token')
            if not path_token or not lease.get('proxy_uuid'):
                raise ValueError('Cloud 代理租约缺少入口凭据')
            value['proxy'] = config | {
                'uuid': lease['proxy_uuid'],
                'ws_path': '/sna-proxy/v2/' + path_token,
            }
        if value['remaining_bytes'] is None and value['quota_bytes'] is not None:
            value['remaining_bytes'] = max(0, value['quota_bytes'] - (value['used_bytes'] or 0))
        return value

    async def handle_relay(self, request: web.Request) -> web.Response:
        relay_id = self.authenticate_relay(request)
        if request.path == '/relay/v1/tunnel-probe' and request.method == 'POST':
            # A remote relay reports the socket peer it actually observed.
            # The control plane returns proof only for that relay's own active
            # short lease, expected private listener, and allocated peer IP.
            if relay_id == '*':
                return _probe_error(403, 'PROBE_RELAY_ID_REQUIRED', 'challenge',
                    '检测中继必须使用独立身份')
            try:
                _, value = await self.body(request)
            except ValueError:
                return _probe_error(400, 'PROBE_BODY_INVALID', 'challenge', '检测请求格式无效')
            probe_id = value.get('probe_id')
            nonce = value.get('nonce')
            device_id = value.get('device_id')
            if (not isinstance(probe_id, str) or not re.fullmatch(r'[0-9a-f]{64}', probe_id) or
                    not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{32}', nonce) or
                    not isinstance(device_id, str) or not 1 <= len(device_id) <= 256):
                return _probe_error(400, 'PROBE_CHALLENGE_INVALID', 'challenge', '检测挑战无效')
            try:
                peer = str(ipaddress.IPv4Address(value.get('peer_address')))
                local = str(ipaddress.IPv4Address(value.get('local_address')))
                lease = self.store.probe_lease(device_id, probe_id)
            except (TypeError, ipaddress.AddressValueError, ClientStoreError):
                return _probe_error(401, 'PROBE_LEASE_INVALID', 'challenge', '检测租约无效')
            if lease['status'] != 'active':
                return _probe_error(401, 'PROBE_LEASE_INACTIVE', 'challenge', '检测租约已失效')
            record = lease['lease']
            try:
                expected_local = (record.get('probe_address')
                    if record.get('probe_version') == 2 else
                    self._tunnel_probe_address(relay_id))
            except web.HTTPServiceUnavailable:
                return _probe_error(503, 'PROBE_ADDRESS_UNAVAILABLE', 'challenge',
                    '中继检测地址尚未配置', retryable=True)
            allocated = str(ipaddress.ip_interface(record['allocated_address']).ip)
            if (record['relay_interface'] != relay_id or peer != allocated or
                    local != expected_local):
                return _probe_error(403, 'PROBE_TUNNEL_REQUIRED', 'challenge',
                    '检测请求未经过该中继的授权隧道')
            proof = hmac.new(bytes.fromhex(probe_id),
                f'lanbridge-tunnel-probe-v1|{nonce}|{record["id"]}'.encode(),
                hashlib.sha256).hexdigest()
            return web.json_response({'ok': True, 'lease_id': record['id'],
                'nonce': nonce, 'proof': proof, 'server_time': int(time.time())})
        if request.path == '/relay/v1/reconcile' and request.method == 'GET':
            try:
                since = max(0, int(request.query.get('since', '0')))
            except ValueError:
                raise ValueError('中继游标无效') from None
            value = self.store.lease_reconciliation(since=since)
            policies = []
            capabilities = self.relay_capabilities(relay_id)
            max_active_peers = self.relay_max_active_peers(capabilities)
            max_offline_seconds = self.relay_max_offline_seconds(capabilities)
            customer_subnet = os.environ.get('SNA_RELAY_CUSTOMER_SUBNET', '10.203.0.0/16')
            management = [v.strip() for v in os.environ.get('SNA_RELAY_MANAGEMENT_SUBNETS', '').split(',') if v.strip()]
            active_wireguard = [lease for lease in value['active']
                                if lease.get('access_mode') == 'wireguard' and
                                (relay_id == '*' or lease['relay_interface'] == relay_id)]
            if len(active_wireguard) > max_active_peers:
                raise web.HTTPConflict(text='中继活跃 peer 数量超过已声明容量')
            if active_wireguard:
                try:
                    self.require_wireguard_relay_contract(capabilities)
                except ClientStoreError as exc:
                    raise web.HTTPConflict(text=str(exc)) from exc
            lease_budgets = {}
            by_customer = {}
            for lease in active_wireguard:
                by_customer.setdefault(lease['customer_id'], []).append(lease)
            for customer_id, customer_leases in by_customer.items():
                usage = self.store.usage(customer_id)
                if usage['remaining_bytes'] is None:
                    for lease in customer_leases:
                        lease_budgets[lease['id']] = None
                    continue
                remaining = usage['remaining_bytes']
                ordered = sorted(customer_leases, key=lambda item: item['id'])
                share, remainder = divmod(remaining, len(ordered))
                for index, lease in enumerate(ordered):
                    budget = share + (1 if index < remainder else 0)
                    lease_budgets[lease['id']] = ((lease['last_rx'] or 0) +
                                                  (lease['last_tx'] or 0) + budget)
            for lease in value['active']:
                if lease.get('access_mode') != 'wireguard':
                    continue
                if relay_id != '*' and lease['relay_interface'] != relay_id:
                    continue
                for direction in ('download', 'upload'):
                    if (lease[f'{direction}_bps'] is not None and
                            capabilities.get(f'{direction}_rate_limit') not in (True, 'enforced')):
                        raise web.HTTPConflict(text='中继未验证该方向的限速能力，已拒绝发布租约策略')
                quota = lease_budgets.get(lease['id'])
                if quota == 0:
                    continue
                offline_deadline = min(lease['expires_at'], value['generated_at'] + max_offline_seconds)
                policy = {'peer_id': lease['id'], 'public_key': lease['wireguard_public_key'],
                    'address': lease['allocated_address'], 'interface': lease['relay_interface'],
                    'egress_interface': lease['egress_interface'], 'customer_subnet': customer_subnet,
                    'egress_policy': _canonical_egress_mode(lease.get('egress_policy')),
                    'management_subnets': management, 'download_bps': lease['download_bps'],
                    'upload_bps': lease['upload_bps'], 'quota_bytes': quota,
                    'usage_baseline_bytes': (lease['last_rx'] or 0) + (lease['last_tx'] or 0),
                    'expires_at': lease['expires_at'], 'snapshot_at': value['generated_at'],
                    'offline_deadline': offline_deadline, 'enabled': True}
                if lease.get('probe_version') == 2:
                    policy['probe_only'] = True
                    policy['probe_echo_addresses'] = lease['probe_echo_addresses']
                try:
                    policy['tunnel_probe_address'] = (lease.get('probe_address')
                        or self._tunnel_probe_address(lease['relay_interface']))
                    policy['tunnel_probe_port'] = self._tunnel_probe_port()
                except web.HTTPServiceUnavailable:
                    if lease.get('probe_version') == 2:
                        raise
                policies.append(policy)
            policy_digests = {policy['peer_id']: relay_policy_fingerprint(policy)
                              for policy in policies}
            by_interface = {}
            for policy in policies:
                by_interface.setdefault(policy['interface'], {})[policy['peer_id']] = (
                    policy_digests[policy['peer_id']])
            for interface, digests in by_interface.items():
                self.store.prepare_relay_readiness(interface, digests)
            return web.json_response({'generated_at': value['generated_at'], 'policies': policies,
                                      'policy_digests': policy_digests,
                                      'max_offline_seconds': max_offline_seconds,
                                      'revoked': value['revoked']})
        if request.path == '/relay/v1/ready' and request.method == 'POST':
            if relay_id == '*':
                raise web.HTTPForbidden(text='中继就绪确认需要独立身份')
            _, value = await self.body(request)
            digests = value.get('policy_digests')
            if not isinstance(digests, dict):
                raise web.HTTPBadRequest(text='中继就绪确认格式无效')
            try:
                self.store.acknowledge_relay_readiness(relay_id, digests)
            except ClientStoreError as exc:
                raise web.HTTPConflict(text=str(exc)) from exc
            return web.json_response({'ok': True})
        if request.path == '/relay/v1/proxy-users' and request.method == 'GET':
            if relay_id not in ('*', 'cloud-vmess'):
                raise web.HTTPForbidden(text='中继无权读取公网代理用户')
            return web.json_response({'generated_at': int(time.time()),
                                      'users': self.store.active_proxy_users()})
        if request.path == '/relay/v1/usage' and request.method == 'POST':
            _, value = await self.body(request)
            lease_id = str(value.get('lease_id', ''))
            if relay_id != '*' and self.store.lease_relay_id(lease_id) != relay_id:
                raise web.HTTPForbidden(text='租约不属于当前中继')
            usage = self.store.record_usage_by_lease(lease_id,
                str(value.get('report_id', '')), int(value.get('rx_total', -1)), int(value.get('tx_total', -1)))
            return web.json_response({'usage': usage})
        raise web.HTTPNotFound(text='中继接口不存在')

