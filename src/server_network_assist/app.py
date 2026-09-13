"""Server Network Assist: authenticated HTTP/WebSocket and pinned SSH routes."""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import sqlite3
import subprocess
import time
from urllib.parse import urlsplit

import asyncssh
from aiohttp import web, WSMsgType
from cryptography.fernet import Fernet
from webauthn import (generate_registration_options, verify_registration_response,
                      generate_authentication_options, verify_authentication_response)
from webauthn.helpers import options_to_json, bytes_to_base64url, base64url_to_bytes
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, ResidentKeyRequirement,
                                     UserVerificationRequirement, PublicKeyCredentialDescriptor)

from .legacy import Panel, initialize, ROOT
from .network_assist import (HELPER as NETWORK_HELPER, NetworkStore,
                             helper_command, parse_probe, probe_command,
                             detect_platform, windows_file_command, WINDOWS_HELPER)
from .auth import hash_password, token_digest, verify_password


class State:
    def __init__(self, data, key_file):
        self.legacy = Panel(Path(data))
        self.data = Path(data)
        self.db = self.data / 'console.sqlite3'
        self.cipher = Fernet(Path(key_file).read_bytes().strip())
        self.recent = {}
        self.challenges = {}
        self.tickets = {}
        self.sockets = {}
        self.codex_jobs = {}
        self.browser_hosts = set()
        self.failed = {}
        self.operations = asyncio.Semaphore(8)
        self.network_changes = asyncio.Lock()
        self.origin = os.environ.get('PANEL_ORIGIN', '').rstrip('/')
        extra_origins = [value.strip().rstrip('/') for value in
                         os.environ.get('PANEL_ALLOWED_ORIGINS', '').split(',') if value.strip()]
        self.allowed_origins = tuple(dict.fromkeys(value for value in [self.origin, *extra_origins] if value))
        for value in self.allowed_origins:
            parsed = urlsplit(value)
            if (parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or
                    parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
                raise ValueError('PANEL_ORIGIN/PANEL_ALLOWED_ORIGINS 必须是完整且不含路径的 HTTP(S) 来源')
        self.base_path = os.environ.get('PANEL_BASE_PATH', '').strip()
        if self.base_path and (not self.base_path.startswith('/') or self.base_path.endswith('/') or
                               not re.fullmatch(r'/[a-zA-Z0-9/_-]+', self.base_path)):
            raise ValueError('PANEL_BASE_PATH 必须是类似 /network-assist 的安全路径')
        self.secure = self.origin.startswith('https://')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS hosts (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS vault (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    kind TEXT NOT NULL, secret BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS passkeys (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    public_key TEXT NOT NULL, sign_count INTEGER NOT NULL, created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS codex_sessions (
                    id TEXT PRIMARY KEY, host_id TEXT NOT NULL, title TEXT NOT NULL,
                    workspace TEXT NOT NULL, sandbox TEXT NOT NULL,
                    remote_thread_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS codex_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
                    created_at INTEGER NOT NULL);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(codex_sessions)')}
            for name, definition in (
                    ('model', "TEXT NOT NULL DEFAULT ''"),
                    ('reasoning_effort', "TEXT NOT NULL DEFAULT ''"),
                    ('service_tier', "TEXT NOT NULL DEFAULT ''")):
                if name not in columns:
                    db.execute(f'ALTER TABLE codex_sessions ADD COLUMN {name} {definition}')
            db.execute("UPDATE codex_sessions SET status='error', last_error='管理台重启，上一条消息已中止' WHERE status='running'")
            db.execute("UPDATE codex_messages SET status='error', content='管理台重启，上一条消息已中止' WHERE status='running'")
        self.network = NetworkStore(self)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.db)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def audit(self, action, target=''):
        self.legacy.audit(action, target)

    def hosts(self):
        with self.connect() as db:
            return [json.loads(r['body']) for r in db.execute('SELECT body FROM hosts')]

    def host(self, id):
        return next((h for h in self.hosts() if h['id'] == id), None)

    def route(self, id, hosts=None):
        hosts = {h['id']: h for h in (self.hosts() if hosts is None else hosts)}
        route = []
        while id:
            if any(h['id'] == id for h in route) or len(route) >= 5:
                raise ValueError('跳板路径存在循环或超过 5 层')
            h = hosts.get(id)
            if not h:
                raise ValueError('主机或跳板不存在')
            route.append(h)
            id = h['jump_id']
        return list(reversed(route))

    def save_host(self, p):
        h = {k: str(p.get(k, '')).strip() for k in
             ('id', 'name', 'address', 'username', 'credential_id', 'jump_id', 'host_key', 'group')}
        h['id'] = h['id'] or secrets.token_hex(8)
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', h['id']):
            raise ValueError('主机标识无效')
        if not h['name'] or len(h['name']) > 160 or len(h['group']) > 80:
            raise ValueError('请填写主机名称（最多 160 字）')
        address = h['address']
        try:
            ipaddress.ip_address(address)
        except ValueError:
            if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?', address):
                raise ValueError('主机地址无效')
        if not re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,63}', h['username']):
            raise ValueError('SSH 账号无效')
        h['port'] = int(p.get('port', 22))
        if not 1 <= h['port'] <= 65535:
            raise ValueError('端口必须为 1–65535')
        h['favorite'] = bool(p.get('favorite'))
        h['terminal_enabled'] = bool(p.get('terminal_enabled', True))
        h['codex_enabled'] = bool(p.get('codex_enabled', True))
        h['browser_enabled'] = bool(p.get('browser_enabled', True))
        h['codex_workspace'] = str(p.get('codex_workspace', '')).strip()
        if len(h['codex_workspace']) > 1024 or any(value in h['codex_workspace'] for value in ('\x00', '\r', '\n')):
            raise ValueError('Codex 工作目录无效')
        if h['host_key']:
            h['host_key'] = asyncssh.import_public_key(h['host_key']).export_public_key().decode().strip()
        if not any(c['id'] == h['credential_id'] for c in self.credentials()):
            raise ValueError('请选择有效登录凭据')
        hosts = [v for v in self.hosts() if v['id'] != h['id']] + [h]
        for v in hosts:
            self.route(v['id'], hosts)
        previous = self.host(h['id'])
        if previous and hasattr(self, 'network'):
            active = [p for p in self.network.profiles() if (p['state'] not in ('disabled', 'error') or p.get('cleanup_pending'))
                      and (h['id'] == p['gateway_id'] or h['id'] in p['client_ids'])]
            changed = any(previous.get(k) != h.get(k) for k in
                          ('address', 'port', 'username', 'credential_id', 'jump_id', 'host_key'))
            if active and changed:
                raise ValueError('该主机正在参与网络借助，请先断开对应方案再修改连接配置')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO hosts VALUES (?,?)', (h['id'], json.dumps(h)))
        self.audit('host_saved', h['name'])
        return h

    def codex_sessions(self, host_id=''):
        query = 'SELECT * FROM codex_sessions'
        parameters = ()
        if host_id:
            query += ' WHERE host_id=?'
            parameters = (host_id,)
        query += ' ORDER BY updated_at DESC'
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, parameters)]

    def codex_session(self, session_id, with_messages=True):
        with self.connect() as db:
            row = db.execute('SELECT * FROM codex_sessions WHERE id=?', (session_id,)).fetchone()
            if not row:
                return None
            value = dict(row)
            if with_messages:
                value['messages'] = [dict(item) for item in db.execute(
                    'SELECT id,role,content,status,created_at FROM codex_messages WHERE session_id=? ORDER BY id',
                    (session_id,))]
            return value

    def create_codex_session(self, p):
        host = self.host(str(p.get('host_id', '')))
        if not host or not host.get('codex_enabled', True):
            raise ValueError('该主机未启用 Codex 对话')
        title = str(p.get('title', '')).strip()[:120] or f"{host['name']} 对话"
        workspace = str(p.get('workspace', host.get('codex_workspace', ''))).strip()
        if len(workspace) > 1024 or any(value in workspace for value in ('\x00', '\r', '\n')):
            raise ValueError('Codex 工作目录无效')
        sandbox = str(p.get('sandbox', 'workspace-write'))
        if sandbox not in ('read-only', 'workspace-write'):
            raise ValueError('Codex 沙箱模式无效')
        model = str(p.get('model', '')).strip()
        effort = str(p.get('reasoning_effort', '')).strip()
        service_tier = str(p.get('service_tier', '')).strip()
        if len(model) > 120 or not re.fullmatch(r'[a-zA-Z0-9._-]*', model):
            raise ValueError('Codex 模型名称无效')
        if effort not in ('', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
            raise ValueError('Codex 推理强度无效')
        if service_tier not in ('', 'priority'):
            raise ValueError('Codex 速度模式无效')
        remote_thread_id = str(p.get('remote_thread_id', '')).strip()
        if remote_thread_id and not re.fullmatch(r'[a-zA-Z0-9._-]{1,160}', remote_thread_id):
            raise ValueError('Codex 远端会话标识无效')
        now, session_id = int(time.time()), secrets.token_hex(16)
        with self.connect() as db:
            db.execute('''INSERT INTO codex_sessions
                (id,host_id,title,workspace,sandbox,remote_thread_id,status,last_error,
                 created_at,updated_at,model,reasoning_effort,service_tier)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (session_id, host['id'], title, workspace, sandbox, remote_thread_id,
                 'idle', '', now, now, model, effort, service_tier))
        self.audit('codex_session_created', host['name'])
        return self.codex_session(session_id)

    def delete_codex_session(self, session_id):
        session = self.codex_session(session_id, False)
        if not session:
            raise ValueError('Codex 会话不存在')
        if session['status'] == 'running' or session_id in self.codex_jobs:
            raise ValueError('请先停止正在运行的回复')
        with self.connect() as db:
            db.execute('DELETE FROM codex_messages WHERE session_id=?', (session_id,))
            db.execute('DELETE FROM codex_sessions WHERE id=?', (session_id,))
        self.audit('codex_session_deleted', session['host_id'])

    def begin_codex_turn(self, session_id, prompt):
        session = self.codex_session(session_id, False)
        if not session:
            raise ValueError('Codex 会话不存在')
        if session['status'] == 'running' or session_id in self.codex_jobs:
            raise ValueError('当前会话正在生成回复')
        content = str(prompt).strip()
        if not content or len(content) > 32000:
            raise ValueError('消息长度需为 1–32000 个字符')
        now = int(time.time())
        with self.connect() as db:
            db.execute('INSERT INTO codex_messages(session_id,role,content,status,created_at) VALUES (?,?,?,?,?)',
                       (session_id, 'user', content, 'done', now))
            cursor = db.execute('INSERT INTO codex_messages(session_id,role,content,status,created_at) VALUES (?,?,?,?,?)',
                                (session_id, 'assistant', '', 'running', now))
            db.execute("UPDATE codex_sessions SET status='running',last_error='',updated_at=? WHERE id=?",
                       (now, session_id))
            message_id = cursor.lastrowid
        return session, message_id, content

    def finish_codex_turn(self, session_id, message_id, content, remote_thread_id='', error=''):
        now = int(time.time())
        status = 'error' if error else 'done'
        with self.connect() as db:
            db.execute('UPDATE codex_messages SET content=?,status=? WHERE id=? AND session_id=?',
                       ((content or error)[:200000], status, message_id, session_id))
            if remote_thread_id:
                db.execute('UPDATE codex_sessions SET remote_thread_id=? WHERE id=?',
                           (remote_thread_id, session_id))
            db.execute('UPDATE codex_sessions SET status=?,last_error=?,updated_at=? WHERE id=?',
                       (status, error[:2000], now, session_id))

    def credentials(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,name,kind FROM vault')]

    def secret(self, id):
        with self.connect() as db:
            r = db.execute('SELECT * FROM vault WHERE id=?', (id,)).fetchone()
        if not r:
            raise ValueError('登录凭据不存在')
        return r['kind'], json.loads(self.cipher.decrypt(r['secret']))

    def save_credential(self, p):
        name, kind = str(p.get('name', '')).strip(), p.get('kind')
        if not name or len(name) > 120 or kind not in ('key', 'password'):
            raise ValueError('凭据名称或类型无效')
        secret = str(p.get('secret', ''))
        phrase = str(p.get('passphrase', ''))
        if not secret or len(secret) > 24000 or len(phrase) > 1024:
            raise ValueError('请提供有效的私钥或密码')
        if kind == 'key':
            asyncssh.import_private_key(secret, passphrase=phrase or None)
        id = secrets.token_hex(8)
        encrypted = self.cipher.encrypt(json.dumps({'value': secret, 'passphrase': phrase}).encode())
        with self.connect() as db:
            db.execute('INSERT INTO vault VALUES (?,?,?,?)', (id, name, kind, encrypted))
        self.audit('credential_added', name)
        return id

    def check_rate(self, key):
        now = time.time()
        self.failed = {k: [t for t in v if now - t < 900] for k, v in self.failed.items() if any(now-t < 900 for t in v)}
        if len(self.failed.get(key, [])) >= 8:
            raise web.HTTPTooManyRequests(text='尝试过于频繁，请 15 分钟后重试')

    def fail(self, key):
        if len(self.failed) < 2048 or key in self.failed:
            self.failed.setdefault(key, []).append(time.time())

    def passkey_ready(self):
        return bool(self.origin and (self.secure or urlsplit(self.origin).hostname in ('localhost', '127.0.0.1')))

    def request_origin(self, request):
        origin = request.headers.get('Origin', '').rstrip('/')
        if origin:
            return origin
        forwarded_scheme = request.headers.get('X-Forwarded-Proto', '').split(',', 1)[0].strip()
        forwarded_host = request.headers.get('X-Forwarded-Host', '').split(',', 1)[0].strip()
        if forwarded_scheme in ('http', 'https') and forwarded_host:
            forwarded = f'{forwarded_scheme}://{forwarded_host}'.rstrip('/')
            if forwarded in self.allowed_origins:
                return forwarded
        return f'{request.scheme}://{request.host}'

    def origin_allowed(self, request):
        origin = request.headers.get('Origin', '').rstrip('/')
        if not origin:
            return True
        expected = f'{request.scheme}://{request.host}'
        return origin in self.allowed_origins if self.allowed_origins else origin == expected

    def cookie_secure(self, request):
        return self.request_origin(request).startswith('https://')

    def passkey_allowed(self, request):
        return self.passkey_ready() and self.request_origin(request) == self.origin


STATE_KEY = web.AppKey("state", State)
SESSION_KEY = web.RequestKey("session", dict)


@contextlib.asynccontextmanager
async def ssh_route(state, id, stop_before=False):
    route = state.route(id)
    connections = []
    try:
        for h in (route[:-1] if stop_before else route):
            if not h['host_key']:
                raise ValueError(f"{h['name']}：请先核对并保存 SSH 主机指纹")
            kind, secret = state.secret(h['credential_id'])
            options = {'client_keys': [], 'agent_path': None, 'config': [],
                       'known_hosts': ([asyncssh.import_public_key(h['host_key'])], [], []),
                       'username': h['username'], 'port': h['port'], 'connect_timeout': 12,
                       'login_timeout': 15, 'keepalive_interval': 20, 'keepalive_count_max': 3}
            if kind == 'key':
                options['client_keys'] = [asyncssh.import_private_key(secret['value'], passphrase=secret['passphrase'] or None)]
            else:
                options['password'] = secret['value']
            try:
                conn = await asyncssh.connect(h['address'], tunnel=connections[-1] if connections else None, **options)
            except asyncssh.HostKeyNotVerifiable as e:
                raise ValueError(f"{h['name']}：主机指纹不匹配，连接已停止") from e
            except asyncssh.PermissionDenied as e:
                raise ValueError(f"{h['name']}：SSH 认证失败，请检查账号和凭据") from e
            except (OSError, asyncio.TimeoutError, asyncssh.Error) as e:
                raise ValueError(f"{h['name']}：连接不可达、超时或 SSH 协议错误（{type(e).__name__}）") from e
            connections.append(conn)
        yield connections[-1] if connections else None
    finally:
        for conn in reversed(connections):
            conn.close()
        for conn in reversed(connections):
            with contextlib.suppress(Exception):
                await conn.wait_closed()


def _remote_json(result):
    for line in reversed(result.stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            if not value.get('ok', False):
                raise ValueError(value.get('error', '远端网络辅助程序执行失败'))
            return value
    detail = (result.stderr or result.stdout or f'exit {result.exit_status}').strip()
    raise ValueError(('远端网络辅助程序不可用：' + detail)[:2000])


async def network_probe_host(state, host_id):
    host = state.host(host_id)
    if not host:
        raise ValueError('主机不存在')
    try:
        async with state.operations, ssh_route(state, host_id) as connection:
            platform = await detect_platform(connection)
            if platform == 'windows':
                async with connection.start_sftp_client() as sftp:
                    home = await sftp.realpath('.')
                    temporary = home.rstrip('/') + '/sna-probe-' + secrets.token_hex(8) + '.ps1'
                    try:
                        await sftp.put(str(ROOT / 'windows_probe.ps1'), temporary)
                        result = await connection.run(windows_file_command(temporary), timeout=45, check=False)
                    finally:
                        with contextlib.suppress(Exception):
                            await sftp.remove(temporary)
            else:
                result = await connection.run(probe_command(), timeout=20, check=False)
            try:
                codex = await connection.run(_codex_prefix(platform) + 'codex --version', timeout=12, check=False)
                codex_version = (codex.stdout or '').strip().splitlines()
                codex_ready = codex.exit_status == 0
            except (OSError, asyncio.TimeoutError, asyncssh.Error):
                codex_version, codex_ready = [], False
            try:
                if platform == 'windows':
                    browser_command = ('powershell.exe -NoProfile -NonInteractive -Command '
                        '"$p=@(\'C:/Program Files/Google/Chrome/Application/chrome.exe\','
                        '\'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe\','
                        '\'C:/Program Files/Microsoft/Edge/Application/msedge.exe\');'
                        '$x=$p|Where-Object {Test-Path -LiteralPath $_}|Select-Object -First 1;'
                        'if($x){[IO.Path]::GetFileName($x)}"')
                else:
                    browser_command = "command -v google-chrome || command -v chromium || command -v chromium-browser || command -v microsoft-edge"
                browser_result = await connection.run(browser_command, timeout=12, check=False)
                browser_values = (browser_result.stdout or '').strip().splitlines()
                browser_ready = browser_result.exit_status == 0 and bool(browser_values)
            except (OSError, asyncio.TimeoutError, asyncssh.Error):
                browser_values, browser_ready = [], False
        value = parse_probe(result.stdout, result.exit_status)
        error = (result.stderr or '远端探测失败')[:1000] if result.exit_status else ''
        value.update(id=host_id, name=host['name'], address=host['address'], error=error,
                     codex=codex_ready,
                     codex_version=codex_version[-1][:120] if codex_version else '',
                     browser=browser_ready,
                     browser_name=browser_values[-1][:160] if browser_values else '')
        return value
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        return {'id': host_id, 'name': host['name'], 'address': host['address'],
                'ssh': False, 'dns': False, 'internet': False, 'helper': False,
                'hostname': '', 'os': '', 'default_route': '', 'http_code': '000',
                'assist': [], 'codex': False, 'codex_version': '', 'browser': False,
                'browser_name': '', 'error': message[:1000]}


def _codex_prefix(platform):
    if platform == 'windows':
        return ''
    return 'export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"; '


def _codex_command(platform, session):
    if session['remote_thread_id']:
        arguments = ['codex', 'exec', 'resume', '--json', '--skip-git-repo-check',
                     session['remote_thread_id'], '-']
    else:
        arguments = ['codex', 'exec', '--json', '--color', 'never', '--skip-git-repo-check',
                     '--sandbox', session['sandbox']]
        if session['workspace']:
            arguments.extend(['--cd', session['workspace']])
        arguments.append('-')
    if session.get('model'):
        arguments[2:2] = ['--model', session['model']]
    if session.get('reasoning_effort'):
        arguments[2:2] = ['--config', f'model_reasoning_effort="{session["reasoning_effort"]}"']
    if session.get('service_tier'):
        arguments[2:2] = ['--config', f'service_tier="{session["service_tier"]}"']
    if platform == 'windows':
        if any(any(char in value for char in '&|<>^%\r\n') for value in arguments):
            raise ValueError('Windows Codex 参数包含不安全字符')
        return subprocess.list2cmdline(arguments)
    return _codex_prefix(platform) + shlex.join(arguments)


async def _codex_app_request(connection, platform, method, params, timeout=45):
    """Issue one protocol request against the remote CLI's stdio app-server."""
    process = await connection.create_process(
        _codex_prefix(platform) + 'codex app-server --stdio', encoding='utf-8')

    async def exchange(request_id, request_method, request_params):
        value = {'id': request_id, 'method': request_method, 'params': request_params}
        process.stdin.write(json.dumps(value, ensure_ascii=False) + '\n')
        await process.stdin.drain()
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout)
            if not line:
                detail = (await process.stderr.read()).strip()
                raise ValueError((detail or '远端 Codex app-server 提前退出')[:2000])
            event = json.loads(line)
            if event.get('id') == request_id:
                if 'error' in event:
                    error = event['error']
                    raise ValueError(str(error.get('message', error))[:2000] if isinstance(error, dict)
                                     else str(error)[:2000])
                return event.get('result', {})

    try:
        await exchange(1, 'initialize', {'clientInfo': {
            'name': 'server-network-assist', 'title': 'Server Network Assist', 'version': '0.4.2'},
            'capabilities': {'experimentalApi': True}})
        return await exchange(2, method, params)
    finally:
        process.terminate()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait_closed(), 3)


async def codex_remote_catalog(state, host_id):
    host = state.host(host_id)
    if not host or not host.get('codex_enabled', True):
        raise ValueError('该主机未启用 Codex 对话')
    async with state.operations, ssh_route(state, host_id) as connection:
        platform = await detect_platform(connection)
        models = await _codex_app_request(connection, platform, 'model/list', {'limit': 100})
        threads = await _codex_app_request(connection, platform, 'thread/list', {
            'limit': 100, 'archived': False, 'sourceKinds': [
                'cli', 'vscode', 'exec', 'appServer', 'subAgent', 'subAgentReview',
                'subAgentCompact', 'subAgentThreadSpawn', 'subAgentOther', 'unknown'],
            'sortKey': 'updated_at', 'sortDirection': 'desc'})
    data = threads.get('data', [])
    projects = {}
    for thread in data:
        cwd = str(thread.get('cwd') or '').strip()
        if cwd:
            project = projects.setdefault(cwd, {'path': cwd, 'count': 0, 'updated_at': 0})
            project['count'] += 1
            project['updated_at'] = max(project['updated_at'], int(thread.get('updatedAt') or 0))
    return {'models': models.get('data', []), 'threads': data,
            'projects': sorted(projects.values(), key=lambda item: item['updated_at'], reverse=True)}


async def codex_remote_thread(state, host_id, thread_id):
    if not re.fullmatch(r'[a-zA-Z0-9._-]{1,160}', thread_id):
        raise ValueError('Codex 远端会话标识无效')
    host = state.host(host_id)
    if not host or not host.get('codex_enabled', True):
        raise ValueError('该主机未启用 Codex 对话')
    async with state.operations, ssh_route(state, host_id) as connection:
        platform = await detect_platform(connection)
        return await _codex_app_request(connection, platform, 'thread/read', {
            'threadId': thread_id, 'includeTurns': True}, timeout=60)


def _codex_result(stdout, stderr, exit_status):
    answer, thread_id, errors = '', '', []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get('type') == 'thread.started':
            thread_id = str(event.get('thread_id', ''))
        item = event.get('item') if isinstance(event.get('item'), dict) else {}
        if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
            answer = str(item.get('text', ''))
        if event.get('type') in ('error', 'turn.failed'):
            errors.append(str(event.get('message') or event.get('error') or 'Codex 执行失败'))
    if exit_status or not answer:
        detail = '\n'.join(errors) or stderr.strip() or 'Codex 没有返回可显示的回复'
        raise ValueError(detail[:2000])
    return answer[:200000], thread_id


async def run_codex_turn(state, session_id, message_id, prompt):
    try:
        session = state.codex_session(session_id, False)
        if not session:
            return
        host = state.host(session['host_id'])
        if not host or not host.get('codex_enabled', True):
            raise ValueError('主机已移除或 Codex 对话已禁用')
        async with state.operations, ssh_route(state, host['id']) as connection:
            platform = await detect_platform(connection)
            command = _codex_command(platform, session)
            result = await connection.run(command, input=prompt + '\n', timeout=900, check=False)
        answer, thread_id = _codex_result(result.stdout, result.stderr, result.exit_status)
        state.finish_codex_turn(session_id, message_id, answer, thread_id)
        state.audit('codex_turn_completed', host['name'])
    except asyncio.CancelledError:
        state.finish_codex_turn(session_id, message_id, '', error='回复已由管理员停止')
        raise
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else f'Codex 连接失败（{type(exc).__name__}）'
        state.finish_codex_turn(session_id, message_id, '', error=message)
    finally:
        state.codex_jobs.pop(session_id, None)


async def network_install_helper(state, host_id):
    host = state.host(host_id)
    if not host:
        raise ValueError('主机不存在')
    source = ROOT / 'network_assist_helper.py'
    temporary = f'/tmp/server-network-assist-{secrets.token_hex(8)}'
    async with state.operations, ssh_route(state, host_id) as connection:
        platform = await detect_platform(connection)
        if platform == 'windows':
            # A short native command creates the destination; never invoke sudo/WSL.
            result = await connection.run(
                'powershell.exe -NoProfile -NonInteractive -Command "[IO.Directory]::CreateDirectory(\'C:/ProgramData/ServerNetworkAssist\') | Out-Null"',
                timeout=15, check=False)
            if result.exit_status:
                raise ValueError('Windows 安装需要已提权的管理员 SSH 账号')
            result = await connection.run(
                'icacls.exe C:\\ProgramData\\ServerNetworkAssist /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F"',
                timeout=15, check=False)
            if result.exit_status:
                raise ValueError('Windows 辅助程序目录权限设置失败')
            async with connection.start_sftp_client() as sftp:
                await sftp.put(str(ROOT / 'windows_helper.ps1'), WINDOWS_HELPER)
            result = await connection.run(helper_command('bootstrap', platform='windows'), timeout=45, check=False)
            value = _remote_json(result)
            state.audit('network_helper_installed', host['name'])
            return value
        try:
            sftp = await connection.start_sftp_client()
            await sftp.put(str(source), temporary)
            install = shlex.join(['sudo', '-n', 'install', '-o', 'root', '-g', 'root',
                                  '-m', '0755', '--', temporary, NETWORK_HELPER])
            result = await connection.run(install, timeout=30, check=False)
            if result.exit_status:
                detail = (result.stderr or result.stdout).strip()
                raise ValueError(('安装失败；需要 root 或无密码 sudo 权限。' + (' ' + detail if detail else ''))[:2000])
            result = await connection.run(helper_command('bootstrap'), timeout=45, check=False)
            value = _remote_json(result)
        finally:
            await connection.run(shlex.join(['rm', '-f', '--', temporary]), timeout=10, check=False)
    state.audit('network_helper_installed', host['name'])
    return value


async def _network_remote(state, host_id, action, payload=None, *args, timeout=60):
    async with state.operations, ssh_route(state, host_id) as connection:
        platform = await detect_platform(connection)
        result = await connection.run(helper_command(action, payload, *args, platform=platform), timeout=timeout, check=False)
    return _remote_json(result)


async def network_enable_profile(state, profile_id):
    profile = state.network.get(profile_id)
    if not profile:
        raise ValueError('借网方案不存在')
    if profile.get('cleanup_pending'):
        raise ValueError('上次回退尚未完成，请先重试断开并恢复原网络')
    for other in state.network.profiles():
        if other['id'] == profile_id or other['state'] in ('disabled', 'error'):
            continue
        client_overlap = set(profile['client_ids']) & set(other['client_ids'])
        role_overlap = profile['gateway_id'] in other['client_ids'] or other['gateway_id'] in profile['client_ids']
        if client_overlap or role_overlap:
            raise ValueError('所选主机正在参与另一个活动借网方案，请先断开该方案')
    state.network.set_state(profile_id, 'enabling')
    enabled_clients = []
    gateway_enabled = False
    try:
        gateway_probe = await network_probe_host(state, profile['gateway_id'])
        if not gateway_probe.get('ssh'):
            raise ValueError('出口机 SSH 不可达')
        if profile.get('proxy_mode', 'direct') == 'direct' and not gateway_probe['internet']:
            raise ValueError('所选出口机当前未通过公网探测，已拒绝切换客户端路由')
        if not gateway_probe.get('gateway_supported', gateway_probe.get('os') == 'Linux'):
            raise ValueError('出口机缺少转发能力：Ubuntu/Linux 需要 WireGuard，Windows 需要原生 WireGuard 和 WinNAT')
        node_ids = [profile['gateway_id'], *profile['client_ids']]
        probes = await asyncio.gather(*(network_probe_host(state, value) for value in node_ids))
        missing = [p['name'] for p in probes if not p['helper']]
        if missing:
            raise ValueError('请先安装网络辅助程序：' + '、'.join(missing))
        prepared = await asyncio.gather(*(_network_remote(state, value, 'prepare', None, profile_id)
                                         for value in node_ids))
        public_keys = {node: result['public_key'] for node, result in zip(node_ids, prepared)}
        gateway_payload, client_payloads = state.network.runtime_payloads(profile, public_keys)
        await _network_remote(state, profile['gateway_id'], 'configure', gateway_payload)
        await asyncio.gather(*(_network_remote(state, node, 'configure', client_payloads[node])
                               for node in profile['client_ids']))
        gateway_enabled = True
        await _network_remote(state, profile['gateway_id'], 'enable', None, profile_id, '0')
        for node in profile['client_ids']:
            enabled_clients.append(node)
            await _network_remote(state, node, 'enable', None, profile_id, '120')
            check = await network_probe_host(state, node)
            if not check['ssh'] or (profile.get('proxy_mode', 'direct') == 'direct' and not check['internet']):
                raise ValueError(f"{state.host(node)['name']} 切换后未通过 SSH 和公网复检，正在回退")
            await _network_remote(state, node, 'verify', None, profile_id)
            await _network_remote(state, node, 'confirm', None, profile_id)
        result = state.network.set_state(profile_id, 'enabled')
        state.audit('network_assist_enabled', profile['name'])
        return result
    except Exception as exc:
        cleanup = await asyncio.gather(*(_network_remote(state, node, 'disable', None, profile_id)
                               for node in enabled_clients), return_exceptions=True)
        if gateway_enabled:
            cleanup += await asyncio.gather(_network_remote(state, profile['gateway_id'], 'disable', None, profile_id),
                                           return_exceptions=True)
        failures = [str(v) for v in cleanup if isinstance(v, Exception)]
        detail = str(exc) + ('；回退尚未完成，请重试断开：' + '；'.join(failures) if failures else '')
        failed = state.network.set_state(profile_id, 'error', detail)
        failed['cleanup_pending'] = bool(failures)
        state.network.put(failed)
        raise


async def network_disable_profile(state, profile_id):
    profile = state.network.get(profile_id)
    if not profile:
        raise ValueError('借网方案不存在')
    state.network.set_state(profile_id, 'disabling')
    results = await asyncio.gather(*(_network_remote(state, node, 'disable', None, profile_id)
                                     for node in profile['client_ids']), return_exceptions=True)
    gateway = await asyncio.gather(_network_remote(state, profile['gateway_id'], 'disable', None, profile_id),
                                   return_exceptions=True)
    failures = [str(v) for v in [*results, *gateway] if isinstance(v, Exception)]
    if failures:
        failed = state.network.set_state(profile_id, 'error', '；'.join(failures))
        failed['cleanup_pending'] = True
        state.network.put(failed)
        raise ValueError('部分主机未完成回退：' + '；'.join(failures))
    result = state.network.set_state(profile_id, 'disabled')
    result['cleanup_pending'] = False
    state.network.put(result)
    state.audit('network_assist_disabled', profile['name'])
    return result


def current(state, request):
    return state.legacy.store.get_session(request.cookies.get('panel_session', ''))


def fresh(state, session):
    if state.recent.get(session['token_hash'], 0) < time.time() - 300:
        raise web.HTTPForbidden(text='请先在设置中验证管理员密码（验证有效期 5 分钟）')


def new_session(state, request, p):
    days = int(p.get('remember', 0))
    if days not in (0, 7, 30):
        raise ValueError('登录时长无效')
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    age = days * 86400 if days else 28800
    name = str(p.get('device', '')).strip()[:80] or request.headers.get('User-Agent', '浏览器')[:160]
    state.legacy.store.create_session(token, csrf, 'admin', int(time.time()) + age, request.remote or '', name)
    state.recent = {k: v for k, v in state.recent.items() if v >= time.time()-300}
    state.recent[token_digest(token)] = time.time()
    response = web.json_response({'csrf': csrf})
    response.set_cookie('panel_session', token, max_age=age, httponly=True,
                        secure=state.cookie_secure(request), samesite='Strict', path='/')
    state.audit('login', name)
    return response


PUBLIC = {'/api/session', '/api/login', '/api/passkey/options', '/api/passkey/login'}


def create_app(data, key_file):
    state = State(data, key_file)

    @web.middleware
    async def guard(request, handler):
        try:
            if request.method == 'POST' or request.path == '/ws':
                origin = request.headers.get('Origin')
                if not state.origin_allowed(request) or (request.path == '/ws' and not origin):
                    raise web.HTTPForbidden(text='来源无效')
            if request.path.startswith('/api/') and request.path not in PUBLIC:
                session = current(state, request)
                if not session:
                    raise web.HTTPUnauthorized(text='请先登录')
                request[SESSION_KEY] = session
                if request.method == 'POST' and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf_token']):
                    raise web.HTTPForbidden(text='登录验证已更新，请刷新页面')
            response = await handler(request)
        except web.HTTPException as e:
            response = web.json_response({'error': e.text}, status=e.status)
        except (ValueError, KeyError, TypeError, asyncssh.KeyImportError) as e:
            response = web.json_response({'error': str(e) or '请求无效'}, status=400)
        except Exception as e:
            print('Request failed:', type(e).__name__, flush=True)
            response = web.json_response({'error': '处理失败，请稍后重试'}, status=500)
        return response

    app = web.Application(middlewares=[guard], client_max_size=32768)
    app[STATE_KEY] = state

    async def headers(request, response):
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
    app.on_response_prepare.append(headers)

    async def get(request):
        path = request.path
        session = request.get(SESSION_KEY)
        if path == '/api/session':
            s = current(state, request)
            return web.json_response({'authenticated': bool(s), 'csrf': s['csrf_token'] if s else None,
                'passkeys': state.passkey_allowed(request), 'secure': state.cookie_secure(request), 'version': '2.0'})
        if path == '/api/hosts':
            return web.json_response({'hosts': state.hosts()})
        if path == '/api/credentials':
            return web.json_response({'credentials': state.credentials()})
        if path == '/api/network':
            return web.json_response({'profiles': state.network.profiles()})
        if path == '/api/codex/sessions':
            return web.json_response({'sessions': state.codex_sessions(request.query.get('host_id', ''))})
        if path == '/api/codex/session':
            value = state.codex_session(request.query.get('id', ''))
            if not value:
                raise web.HTTPNotFound(text='Codex 会话不存在')
            return web.json_response({'session': value})
        if path == '/api/codex/remote/catalog':
            return web.json_response(await codex_remote_catalog(state, request.query.get('host_id', '')))
        if path == '/api/codex/remote/thread':
            value = await codex_remote_thread(state, request.query.get('host_id', ''),
                                              request.query.get('thread_id', ''))
            return web.json_response(value)
        if path == '/api/security':
            with state.legacy.store._connect() as db:
                rows = db.execute('SELECT token_hash,user_agent,remote_ip,created_at,expires_at FROM sessions WHERE expires_at>=? ORDER BY created_at DESC', (int(time.time()),)).fetchall()
            with state.connect() as db:
                keys = [dict(r) for r in db.execute('SELECT id,name,created_at FROM passkeys')]
            return web.json_response({'devices': [{**dict(r), 'current': r['token_hash'] == session['token_hash']} for r in rows],
                'passkeys': keys, 'key_enabled': bool(state.legacy.credentials.get('key_hash')),
                'verified': state.recent.get(session['token_hash'], 0) >= time.time()-300})
        if path == '/api/audit':
            return web.json_response({'events': state.legacy.store.list_audit(100)})
        if path == '/api/servers':
            return web.json_response({'servers': [{k: s[k] for k in ('id', 'name', 'address', 'route')} for s in state.legacy.servers]})
        raise web.HTTPNotFound(text='不存在')

    async def post(request):
        p = await request.json()
        if not isinstance(p, dict):
            raise ValueError('请求必须为对象')
        path, session = request.path, request.get(SESSION_KEY)
        ip = request.remote or ''
        if path == '/api/login':
            state.check_rate(ip)
            # Existing password/key hashes remain valid during migration.
            c = state.legacy.credentials
            password_ok = await asyncio.to_thread(verify_password, str(p.get('password', '')), c['password_hash'])
            key_ok = bool(c.get('key_hash')) and hmac.compare_digest(hashlib.sha256(str(p.get('key', '')).encode()).hexdigest(), c['key_hash'])
            if p.get('username') != 'admin' or not (password_ok or key_ok):
                state.fail(ip)
                state.audit('login_failed')
                raise web.HTTPUnauthorized(text='账号或凭据不正确')
            response = new_session(state, request, p)
            state.failed.pop(ip, None)
            return response
        if path.startswith('/api/passkey/'):
            return await passkey(request, p)
        if path == '/api/logout':
            state.legacy.store.delete_session(request.cookies['panel_session'])
            state.recent.pop(session['token_hash'], None)
            response = web.json_response({'ok': True})
            response.del_cookie('panel_session', path='/')
            return response
        if path == '/api/reauth':
            state.check_rate(ip)
            if not await asyncio.to_thread(verify_password, str(p.get('password', '')), state.legacy.credentials['password_hash']):
                state.fail(ip)
                raise web.HTTPForbidden(text='管理员密码不正确')
            state.recent[session['token_hash']] = time.time()
            return web.json_response({'ok': True})
        if path == '/api/run':
            result = await asyncio.to_thread(state.legacy.run, p.get('server'), p.get('action'))
            state.audit(p.get('action'), p.get('server'))
            return web.json_response(result)
        if path == '/api/host/test':
            id = str(p.get('id', ''))
            async with state.operations, ssh_route(state, id) as conn:
                result = await conn.run('hostname; whoami', timeout=10, check=False)
            state.audit('host_test', id)
            return web.json_response({'ok': result.exit_status == 0, 'output': (result.stdout+result.stderr)[:16384],
                'route': [h['name'] for h in state.route(id)]})
        if path == '/api/terminal/ticket':
            h = state.host(p.get('id'))
            if not h or not h['terminal_enabled']:
                raise ValueError('该主机未启用终端')
            if len(state.sockets) >= 12:
                raise ValueError('终端数量已达上限，请先断开不用的终端')
            ticket = secrets.token_urlsafe(32)
            now = time.time()
            state.tickets = {k: v for k,v in state.tickets.items() if v['expires'] > now}
            if len(state.tickets) >= 100:
                raise web.HTTPTooManyRequests(text='连接请求过多')
            state.tickets[ticket] = {'session': session['token_hash'], 'id': h['id'], 'expires': now+30,
                                     'kind': 'terminal',
                                     'persistent': bool(p.get('persistent')), 'route': state.route(h['id'])}
            return web.json_response({'ticket': ticket})
        if path == '/api/browser/ticket':
            h = state.host(p.get('id'))
            if not h or not h.get('browser_enabled', True):
                raise ValueError('该主机未启用浏览器标签')
            url = str(p.get('url', 'https://chatgpt.com/')).strip()
            parsed = urlsplit(url)
            if (parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or
                    parsed.password or len(url) > 4096):
                raise ValueError('浏览器地址必须是普通 HTTP(S) 地址')
            ticket = secrets.token_urlsafe(32)
            now = time.time()
            state.tickets = {k: v for k, v in state.tickets.items() if v['expires'] > now}
            if len(state.tickets) >= 100:
                raise web.HTTPTooManyRequests(text='连接请求过多')
            state.tickets[ticket] = {'session': session['token_hash'], 'id': h['id'],
                                     'expires': now + 30, 'kind': 'browser', 'url': url,
                                     'route': state.route(h['id'])}
            return web.json_response({'ticket': ticket})
        if path == '/api/codex/session/create':
            fresh(state, session)
            return web.json_response({'session': state.create_codex_session(p)})
        if path == '/api/codex/session/import':
            fresh(state, session)
            return web.json_response({'session': state.create_codex_session(p)})
        if path == '/api/codex/session/delete':
            fresh(state, session)
            state.delete_codex_session(str(p.get('id', '')))
            return web.json_response({'ok': True})
        if path == '/api/codex/message':
            fresh(state, session)
            session_value, message_id, prompt = state.begin_codex_turn(str(p.get('id', '')), p.get('prompt', ''))
            job = asyncio.create_task(run_codex_turn(state, session_value['id'], message_id, prompt))
            state.codex_jobs[session_value['id']] = job
            state.audit('codex_turn_started', session_value['host_id'])
            return web.json_response({'session': state.codex_session(session_value['id'])})
        if path == '/api/codex/cancel':
            fresh(state, session)
            session_id = str(p.get('id', ''))
            job = state.codex_jobs.get(session_id)
            if not job:
                raise ValueError('该会话当前没有运行中的回复')
            job.cancel()
            return web.json_response({'ok': True})
        if path == '/api/network/probe':
            ids = p.get('ids') or [h['id'] for h in state.hosts()]
            if not isinstance(ids, list) or len(ids) > 64:
                raise ValueError('探测主机列表无效')
            results = await asyncio.gather(*(network_probe_host(state, str(value)) for value in ids))
            state.audit('network_probe', f'{len(results)} hosts')
            return web.json_response({'results': results, 'checked_at': int(time.time())})
        fresh(state, session)
        if path == '/api/network/helper/install':
            ids = p.get('ids', [])
            if not isinstance(ids, list) or not ids or len(ids) > 32:
                raise ValueError('请选择要安装辅助程序的主机')
            results = []
            for value in dict.fromkeys(str(v) for v in ids):
                results.append({'id': value, **await network_install_helper(state, value)})
            return web.json_response({'results': results})
        if path == '/api/network/profile/save':
            profile = state.network.save(p)
            state.audit('network_profile_saved', profile['name'])
            return web.json_response({'profile': profile})
        if path == '/api/network/profile/enable':
            async with state.network_changes:
                profile = await network_enable_profile(state, str(p.get('id', '')))
            return web.json_response({'profile': profile})
        if path == '/api/network/profile/disable':
            async with state.network_changes:
                profile = await network_disable_profile(state, str(p.get('id', '')))
            return web.json_response({'profile': profile})
        if path == '/api/network/profile/delete':
            profile = state.network.get(str(p.get('id', '')))
            state.network.delete(str(p.get('id', '')))
            state.audit('network_profile_deleted', profile['name'] if profile else str(p.get('id', '')))
            return web.json_response({'ok': True})
        if path == '/api/host/save':
            return web.json_response({'host': state.save_host(p)})
        if path == '/api/host/delete':
            if any(h['jump_id'] == p.get('id') for h in state.hosts()):
                raise ValueError('该主机仍被用作跳板，请先修改关联路径')
            target_id = p.get('id')
            if any(profile.get('gateway_id') == target_id or target_id in profile.get('client_ids', [])
                   for profile in state.network.profiles()):
                raise ValueError('该主机仍被网络借助方案使用，请先删除对应方案')
            if state.codex_sessions(str(target_id)):
                raise ValueError('该主机仍有 Codex 会话，请先删除对应会话')
            with state.connect() as db:
                db.execute('DELETE FROM hosts WHERE id=?', (p.get('id'),))
            state.audit('host_deleted', str(p.get('id')))
        elif path == '/api/host/inspect':
            h = state.host(p.get('id'))
            if not h:
                raise ValueError('请先保存主机再读取指纹')
            async with state.operations, ssh_route(state, h['id'], stop_before=True) as tunnel:
                key = await asyncio.wait_for(asyncssh.get_server_host_key(h['address'], port=h['port'], tunnel=tunnel, config=[]), 15)
            if not key:
                raise ValueError('未读取到主机公钥')
            return web.json_response({'host_key': key.export_public_key().decode().strip(), 'fingerprint': key.get_fingerprint('sha256')})
        elif path == '/api/credential/save':
            return web.json_response({'id': state.save_credential(p)})
        elif path == '/api/credential/delete':
            if any(h['credential_id'] == p.get('id') for h in state.hosts()):
                raise ValueError('凭据仍被主机使用')
            with state.connect() as db:
                db.execute('DELETE FROM vault WHERE id=?', (p.get('id'),))
            state.audit('credential_deleted', str(p.get('id')))
        elif path == '/api/device/revoke':
            with state.legacy.store._connect() as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (p.get('id'),))
            state.recent.pop(p.get('id'), None)
            state.audit('device_revoked')
        elif path == '/api/password':
            password = str(p.get('new_password', ''))
            if not 12 <= len(password) <= 256:
                raise ValueError('新密码需为 12–256 个字符')
            updated = {**state.legacy.credentials, 'password_hash': await asyncio.to_thread(hash_password, password)}
            temporary = state.data / 'credentials.tmp'
            temporary.write_text(json.dumps(updated), encoding='utf8')
            temporary.chmod(0o600)
            temporary.replace(state.data / 'credentials.json')
            state.legacy.credentials = updated
            with state.legacy.store._connect() as db:
                db.execute('DELETE FROM sessions')
            state.recent.clear()
            state.audit('password_changed')
        elif path == '/api/key':
            key = state.legacy.rotate_key(bool(p.get('revoke')))
            state.recent.clear()
            state.audit('key_revoked' if not key else 'key_rotated')
            return web.json_response({'key': key})
        elif path == '/api/passkey-delete':
            with state.connect() as db:
                db.execute('DELETE FROM passkeys WHERE id=?', (p.get('id'),))
            # Revoke existing sessions as their original authenticator is not stored.
            with state.legacy.store._connect() as db:
                db.execute('DELETE FROM sessions')
            state.audit('passkey_deleted')
        else:
            raise web.HTTPNotFound(text='不存在')
        return web.json_response({'ok': True})

    async def passkey(request, p):
        if not state.passkey_allowed(request):
            raise ValueError('通行密钥需要配置固定 HTTPS 入口')
        register = request.path in ('/api/passkey/register-options', '/api/passkey/register')
        session = request.get(SESSION_KEY)
        if register:
            fresh(state, session)
        else:
            state.check_rate(request.remote or '')
        rp = urlsplit(state.origin).hostname
        if request.path.endswith('options'):
            with state.connect() as db:
                keys = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(r['id'])) for r in db.execute('SELECT id FROM passkeys')]
            if register:
                opts = generate_registration_options(rp_id=rp, rp_name='Server Network Assist', user_name='admin', user_id=b'server-network-assist-admin',
                    exclude_credentials=keys, authenticator_selection=AuthenticatorSelectionCriteria(
                        resident_key=ResidentKeyRequirement.PREFERRED, user_verification=UserVerificationRequirement.REQUIRED))
            else:
                if not keys:
                    raise ValueError('尚未添加通行密钥，请先使用密码登录')
                opts = generate_authentication_options(rp_id=rp, allow_credentials=keys, user_verification=UserVerificationRequirement.REQUIRED)
            state.challenges = {k:v for k,v in state.challenges.items() if v['expires'] > time.time()}
            if len(state.challenges) >= 100:
                raise web.HTTPTooManyRequests(text='验证请求过多')
            id = secrets.token_urlsafe(32)
            state.challenges[id] = {'challenge': opts.challenge, 'register': register, 'expires': time.time()+120,
                'session': session['token_hash'] if register else None}
            response = web.json_response(json.loads(options_to_json(opts)))
            response.set_cookie('pk_challenge', id, httponly=True, secure=state.cookie_secure(request),
                                samesite='Strict', max_age=120, path='/')
            return response
        challenge = state.challenges.pop(request.cookies.get('pk_challenge', ''), None)
        if not challenge or challenge['expires'] < time.time() or challenge['register'] != register:
            raise ValueError('验证已过期，请重新操作')
        if register and challenge['session'] != session['token_hash']:
            raise web.HTTPForbidden(text='验证会话不匹配')
        try:
            if register:
                result = verify_registration_response(credential=p['credential'], expected_challenge=challenge['challenge'],
                    expected_rp_id=rp, expected_origin=state.origin, require_user_verification=True)
                with state.connect() as db:
                    db.execute('INSERT INTO passkeys VALUES (?,?,?,?,?)', (bytes_to_base64url(result.credential_id),
                        str(p.get('name','通行密钥'))[:80], bytes_to_base64url(result.credential_public_key), result.sign_count, int(time.time())))
                state.audit('passkey_added')
                response = web.json_response({'ok': True})
            else:
                with state.connect() as db:
                    row = db.execute('SELECT * FROM passkeys WHERE id=?', (p['credential']['id'],)).fetchone()
                    if not row:
                        raise ValueError('通行密钥不存在')
                    result = verify_authentication_response(credential=p['credential'], expected_challenge=challenge['challenge'],
                        expected_rp_id=rp, expected_origin=state.origin, credential_public_key=base64url_to_bytes(row['public_key']),
                        credential_current_sign_count=row['sign_count'], require_user_verification=True)
                    db.execute('UPDATE passkeys SET sign_count=? WHERE id=?', (result.new_sign_count, row['id']))
                response = new_session(state, request, p)
        except Exception as e:
            state.fail(request.remote or '')
            raise ValueError('通行密钥验证失败，请重试或使用密码') from e
        response.del_cookie('pk_challenge', path='/')
        return response

    async def websocket(request):
        session = current(state, request)
        if not session:
            raise web.HTTPUnauthorized(text='请先登录')
        if len(state.sockets) >= 12:
            raise web.HTTPTooManyRequests(text='终端数量已达上限')
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=32768, compress=False)
        await ws.prepare(request)
        state.sockets[id(ws)] = ws
        jobs = []
        try:
            message = await ws.receive_json(timeout=10)
            ticket = state.tickets.pop(message.get('ticket', ''), None)
            if (not ticket or ticket.get('kind') != 'terminal' or ticket['expires'] < time.time() or
                    ticket['session'] != session['token_hash']):
                await ws.close(code=1008, message=b'Invalid ticket')
                return ws
            h = state.host(ticket['id'])
            if not h or not h['terminal_enabled']:
                raise ValueError('主机已移除或终端已禁用')
            if state.route(h['id']) != ticket['route']:
                raise ValueError('主机配置已改变，请重新连接')
            async def watch_session():
                while not ws.closed:
                    await asyncio.sleep(1)
                    try:
                        unchanged = state.route(h['id']) == ticket['route']
                    except ValueError:
                        unchanged = False
                    if not current(state, request) or not unchanged:
                        await ws.close(code=1008, message=b'Session revoked or expired')
                        return
            jobs.append(asyncio.create_task(watch_session()))
            async with ssh_route(state, h['id']) as conn:
                if ws.closed or not current(state, request):
                    return ws
                command = 'tmux new-session -A -s network-assist-admin' if ticket['persistent'] else None
                async with conn.create_process(command, term_type='xterm-256color', term_size=(100,30), encoding=None) as process:
                    state.audit('terminal_open', h['name'])
                    await ws.send_json({'type':'ready', 'route': [r['name'] for r in state.route(h['id'])]})
                    async def stream(reader):
                        while not ws.closed:
                            chunk = await reader.read(16384)
                            if not chunk:
                                break
                            await ws.send_bytes(chunk)
                    jobs.extend([asyncio.create_task(stream(process.stdout)), asyncio.create_task(stream(process.stderr))])
                    async def wait_exit():
                        await process.wait_closed()
                        await asyncio.gather(*jobs[1:3], return_exceptions=True)
                        await ws.close()
                    jobs.append(asyncio.create_task(wait_exit()))
                    async for msg in ws:
                        try:
                            unchanged = state.route(h['id']) == ticket['route']
                        except ValueError:
                            unchanged = False
                        if not current(state, request) or not unchanged:
                            await ws.close(code=1008)
                            break
                        if msg.type == WSMsgType.BINARY:
                            process.stdin.write(msg.data)
                            await process.stdin.drain()
                        elif msg.type == WSMsgType.TEXT:
                            event = json.loads(msg.data)
                            if event.get('type') == 'input':
                                process.stdin.write(str(event.get('data','')).encode())
                                await process.stdin.drain()
                            elif event.get('type') == 'resize':
                                process.change_terminal_size(max(20,min(400,int(event['cols']))), max(5,min(150,int(event['rows']))))
                    process.close()
            state.audit('terminal_closed', h['name'])
        except Exception as e:
            if not ws.closed:
                with contextlib.suppress(Exception):
                    await ws.send_json({'type':'error','message': str(e) if isinstance(e, ValueError) else f'终端连接结束（{type(e).__name__}）'})
                await ws.close()
        finally:
            for task in jobs:
                task.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            state.sockets.pop(id(ws), None)
        return ws

    async def browser_websocket(request):
        session = current(state, request)
        if not session:
            raise web.HTTPUnauthorized(text='请先登录')
        if len(state.sockets) >= 12:
            raise web.HTTPTooManyRequests(text='远程连接数量已达上限')
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=16384, compress=False)
        await ws.prepare(request)
        state.sockets[id(ws)] = ws
        jobs = []
        temporary = ''
        try:
            message = await ws.receive_json(timeout=10)
            ticket = state.tickets.pop(message.get('ticket', ''), None)
            if (not ticket or ticket.get('kind') != 'browser' or ticket['expires'] < time.time() or
                    ticket['session'] != session['token_hash']):
                await ws.close(code=1008, message=b'Invalid ticket')
                return ws
            host = state.host(ticket['id'])
            if not host or not host.get('browser_enabled', True) or state.route(host['id']) != ticket['route']:
                raise ValueError('主机已移除、配置改变或浏览器标签已禁用')
            if host['id'] in state.browser_hosts:
                raise ValueError('这台服务器已有浏览器标签正在使用')
            state.browser_hosts.add(host['id'])
            async with ssh_route(state, host['id']) as connection:
                platform = await detect_platform(connection)
                async with connection.start_sftp_client() as sftp:
                    home = await sftp.realpath('.')
                    temporary = home.rstrip('/') + '/sna-browser-' + secrets.token_hex(8) + '.py'
                    await sftp.put(str(ROOT / 'browser_bridge.py'), temporary)
                python_arguments = ['python3']
                if platform == 'windows':
                    python_arguments = []
                    for candidate in (['python.exe'], ['python'], ['py.exe', '-3']):
                        probe = await connection.run(subprocess.list2cmdline(candidate + ['--version']),
                                                     timeout=10, check=False)
                        if probe.exit_status == 0:
                            python_arguments = candidate
                            break
                    if not python_arguments:
                        raise ValueError('远端 Windows 未找到可用 Python，无法启动浏览器桥接')
                arguments = python_arguments + [temporary, ticket['url']]
                command = subprocess.list2cmdline(arguments) if platform == 'windows' else shlex.join(arguments)
                async with connection.create_process(command, encoding='utf-8') as process:
                    state.audit('browser_open', host['name'])

                    async def stream():
                        while not ws.closed:
                            line = await process.stdout.readline()
                            if not line:
                                break
                            await ws.send_str(line.rstrip('\r\n'))

                    jobs.append(asyncio.create_task(stream()))
                    async for item in ws:
                        if item.type != WSMsgType.TEXT:
                            continue
                        if not current(state, request) or state.route(host['id']) != ticket['route']:
                            await ws.close(code=1008)
                            break
                        event = json.loads(item.data)
                        if event.get('type') not in ('navigate', 'click', 'text', 'key'):
                            continue
                        process.stdin.write(json.dumps(event, ensure_ascii=False) + '\n')
                        await process.stdin.drain()
                    process.terminate()
                with contextlib.suppress(Exception):
                    await connection.run(shlex.join(['rm', '-f', '--', temporary]) if platform != 'windows'
                                         else subprocess.list2cmdline(['cmd.exe', '/c', 'del', '/q', temporary]),
                                         timeout=10, check=False)
            state.audit('browser_closed', host['name'])
        except Exception as error:
            if not ws.closed:
                with contextlib.suppress(Exception):
                    await ws.send_json({'type': 'error', 'message': str(error) if isinstance(error, ValueError)
                                        else f'浏览器连接结束（{type(error).__name__}）'})
                await ws.close()
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            state.sockets.pop(id(ws), None)
            if 'host' in locals():
                state.browser_hosts.discard(host['id'])
        return ws

    async def static(request):
        name = request.match_info.get('name', '') or 'index.html'
        root = (ROOT / 'ui').resolve()
        target = (root / name).resolve()
        if root not in target.parents or not target.is_file():
            target = root / 'index.html'
        if not target.is_file():
            raise web.HTTPServiceUnavailable(text='前端尚未构建，请先执行 npm run build')
        if target.name == 'index.html' and state.base_path:
            body = re.sub(r'<base href="/"\s*/?>', f'<base href="{state.base_path}/">',
                          target.read_text(encoding='utf-8'), count=1)
            return web.Response(text=body, content_type='text/html')
        return web.FileResponse(target)

    async def shutdown(app):
        for job in list(state.codex_jobs.values()):
            job.cancel()
        await asyncio.gather(*list(state.codex_jobs.values()), return_exceptions=True)
        await asyncio.gather(*(ws.close(code=1001) for ws in list(state.sockets.values())), return_exceptions=True)
    app.on_shutdown.append(shutdown)
    app.router.add_get('/ws', websocket)
    app.router.add_get('/ws/browser', browser_websocket)
    app.router.add_get('/api/{tail:.*}', get)
    app.router.add_post('/api/{tail:.*}', post)
    app.router.add_get('/', static)
    app.router.add_get('/{name:.*}', static)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--master-key', type=Path, required=True)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9180)
    args = parser.parse_args()
    web.run_app(create_app(args.data, args.master_key), host=args.bind, port=args.port, access_log=None)
