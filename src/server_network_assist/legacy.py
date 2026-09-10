from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
from .auth import hash_password, verify_password
from .store import Store

COMMANDS = {
    'status': ['sh', '-c', 'hostname; uptime; free -m'],
    'gpu': ['nvidia-smi'],
    'disk': ['df', '-h'],
    'processes': ['ps', '-eo', 'pid,user,pcpu,pmem,comm', '--sort=-pcpu'],
}


class Panel:
    def __init__(self, data: Path):
        self.data = data
        self.credentials = json.loads((data / 'credentials.json').read_text(encoding='utf-8'))
        self.servers = json.loads((data / 'servers.json').read_text(encoding='utf-8'))
        self.store = Store(data / 'panel.sqlite3')
        self.lock = threading.RLock()
        self.attempts = []
        self.slots = threading.BoundedSemaphore(4)

    def audit(self, action, target='', details=None):
        try:
            self.store.add_audit('admin', action, target, details or {}, '')
        except Exception as exc:
            print('Audit unavailable:', type(exc).__name__, flush=True)

    def authenticate(self, username, password, key):
        with self.lock:
            self.attempts = [t for t in self.attempts if time.time() - t < 900]
            if len(self.attempts) >= 8:
                return False
            c = self.credentials
            password_ok = verify_password(password, c['password_hash'])
            key_ok = bool(c.get('key_hash')) and hmac.compare_digest(
                hashlib.sha256(key.encode()).hexdigest(), c.get('key_hash', ''))
            ok = username == 'admin' and (password_ok or key_ok)
            if ok:
                self.attempts.clear()
            else:
                self.attempts.append(time.time())
            return ok

    def rotate_key(self, revoke=False):
        key = '' if revoke else secrets.token_urlsafe(48)
        with self.lock:
            updated = {**self.credentials, 'key_hash': hashlib.sha256(key.encode()).hexdigest() if key else ''}
            temporary = self.data / 'credentials.tmp'
            temporary.write_text(json.dumps(updated), encoding='utf-8')
            temporary.chmod(0o600)
            temporary.replace(self.data / 'credentials.json')
            self.credentials = updated
            with self.store._connect() as db:
                db.execute('DELETE FROM sessions')
        return key

    def run(self, server_id, action):
        server = next((s for s in self.servers if s['id'] == server_id), None)
        if server is None or action not in COMMANDS:
            raise ValueError('Unknown server or action')
        if not self.slots.acquire(blocking=False):
            raise ValueError('Server busy; retry shortly')
        try:
            command = COMMANDS[action]
            if not server.get('local'):
                command = ['ssh', '-F', str(self.data / 'ssh_config'), '-o', 'BatchMode=yes',
                           '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8',
                           '--', server['alias'], __import__('shlex').join(command)]
            result = subprocess.run(command, capture_output=True, timeout=18)
            return {'ok': result.returncode == 0, 'output': (result.stdout + result.stderr).decode('utf-8', 'replace'),
                    'checked_at': int(time.time())}
        except subprocess.TimeoutExpired:
            return {'ok': False, 'output': '连接超时', 'checked_at': int(time.time())}
        except OSError as exc:
            return {'ok': False, 'output': str(exc), 'checked_at': int(time.time())}
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    panel: Panel
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(25)

    def log_message(self, *args):
        pass

    def send(self, status, payload, cookie=None, mime='application/json; charset=utf-8'):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        for name, value in {'Content-Type': mime, 'Content-Length': str(len(body)),
                            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
                            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():
            self.send_header(name, value)
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def cookie(self, token, age=28800):
        value = f'panel_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}'
        return value + ('; Secure' if os.environ.get('PANEL_SECURE_COOKIE') == '1' else '')

    def session(self):
        try:
            morsel = SimpleCookie(self.headers.get('Cookie', '')).get('panel_session')
            return self.panel.store.get_session(morsel.value) if morsel else None
        except Exception:
            return None

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/session':
            session = self.session()
            return self.send(200, {'authenticated': bool(session), 'csrf': session['csrf_token'] if session else None})
        if path.startswith('/api/'):
            if not self.session():
                return self.send(401, {'error': '请先登录'})
            if path == '/api/servers':
                return self.send(200, {'servers': [{k: s[k] for k in ('id', 'name', 'address', 'route')} for s in self.panel.servers]})
            if path == '/api/audit':
                return self.send(200, {'events': self.panel.store.list_audit()})
            if path == '/api/security':
                return self.send(200, {'key_enabled': bool(self.panel.credentials.get('key_hash'))})
            return self.send(404, {'error': '不存在'})
        allowed = {'/': ('index.html', 'text/html; charset=utf-8'),
                   '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                   '/app.css': ('app.css', 'text/css; charset=utf-8')}
        if path not in allowed:
            return self.send(404, {'error': '不存在'})
        file, mime = allowed[path]
        self.send(200, (ROOT / 'static' / file).read_bytes(), mime=mime)

    def do_POST(self):
        try:
            origin = self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'):
                return self.send(403, {'error': '来源无效'})
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError('请求大小无效')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('请求无效')
            path = urlparse(self.path).path
            if path == '/api/login':
                if not self.panel.authenticate(str(data.get('username', '')), str(data.get('password', '')), str(data.get('key', ''))):
                    self.panel.audit('login_failed')
                    return self.send(401, {'error': '凭据无效或尝试过于频繁，请稍后重试'})
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                self.panel.store.create_session(token, csrf, 'admin', int(time.time()) + 28800, self.client_address[0], '')
                self.panel.audit('login')
                return self.send(200, {'csrf': csrf}, self.cookie(token))
            session = self.session()
            if not session:
                return self.send(401, {'error': '请先登录'})
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), session['csrf_token']):
                return self.send(403, {'error': '请刷新后重试'})
            if path == '/api/logout':
                token = SimpleCookie(self.headers['Cookie'])['panel_session'].value
                self.panel.store.delete_session(token)
                return self.send(200, {'ok': True}, self.cookie('', 0))
            if path == '/api/run':
                result = self.panel.run(data.get('server'), data.get('action'))
                self.panel.audit(data.get('action'), data.get('server'), {'ok': result['ok']})
                return self.send(200, result)
            if path == '/api/key':
                if not self.panel.authenticate('admin', str(data.get('password', '')), ''):
                    return self.send(403, {'error': '密码无效或尝试过于频繁'})
                key = self.panel.rotate_key(bool(data.get('revoke')))
                self.panel.audit('key_revoked' if not key else 'key_rotated')
                return self.send(200, {'key': key}, self.cookie('', 0))
            self.send(404, {'error': '不存在'})
        except (ValueError, TypeError) as exc:
            self.send(400, {'error': str(exc)})
        except Exception:
            self.send(500, {'error': '处理失败，请稍后重试'})


def initialize(data):
    data.mkdir(parents=True, exist_ok=True)
    target = data / 'credentials.json'
    if target.exists():
        raise SystemExit('Already initialized')
    password = secrets.token_urlsafe(24)
    key = secrets.token_urlsafe(48)
    target.write_text(json.dumps({'password_hash': hash_password(password), 'key_hash': hashlib.sha256(key.encode()).hexdigest()}))
    target.chmod(0o600)
    delivery = data / 'initial-login.json'
    delivery.write_text(json.dumps({'username': 'admin', 'password': password, 'key': key}))
    delivery.chmod(0o600)
    examples = [
        {"id": "controller", "name": "Controller", "address": "127.0.0.1",
         "route": "Local controller", "local": True}
    ]
    (data / 'servers.json').write_text(
        json.dumps(examples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print('Initialized. Credentials are in initial-login.json (private delivery file).')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['init', 'serve'])
    parser.add_argument('--data', type=Path, default=ROOT / 'data')
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9180)
    args = parser.parse_args()
    if args.command == 'init':
        initialize(args.data)
    else:
        handler = type('PanelHandler', (Handler,), {'panel': Panel(args.data)})
        ThreadingHTTPServer((args.bind, args.port), handler).serve_forever()

