"""Customer-only loopback panel for signed subscriptions and local tunnels."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
from pathlib import Path
import secrets
import socket
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .client_online import OnlineServiceClient
from .client_paths import customer_data
from .client_subscription import SubscriptionStore, local_device_id, probe_line
from .desktop import WINDOWS, change_tunnel, connectivity, local_data_dir, native_status

ROOT = Path(__file__).with_name('client_ui')


def restored_network_probe(timeout=5):
    """Bound the optional post-disconnect probe, including DNS resolution."""
    results = queue.Queue(maxsize=1)

    def probe():
        try:
            results.put(connectivity(True))
        except Exception:
            results.put({'ok': None, 'milliseconds': None, 'error': 'probe_failed'})

    threading.Thread(target=probe, daemon=True).start()
    try:
        return results.get(timeout=timeout)
    except queue.Empty:
        return {'ok': None, 'milliseconds': None, 'timed_out': True}


class ClientPanel:
    def __init__(self, data: Path):
        self.data = Path(data)
        self.token = secrets.token_urlsafe(32)
        self.origin = ''
        self.lock = threading.Lock()
        self.subscription = SubscriptionStore(self.data)
        self.online = OnlineServiceClient(self.data)
        self.active_path = self.data / 'customer-active-line.json'
        self.leaving_path = self.data / 'customer-leaving-network.json'
        self.attachment = None
        self.event_lock = threading.Lock()
        self.events = []
        self.event_callbacks = []
        self.event_sequence = 0
        self.ui_activate = None
        self._network_cache = None
        self._network_cache_at = 0.0

    def publish_event(self, code, message, error=False):
        with self.event_lock:
            self.event_sequence += 1
            event = {'sequence': self.event_sequence, 'code': code, 'message': message,
                     'error': bool(error), 'timestamp': int(time.time())}
            self.events = (self.events + [event])[-20:]
        for callback in list(self.event_callbacks):
            try:
                callback(event)
            except Exception:
                pass  # Optional notification must never block network recovery.
        return event

    def observe_attachment(self, value):
        from .client_attachment import change_reason
        reason = change_reason(self.attachment, value)
        self.attachment = value
        self._network_cache = None
        if not reason:
            return None
        active = self.active()
        if active and active.get('kind') == 'online-public-proxy':
            message = ('物理网络暂时断开，公网代理将在网络恢复后继续连接。' if reason == 'attachment_lost'
                       else '检测到网络切换，正在让公网代理重新绑定系统当前出口。')
            self.publish_event(reason, message, reason == 'attachment_lost')
            return reason
        message = ('Wi-Fi 或有线接入已断开。' if reason == 'attachment_lost' else
                   '检测到网络切换，流量将交还给系统当前选择的网络。')
        self.publish_event(reason, message + ' 正在退出本客户端借网。', True)
        with self.lock:
            try:
                self.leave_network()
            except Exception:
                self.publish_event('recovery_failed', message + ' 恢复尚未完成，请重试退出组网。', True)
                return
        self.publish_event(reason, message + ' 本客户端已退出组网，不会自动重新接入。', True)
        return reason

    def payload(self):
        return self.subscription.cached()

    def active(self):
        try:
            value = json.loads(self.active_path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) and isinstance(value.get('line_id'), str) else None
        except (OSError, ValueError):
            return None

    def save_active(self, line_id, tunnel=None, kind='static', **settings):
        self.data.mkdir(parents=True, exist_ok=True)
        temporary = self.active_path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'line_id': line_id, 'tunnel': tunnel, 'kind': kind,
                                         'started_at': int(time.time()), **settings}), encoding='utf-8')
        if os.name != 'nt':
            temporary.chmod(0o600)
        temporary.replace(self.active_path)

    def clear_active(self):
        with contextlib.suppress(FileNotFoundError):
            self.active_path.unlink()

    def network_context(self, force=False):
        if not WINDOWS:
            return {'kind': 'unsupported', 'label': '当前平台未实现自动识别',
                    'internet_proxy': True, 'source_wireguard': True,
                    'reason': 'Linux 客户端按线路配置进行连接。'}
        if not force and self._network_cache and time.monotonic() - self._network_cache_at < 4:
            return self._network_cache
        from .client_attachment import preferred, snapshot, validate_selected_access
        from .client_campus import authentication
        from .client_network import _campus_address, classify
        attachment = snapshot()
        auth = 'unknown'
        link, _ = preferred(attachment)
        if _campus_address(link):
            with contextlib.suppress(Exception):
                auth = authentication(attachment)
        service = None
        if self.online.configured():
            service = self._select_online_service(attachment) is not None
        value = classify(attachment, authentication=auth, service_reachable=service)
        value['authentication'] = auth
        self.attachment = attachment
        self._network_cache = value
        self._network_cache_at = time.monotonic()
        return value

    def _select_online_service(self, attachment):
        from .client_attachment import preferred, validate_selected_access
        from .client_network import _campus_address, endpoint_is_private
        link, _ = preferred(attachment)
        campus = _campus_address(link)
        urls = sorted(self.online.service_urls(),
                      key=lambda value: endpoint_is_private(value) != campus)
        for value in urls:
            try:
                validate_selected_access(attachment, value)
            except ValueError:
                continue
            self.online.prefer_base_url(value)
            return value
        return None

    def _available_routes(self, routes):
        network = self.network_context()
        from .client_network import route_availability
        result = []
        for route in routes:
            available, reason = route_availability(route, network)
            result.append(route | {'client_available': available,
                                   'client_unavailable_reason': reason})
        return result, network

    @staticmethod
    def _subscription_routes(payload):
        """Use the resolved logical subscription as the only route source."""
        routes = payload.get('routes') if isinstance(payload, dict) else None
        if not isinstance(routes, list):
            raise ValueError('服务端没有返回可用的逻辑订阅线路')
        return routes

    def online_catalog(self):
        payload = self.online.subscription()
        routes, network = self._available_routes(self._subscription_routes(payload))
        return payload | {'routes': routes, 'network': network}

    def online_connect(self, grant_id, *, capture_mode='system_proxy', proxy_mode='rule'):
        from . import client_tunnel, client_public_proxy
        if self.active() or self.leaving_path.exists():
            raise ValueError('请先退出当前客户借网节点')
        subscription = self.online.subscription()
        routes, _ = self._available_routes(self._subscription_routes(subscription))
        route = next((row for row in routes if row.get('id') == grant_id), None)
        if not route:
            raise ValueError('所选套餐节点不存在或已经到期')
        if not route.get('client_available', True):
            raise ValueError(route.get('client_unavailable_reason') or '当前网络不能使用所选线路')
        access_mode = route.get('access_mode', 'wireguard')
        network_before = None
        if WINDOWS and access_mode == 'wireguard':
            from .client_attachment import snapshot
            from .client_campus import campus_link, authentication
            attachment = snapshot()
            network_before = attachment
            link = campus_link(attachment)
            if self._select_online_service(attachment) is None:
                raise ValueError('当前校园接入无法连接任一受信任订阅入口')
            status = authentication(attachment)
            if link.get('wifi') and status != 'online':
                raise ValueError('校园 Wi-Fi 必须先完成运营商认证，再使用源网线路')
            self.attachment = attachment
        capabilities = route.get('capabilities') if isinstance(route.get('capabilities'), dict) else {}
        allowed_capture = capabilities.get('capture_modes', ['system_proxy', 'tun'])
        allowed_proxy = capabilities.get('proxy_modes', ['rule', 'global', 'direct'])
        if access_mode == 'public_proxy' and (capture_mode not in allowed_capture or proxy_mode not in allowed_proxy):
            raise ValueError('套餐未授权所选流量接管或代理模式')
        lease = self.online.lease(grant_id)
        tunnel = None
        try:
            if lease.get('access_mode') == 'public_proxy':
                proxy = client_public_proxy.start(self.data, lease, capture_mode=capture_mode,
                                                   proxy_mode=proxy_mode)
                self.save_active(grant_id, None, 'online-public-proxy',
                                 capture_mode=capture_mode, proxy_mode=proxy_mode)
            else:
                tunnel = client_tunnel.install(
                    self.data, lease, self.online._record()['wireguard_private_key'],
                    network_before=network_before)
                proxy = None
                self.save_active(grant_id, tunnel, 'online')
        except Exception as failure:
            try:
                self.leave_network()
            except Exception as rollback:
                raise RuntimeError('借网失败，恢复记录已保留，将继续重试退出：' + str(rollback)[:200]) from failure
            raise
        if lease.get('access_mode') == 'public_proxy':
            capture = 'TUN' if capture_mode == 'tun' else '系统代理'
            self.publish_event('connected', f'公网代理已启动：{capture} · {proxy_mode}。')
        else:
            self.publish_event('connected', '借网已启动；切换物理网络将自动退出组网。')
        return {'ok': True, 'lease': lease, 'tunnel': tunnel, 'proxy': proxy}

    def proxy_settings(self, capture_mode, proxy_mode):
        active = self.active()
        if not active or active.get('kind') != 'online-public-proxy':
            raise ValueError('当前没有运行中的公网代理')
        lease = self.online.active_lease()
        if not lease or lease.get('expires_at', 0) <= int(time.time()):
            raise ValueError('公网代理租约已失效')
        route = next((row for row in self.online_catalog().get('routes', [])
                      if row.get('id') == active.get('line_id')), {})
        capabilities = route.get('capabilities') if isinstance(route.get('capabilities'), dict) else {}
        if capture_mode not in capabilities.get('capture_modes', ['system_proxy', 'tun']):
            raise ValueError('套餐未授权所选流量接管方式')
        if proxy_mode not in capabilities.get('proxy_modes', ['rule', 'global', 'direct']):
            raise ValueError('套餐未授权所选代理模式')
        from . import client_public_proxy
        result = client_public_proxy.ensure(self.data, lease, capture_mode=capture_mode,
                                            proxy_mode=proxy_mode, force=True)
        self.save_active(active['line_id'], None, 'online-public-proxy',
                         capture_mode=capture_mode, proxy_mode=proxy_mode)
        self.publish_event('proxy_settings_changed', '公网代理设置已切换。')
        return {'ok': True, 'proxy': result}

    def access_report(self):
        """Read-only GUI preflight. Never acquire a lease or change networking."""
        result = {'ready': False, 'network': '未检查', 'campus': '未检查',
                  'service': '请先读取订阅', 'account': '未检查',
                  'message': '仅检查接入；不会开始借网或切换 Wi-Fi。'}
        if not WINDOWS:
            return {**result, 'message': '此接入保护检查暂适用于 Windows。'}
        from .client_attachment import snapshot, preferred
        try:
            attachment = snapshot()
            link, _ = preferred(attachment)
            result['network'] = (str(link.get('profile') or link.get('name') or link['id']) +
                                 '\n' + ', '.join(link.get('addresses', []))) if link else '没有可用物理出口'
            context = self.network_context(force=True)
            result['campus'] = context['label']
            status = context.get('authentication', 'unknown')
            result['account'] = {'offline': '个人账号未在线', 'online': '个人账号已在线，保留个人网络',
                                  'unknown': '认证状态无法确认，暂不允许借网'}.get(status, '认证状态无法确认')
            if self.online.configured():
                result['service'] = '订阅入口可达' if context['kind'] != 'offline' else '订阅入口不可达'
                result['ready'] = ((context.get('internet_proxy') or context.get('source_wireguard'))
                                   and not self.active() and not self.leaving_path.exists())
            if result['ready']:
                result['message'] = context['reason']
        except Exception as exc:
            result['message'] = '接入检查未完成，保持原网络：' + str(exc)[:300]
        return result

    def online_disconnect(self):
        result = self.leave_network()
        return {**result, 'original_network': restored_network_probe()}

    def leave_network(self):
        """Local recovery never depends on a reachable subscription server."""
        from . import client_tunnel, client_clash_coexist, client_public_proxy
        self.data.mkdir(parents=True, exist_ok=True)
        temporary = self.leaving_path.with_suffix('.tmp')
        temporary.write_text('{"leaving":true}', encoding='utf-8')
        temporary.replace(self.leaving_path)
        active = self.active()
        owned_path = self.data / client_tunnel.OWNED
        owned = client_tunnel.read_owned(self.data) if owned_path.exists() else {}
        names = set()
        if owned.get('tunnel'):
            names.add(owned['tunnel'])
        if active and active.get('kind') == 'online' and active.get('tunnel'):
            names.add(active['tunnel'])
        coexist = self.data / client_clash_coexist.STATE
        if coexist.exists():
            names.add(json.loads(coexist.read_text(encoding='utf-8'))['tunnel'])
        from .client_app_routing import restore as restore_apps
        app_error = None
        try:
            restore_apps(self.data)
        except Exception as exc:
            app_error = exc
        tunnel_error = None
        proxy_error = None
        try:
            client_public_proxy.stop(self.data)
        except Exception as exc:
            proxy_error = exc
        for name in names:
            try:
                client_tunnel.stop(name, self.data)
            except Exception as exc:
                tunnel_error = exc
        if (active and active.get('kind') not in ('online', 'online-public-proxy')
                and active.get('tunnel') not in names):
            raise RuntimeError('旧静态线路缺少客户隧道所有权记录，不能安全修改；请联系管理员迁移')
        from .client_original_network import restore
        restore_error = None
        try:
            restore(self.data)
        except Exception as exc:
            restore_error = exc
        pending = proxy_error or tunnel_error or app_error or restore_error
        if pending:
            for name in names:
                client_tunnel.mark_rollback_pending(self.data, name, pending)
        if proxy_error:
            raise RuntimeError('公网代理恢复尚未完成，请重试退出：' + str(proxy_error)) from proxy_error
        if tunnel_error:
            raise RuntimeError('客户网络恢复尚未完成，请重试退出：' + str(tunnel_error)) from tunnel_error
        if app_error:
            raise RuntimeError('客户隧道已退出，但应用规则恢复未完成，请重试退出') from app_error
        if restore_error:
            raise RuntimeError('客户隧道已退出，但原系统代理恢复未完成，请重试退出') from restore_error
        for name in names:
            client_tunnel.finalize_owned(self.data, name)
        self.clear_active()
        self.leaving_path.unlink(missing_ok=True)
        self.publish_event('left_network', '已退出服务，本客户端修改的网络配置已恢复。')
        error = None
        if self.online.configured():
            try:
                self.online.release()
            except Exception as exc:
                error = str(exc)[:200]
        return {'ok': True, 'left_network': True, 'release_error': error}

    def state(self):
        try:
            subscription = self.subscription.summary()
            payload = self.payload()
            error = None
        except ValueError as exc:
            subscription, error = {'configured': self.subscription.configured(), 'device_id': local_device_id()}, str(exc)
            try:
                payload = self.subscription.cached(allow_expired=True)
            except ValueError:
                payload = None
        online = None
        if self.online.configured():
            try:
                online = self.online.summary()
                if not self.subscription.configured():
                    error = None
            except ValueError as exc:
                online = {'configured': True, 'error': str(exc)}
        native = native_status()
        tunnels = {row['name']: row for row in native.get('tunnels', [])}
        lines = []
        for row in payload.get('lines', []) if payload else []:
            tunnel = tunnels.get(row['tunnel'])
            lines.append({k: v for k, v in row.items() if k != 'tunnel'} | {
                'installed': tunnel is not None, 'connected': bool(tunnel and tunnel.get('active')),
                'handshake': tunnel.get('handshake') if tunnel else None,
                'received': tunnel.get('received') if tunnel and tunnel.get('telemetry') else None,
                'sent': tunnel.get('sent') if tunnel and tunnel.get('telemetry') else None,
            })
        proxy = None
        with contextlib.suppress(Exception):
            from .client_public_proxy import status as proxy_status
            proxy = proxy_status(self.data)
        network = None
        with contextlib.suppress(Exception):
            network = self.network_context()
        recovery = None
        with contextlib.suppress(Exception):
            from .client_tunnel import read_owned
            ledger = read_owned(self.data)
            if ledger:
                recovery = {
                    'state': ledger.get('state', 'legacy'),
                    'reason': ledger.get('reason', ''),
                    'checks': ledger.get('checks', {}),
                    'desired': bool(ledger.get('desired')),
                }
        recovering = self.leaving_path.exists() or bool(
            recovery and recovery.get('state') in ('leaving', 'tunnel_removed', 'rollback_pending'))
        return {'version': __version__, 'platform': 'Windows' if WINDOWS else 'Ubuntu / Linux',
                'hostname': socket.gethostname(), 'elevated': native.get('elevated', False),
                'subscription': subscription, 'customer': payload.get('customer') if payload else None,
                'subscription_expires_at': payload.get('expires_at') if payload else None,
                'lines': lines, 'active': self.active(), 'online_service': online, 'error': error,
                'network_events': list(self.events), 'physical_attachment': self.attachment,
                'recovering': recovering, 'network_recovery': recovery, 'public_proxy': proxy,
                'network_context': network,
                'timestamp': int(time.time())}

    def _line(self, line_id):
        return next((row for row in self.payload()['lines'] if row['id'] == line_id), None)

    def connect(self, line_id):
        raise ValueError('客户纯享版不再激活旧静态隧道；请使用在线订阅创建客户专用隧道')

    def disconnect(self, line_id):
        raise ValueError('旧静态线路不能证明隧道所有权；请使用退出组网恢复客户专用连接')

    def update_subscription(self):
        old_payload = None
        with contextlib.suppress(ValueError):
            old_payload = self.subscription.cached(allow_expired=True)
        active = self.active()
        cache_path = self.subscription.path.with_name('customer-subscription-cache.json')
        old_cache = cache_path.read_bytes() if cache_path.is_file() else None
        payload = self.subscription.update()
        if active and active.get('kind') in ('online', 'online-public-proxy'):
            return payload
        if active:
            old = next((row for row in (old_payload or {}).get('lines', []) if row['id'] == active['line_id']), None)
            new = next((row for row in payload['lines'] if row['id'] == active['line_id']), None)
            revoked = (not old or not new or not new['available'] or new['expires_at'] <= int(time.time()))
            changed = bool(old and new and old['tunnel'] != new['tunnel'])
            if revoked or changed:
                try:
                    self.leave_network()
                except Exception:
                    if old_cache is not None:
                        cache_path.write_bytes(old_cache)
                    raise
        return payload

    def remove_subscription(self):
        self.leave_network()
        self.subscription.remove()

    def replace_subscription(self, url):
        active = self.active()
        old_payload = None
        with contextlib.suppress(ValueError):
            old_payload = self.subscription.cached(allow_expired=True)
        record_path = self.subscription.path
        cache_path = record_path.with_name('customer-subscription-cache.json')
        backups = {path: path.read_bytes() for path in (record_path, cache_path) if path.is_file()}
        old_line = next((row for row in (old_payload or {}).get('lines', [])
                         if active and row['id'] == active['line_id']), None)
        if active or self.leaving_path.exists():
            self.leave_network()
        try:
            self.subscription.save_url(url)
            payload = self.subscription.update()
        except Exception:
            for path in (record_path, cache_path):
                if path in backups:
                    path.write_bytes(backups[path])
                else:
                    with contextlib.suppress(FileNotFoundError):
                        path.unlink()
            raise
        if active:
            self.clear_active()
        return payload

    def reconcile(self):
        """Stop a client-owned tunnel when its signed authorization expires."""
        if self.leaving_path.exists():
            self.leave_network()
            return
        active = self.active()
        if not active:
            return
        if active.get('kind') in ('online', 'online-public-proxy'):
            lease = self.online.active_lease()
            now = int(time.time())
            if not lease or lease.get('expires_at', 0) <= now:
                self.online_disconnect()
            elif lease['expires_at'] - now < 120:
                lease = self.online.renew()
            if (lease and lease.get('expires_at', 0) > now and
                    active.get('kind') == 'online-public-proxy'):
                from . import client_public_proxy
                client_public_proxy.ensure(
                    self.data, lease,
                    capture_mode=active.get('capture_mode', 'system_proxy'),
                    proxy_mode=active.get('proxy_mode', 'rule'))
            elif lease and lease.get('expires_at', 0) > now and os.name == 'nt':
                from . import client_tunnel
                try:
                    client_tunnel.refresh_coexist(self.data, lease, active['tunnel'])
                    client_tunnel.health(self.data, lease, active['tunnel'])
                except Exception:
                    self.leave_network()
                    raise
            return
        revoked = False
        try:
            payload = self.subscription.cached()
            line = next((row for row in payload['lines'] if row['id'] == active['line_id']), None)
            revoked = not line or not line['available'] or line['expires_at'] <= int(time.time())
        except ValueError as exc:
            revoked = '过期' in str(exc)
        if revoked and isinstance(active.get('tunnel'), str):
            self.leave_network()


def handler_for(panel: ClientPanel):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, value, content_type='application/json; charset=utf-8'):
            body = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, dict) else value
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self' data:; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

        def authorized(self):
            return (self.headers.get('Host') == panel.origin.removeprefix('http://')
                    and secrets.compare_digest(self.headers.get('X-Client-Token', ''), panel.token)
                    and self.headers.get('Origin', panel.origin) == panel.origin)

        def do_GET(self):
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/client.js': ('client.js', 'text/javascript'),
                      '/client.css': ('client.css', 'text/css'), '/framework7-bundle.min.css': ('framework7-bundle.min.css', 'text/css'),
                      '/framework7-bundle.min.js': ('framework7-bundle.min.js', 'text/javascript'),
                      '/framework7-default-theme.css': ('framework7-default-theme.css', 'text/css')}
            if self.path in assets:
                name, kind = assets[self.path]
                return self.reply(200, (ROOT / name).read_bytes(), kind)
            if not self.authorized():
                return self.reply(403, {'error': '请从客户端快捷方式打开'})
            try:
                if self.path == '/api/health':
                    return self.reply(200, {'app': 'server-network-assist-client', 'version': __version__, 'pid': os.getpid(), 'native_ui': callable(panel.ui_activate)})
                if self.path == '/api/state':
                    return self.reply(200, panel.state())
                if self.path == '/api/network/access':
                    return self.reply(200, panel.access_report())
                if self.path == '/api/apps/policies':
                    from .client_app_routing import policies
                    return self.reply(200, {'policies': policies()})
                if self.path == '/api/online/routes':
                    catalog = panel.online_catalog()
                    return self.reply(200, {'routes': catalog['routes']})
                if self.path == '/api/online/subscription':
                    return self.reply(200, panel.online_catalog())
                if self.path == '/api/online/usage':
                    return self.reply(200, panel.online.usage())
            except Exception as exc:
                return self.reply(400, {'error': str(exc)[:500]})
            self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.authorized() or self.headers.get('Origin') != panel.origin:
                return self.reply(403, {'error': '请求来源或令牌无效'})
            if self.path == '/api/ui/show':
                try:
                    callback = panel.ui_activate
                    if not callable(callback):
                        return self.reply(409, {'error': '当前后台没有桌面窗口'})
                    return self.reply(200, {'shown': callback() is True})
                except Exception:
                    return self.reply(503, {'error': '窗口暂不可用；没有修改网络配置'})
            if self.path not in ('/api/subscription', '/api/subscription/update', '/api/subscription/remove',
                                 '/api/line/probe', '/api/line/connect', '/api/line/disconnect',
                                 '/api/online/enroll', '/api/online/remove',
                                 '/api/online/connect', '/api/online/disconnect', '/api/network/leave',
                                 '/api/network/personal-login', '/api/apps/rule',
                                 '/api/proxy/settings'):
                return self.reply(404, {'error': 'Not found'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 16384:
                    raise ValueError('请求大小无效')
                value = json.loads(self.rfile.read(size))
                if not isinstance(value, dict):
                    raise ValueError('请求必须为 JSON 对象')
                with panel.lock:
                    if self.path == '/api/apps/rule':
                        if panel.leaving_path.exists():
                            raise ValueError('请先完成网络恢复，再修改应用流量')
                        if not panel.active():
                            from .client_attachment import snapshot
                            from .client_campus import authentication
                            if not WINDOWS or authentication(snapshot()) != 'online':
                                raise ValueError('先借网或登录个人校园账号，再配置临时应用流量')
                        from .client_app_routing import apply
                        result = apply(panel.data, str(value.get('process', '')), str(value.get('policy', '')))
                    elif self.path == '/api/network/personal-login':
                        from .client_campus import PORTAL
                        result = {**panel.leave_network(), 'portal_url': PORTAL}
                        panel.publish_event('personal_login', '已退出借网，可以使用自己的校园网账号登录。')
                    elif self.path == '/api/network/leave':
                        result = panel.leave_network()
                    elif self.path == '/api/online/connect':
                        result = panel.online_connect(
                            str(value.get('grant_id', '')),
                            capture_mode=str(value.get('capture_mode', 'system_proxy')),
                            proxy_mode=str(value.get('proxy_mode', 'rule')))
                    elif self.path == '/api/proxy/settings':
                        result = panel.proxy_settings(
                            str(value.get('capture_mode', 'system_proxy')),
                            str(value.get('proxy_mode', 'rule')))
                    elif self.path == '/api/online/disconnect':
                        result = panel.online_disconnect()
                    elif self.path == '/api/online/enroll':
                        if panel.active() or panel.leaving_path.exists():
                            raise ValueError('请先退出当前借网并完成恢复，再更换订阅身份')
                        result = panel.online.enroll(str(value.get('url', '')), str(value.get('label', '')))
                        result = {'ok': True, **result}
                    elif self.path == '/api/online/lease':
                        result = {'ok': True, 'lease': panel.online.lease(str(value.get('grant_id', '')))}
                    elif self.path == '/api/online/lease/renew':
                        result = {'ok': True, 'lease': panel.online.renew(value.get('lease_id'))}
                    elif self.path == '/api/online/lease/release':
                        panel.online.release(value.get('lease_id'))
                        result = {'ok': True}
                    elif self.path == '/api/online/remove':
                        panel.online_disconnect()
                        panel.online.remove()
                        result = {'ok': True}
                    elif self.path == '/api/subscription':
                        payload = panel.replace_subscription(str(value.get('url', '')))
                        result = {'ok': True, 'expires_at': payload['expires_at']}
                    elif self.path == '/api/subscription/update':
                        payload = panel.update_subscription()
                        result = {'ok': True, 'expires_at': payload['expires_at']}
                    elif self.path == '/api/subscription/remove':
                        panel.remove_subscription()
                        result = {'ok': True}
                    else:
                        line = panel._line(str(value.get('id', '')))
                        if self.path == '/api/line/probe':
                            if not line:
                                raise ValueError('线路不在当前订阅授权中')
                            result = probe_line(line)
                        elif self.path == '/api/line/connect':
                            result = panel.connect(str(value.get('id', '')))
                        else:
                            result = panel.disconnect(str(value.get('id', '')))
                self.reply(200, result)
            except Exception as exc:
                self.reply(400, {'error': str(exc)[:500]})
    return Handler


def serve(data: Path, ready=None):
    panel = ClientPanel(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(panel))
    panel.origin = f'http://127.0.0.1:{server.server_port}'
    state = {'port': server.server_port, 'token': panel.token, 'pid': os.getpid()}
    data.mkdir(parents=True, exist_ok=True)
    instance = data / 'client-instance.json'
    temporary = data / 'client-instance.tmp'
    temporary.write_text(json.dumps(state), encoding='utf-8')
    if os.name != 'nt':
        temporary.chmod(0o600)
    temporary.replace(instance)
    if ready:
        ready(panel, server)
    ui_uid = os.environ.get('SNA_CLIENT_UI_UID')
    if os.name != 'nt' and ui_uid and os.geteuid() == 0:
        os.chown(instance, int(ui_uid), -1)
    stopped = threading.Event()
    def attachment_watchdog():
        from .client_attachment import snapshot
        from .client_campus import authentication, campus_link
        failed = False
        next_auth = 0
        while not stopped.is_set():
            active = panel.active()
            if not active and not panel.leaving_path.exists():
                stopped.wait(2)
                continue  # Idle GUI never needs repeated physical probes.
            try:
                value = snapshot()
                reason = None
                if not stopped.is_set():
                    reason = panel.observe_attachment(value)
                current = panel.active()
                if current and current.get('kind') == 'online-public-proxy':
                    if reason == 'network_changed':
                        lease = panel.online.active_lease()
                        if lease:
                            from . import client_public_proxy
                            client_public_proxy.ensure(
                                panel.data, lease,
                                capture_mode=current.get('capture_mode', 'system_proxy'),
                                proxy_mode=current.get('proxy_mode', 'rule'), force=True)
                    failed = False
                    stopped.wait(2)
                    continue
                if current and time.monotonic() >= next_auth and not stopped.is_set():
                    link = campus_link(value)
                    next_auth = time.monotonic() + 5
                    status = authentication(value) if link.get('wifi') else 'not-required'
                    if link.get('wifi') and status != 'online':
                        with panel.lock:
                            panel.leave_network()
                        panel.publish_event('campus_wifi_auth_lost',
                            '校园 Wi-Fi 认证已失效或无法确认，已退出源网并恢复原网络。', True)
                failed = False
            except Exception:
                if not failed:
                    panel.publish_event('monitor_failed', '无法确认物理网络接入状态，请检查网卡并退出组网。', True)
                current = panel.active()
                if not current or current.get('kind') != 'online-public-proxy':
                    with panel.lock:
                        try:
                            panel.leave_network()
                        except Exception:
                            if not failed:
                                panel.publish_event('recovery_failed', '接入检测失败且恢复未完成，请重试退出组网。', True)
                failed = True
            stopped.wait(2)
    def watchdog():
        next_update = 0.0
        failed = False
        while not stopped.is_set():
            with panel.lock:
                if time.monotonic() >= next_update and panel.subscription.configured():
                    with contextlib.suppress(Exception):
                        panel.update_subscription()
                    next_update = time.monotonic() + 300
                try:
                    panel.reconcile()
                    failed = False
                except Exception:
                    if not failed:
                        panel.publish_event('lease_check_failed', '租约或代理兼容检查失败，请查看订阅状态；到期仍会退出组网。', True)
                    failed = True
            stopped.wait(3 if os.name == 'nt' else 30)
    if WINDOWS:
        threading.Thread(target=attachment_watchdog, daemon=True, name='client-attachment-watchdog').start()
    threading.Thread(target=watchdog, daemon=True, name='client-expiry-watchdog').start()
    try:
        server.serve_forever()
    finally:
        stopped.set()
        try:
            with panel.lock:
                panel.leave_network()
        finally:
            server.server_close()
            instance.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='Server Network Assist customer client')
    parser.add_argument('--data', type=Path, default=customer_data())
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--device-id', action='store_true')
    parser.add_argument('--disconnect', '--leave-network', action='store_true', dest='disconnect',
                        help='退出组网并恢复本客户端修改的网络配置；保留订阅')
    args = parser.parse_args()
    if args.device_id:
        print(local_device_id())
        return
    if args.serve:
        from .client_ownership import CustomerLock
        with CustomerLock(args.data):
            serve(args.data)
        return
    if args.disconnect:
        state_path = args.data / 'client-instance.json'
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
            origin = f"http://127.0.0.1:{state['port']}"
            request = urllib.request.Request(origin + '/api/network/leave', data=b'{}',
                headers={'X-Client-Token': state['token'], 'Origin': origin, 'Content-Type': 'application/json'})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=60) as response:
                result = json.load(response)
        except (FileNotFoundError, ConnectionRefusedError, urllib.error.URLError, ValueError, KeyError) as exc:
            if isinstance(exc, urllib.error.HTTPError):
                raise SystemExit('退出未完成：' + exc.read().decode('utf-8'))
            from .client_ownership import CustomerLock
            with CustomerLock(args.data, timeout=1):
                result = ClientPanel(args.data).leave_network()
        print(json.dumps(result, ensure_ascii=False))
        return
    state_path = args.data / 'client-instance.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
        request = urllib.request.Request(f"http://127.0.0.1:{state['port']}/api/health",
                                         headers={'X-Client-Token': state['token']})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=1):
            pass
    except Exception:
        raise SystemExit('客户客户端后台尚未启动，请先运行 --serve 或安装后台服务')
    url = f"http://127.0.0.1:{state['port']}/#token={state['token']}"
    webbrowser.open(url)


if __name__ == '__main__':
    main()
