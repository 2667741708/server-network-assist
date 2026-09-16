"""Customer-only loopback panel for signed subscriptions and local tunnels."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
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
from .client_subscription import SubscriptionStore, local_device_id, probe_line
from .desktop import WINDOWS, change_tunnel, connectivity, local_data_dir, native_status

ROOT = Path(__file__).with_name('client_ui')


class ClientPanel:
    def __init__(self, data: Path):
        self.data = Path(data)
        self.token = secrets.token_urlsafe(32)
        self.origin = ''
        self.lock = threading.Lock()
        self.subscription = SubscriptionStore(self.data)
        self.online = OnlineServiceClient(self.data)
        self.active_path = self.data / 'customer-active-line.json'

    def payload(self):
        return self.subscription.cached()

    def active(self):
        try:
            value = json.loads(self.active_path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) and isinstance(value.get('line_id'), str) else None
        except (OSError, ValueError):
            return None

    def save_active(self, line_id, tunnel=None):
        self.data.mkdir(parents=True, exist_ok=True)
        temporary = self.active_path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'line_id': line_id, 'tunnel': tunnel,
                                         'started_at': int(time.time())}), encoding='utf-8')
        if os.name != 'nt':
            temporary.chmod(0o600)
        temporary.replace(self.active_path)

    def clear_active(self):
        with contextlib.suppress(FileNotFoundError):
            self.active_path.unlink()

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
        return {'version': __version__, 'platform': 'Windows' if WINDOWS else 'Ubuntu / Linux',
                'hostname': socket.gethostname(), 'elevated': native.get('elevated', False),
                'subscription': subscription, 'customer': payload.get('customer') if payload else None,
                'subscription_expires_at': payload.get('expires_at') if payload else None,
                'lines': lines, 'online_service': online, 'error': error, 'timestamp': int(time.time())}

    def _line(self, line_id):
        return next((row for row in self.payload()['lines'] if row['id'] == line_id), None)

    def connect(self, line_id):
        payload = self.payload()
        line = next((row for row in payload['lines'] if row['id'] == line_id), None)
        if not line or not line['available'] or line.get('expires_at', payload['expires_at']) <= int(time.time()):
            raise ValueError('线路未获授权、不可用或已经到期')
        probe = probe_line(line)
        if not probe['reachable']:
            raise ValueError('当前网络无法到达该接入线路')
        installed = {row['name']: row for row in native_status().get('tunnels', [])}
        if line['tunnel'] not in installed:
            raise ValueError('本机尚未安装此订阅对应的隧道配置')
        previous = None
        active = self.active()
        if active and active['line_id'] != line_id:
            previous = next((row for row in payload['lines'] if row['id'] == active['line_id']), None)
        if previous and installed.get(previous['tunnel'], {}).get('active'):
            change_tunnel(previous['tunnel'], 'disconnect')
        try:
            change_tunnel(line['tunnel'], 'connect')
        except Exception:
            if previous:
                with contextlib.suppress(Exception):
                    change_tunnel(previous['tunnel'], 'connect')
            raise
        self.save_active(line_id, line['tunnel'])
        return {'ok': True, 'probe': probe}

    def disconnect(self, line_id):
        payload = self.subscription.cached(allow_expired=True)
        line = next((row for row in payload['lines'] if row['id'] == line_id), None)
        if not line:
            raise ValueError('线路不在当前订阅授权中')
        installed = {row['name']: row for row in native_status().get('tunnels', [])}
        if line['tunnel'] not in installed:
            raise ValueError('本机未安装此线路')
        change_tunnel(line['tunnel'], 'disconnect')
        active = self.active()
        if active and active['line_id'] == line_id:
            self.clear_active()
        direct = connectivity(True)
        return {'ok': True, 'original_network': direct}

    def update_subscription(self):
        old_payload = None
        with contextlib.suppress(ValueError):
            old_payload = self.subscription.cached(allow_expired=True)
        active = self.active()
        cache_path = self.subscription.path.with_name('customer-subscription-cache.json')
        old_cache = cache_path.read_bytes() if cache_path.is_file() else None
        payload = self.subscription.update()
        if active:
            old = next((row for row in (old_payload or {}).get('lines', []) if row['id'] == active['line_id']), None)
            new = next((row for row in payload['lines'] if row['id'] == active['line_id']), None)
            revoked = (not old or not new or not new['available'] or new['expires_at'] <= int(time.time()))
            changed = bool(old and new and old['tunnel'] != new['tunnel'])
            if revoked or changed:
                try:
                    if old:
                        change_tunnel(old['tunnel'], 'disconnect')
                    self.clear_active()
                except Exception:
                    if old_cache is not None:
                        cache_path.write_bytes(old_cache)
                    raise
        return payload

    def remove_subscription(self):
        active = self.active()
        if active:
            payload = self.subscription.cached(allow_expired=True)
            line = next((row for row in payload['lines'] if row['id'] == active['line_id']), None)
            if line:
                change_tunnel(line['tunnel'], 'disconnect')
            self.clear_active()
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
        if old_line:
            change_tunnel(old_line['tunnel'], 'disconnect')
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
            if old_line:
                with contextlib.suppress(Exception):
                    change_tunnel(old_line['tunnel'], 'connect')
            raise
        if active:
            self.clear_active()
        return payload

    def reconcile(self):
        """Stop a client-owned tunnel when its signed authorization expires."""
        active = self.active()
        if not active:
            return
        revoked = False
        try:
            payload = self.subscription.cached()
            line = next((row for row in payload['lines'] if row['id'] == active['line_id']), None)
            revoked = not line or not line['available'] or line['expires_at'] <= int(time.time())
        except ValueError as exc:
            revoked = '过期' in str(exc)
        if revoked and isinstance(active.get('tunnel'), str):
            change_tunnel(active['tunnel'], 'disconnect')
            self.clear_active()


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
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
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
                    return self.reply(200, {'app': 'server-network-assist-client', 'version': __version__})
                if self.path == '/api/state':
                    return self.reply(200, panel.state())
                if self.path == '/api/online/routes':
                    return self.reply(200, {'routes': panel.online.routes()})
                if self.path == '/api/online/usage':
                    return self.reply(200, panel.online.usage())
            except Exception as exc:
                return self.reply(400, {'error': str(exc)[:500]})
            self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.authorized() or self.headers.get('Origin') != panel.origin:
                return self.reply(403, {'error': '请求来源或令牌无效'})
            if self.path not in ('/api/subscription', '/api/subscription/update', '/api/subscription/remove',
                                 '/api/line/probe', '/api/line/connect', '/api/line/disconnect',
                                 '/api/online/enroll', '/api/online/lease', '/api/online/lease/renew',
                                 '/api/online/lease/release', '/api/online/remove'):
                return self.reply(404, {'error': 'Not found'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 16384:
                    raise ValueError('请求大小无效')
                value = json.loads(self.rfile.read(size))
                if not isinstance(value, dict):
                    raise ValueError('请求必须为 JSON 对象')
                with panel.lock:
                    if self.path == '/api/online/enroll':
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
                        panel.online.release()
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


def serve(data: Path):
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
    ui_uid = os.environ.get('SNA_CLIENT_UI_UID')
    if os.name != 'nt' and ui_uid and os.geteuid() == 0:
        os.chown(instance, int(ui_uid), -1)
    def watchdog():
        next_update = 0.0
        while True:
            with panel.lock:
                if time.monotonic() >= next_update and panel.subscription.configured():
                    with contextlib.suppress(Exception):
                        panel.update_subscription()
                    next_update = time.monotonic() + 300
                with contextlib.suppress(Exception):
                    panel.reconcile()
            time.sleep(30)
    threading.Thread(target=watchdog, daemon=True, name='client-expiry-watchdog').start()
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description='Server Network Assist customer client')
    parser.add_argument('--data', type=Path, default=local_data_dir())
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--device-id', action='store_true')
    args = parser.parse_args()
    if args.device_id:
        print(local_device_id())
        return
    if args.serve:
        serve(args.data)
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
