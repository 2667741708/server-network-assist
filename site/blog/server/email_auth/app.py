"""Private blog sign-in and narrowly scoped Decap content gateway."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import EmailMessage
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import smtplib
import sqlite3
import ssl
import threading
import time
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
import httpx

PREFIX = '/projects/email-auth'
SESSION_COOKIE = '__Secure-whm-blog-session'
PREAUTH_COOKIE = '__Secure-whm-blog-preauth'
POSTS = 'site/blog/src/content/posts/'
MEDIA = 'site/blog/public/images/uploads/'
FOLDERS = {'tutorials', 'introductions', 'retrospectives', 'experiments',
           'explanations', 'quick-questions'}
SESSION_SECONDS = 8 * 3600
CODE_SECONDS = 600


@dataclass
class Settings:
    database: Path
    secret_file: Path
    github_token_file: Path
    smtp_config_file: Path
    google_client_file: Path
    qq_email: str
    google_email: str
    origin: str = 'https://whm12.art'
    repository: str = '2667741708/server-network-assist'
    branch: str = 'main'

    @classmethod
    def load(cls):
        path = Path(os.environ.get('BLOG_AUTH_CONFIG', '/etc/blog-email-auth/config.json'))
        raw = json.loads(path.read_text())
        for key in ('database', 'secret_file', 'github_token_file', 'smtp_config_file', 'google_client_file'):
            raw[key] = Path(raw[key])
        return cls(**raw)

    def google(self):
        if not self.google_client_file.is_file():
            return None
        data = json.loads(self.google_client_file.read_text())
        client = data.get('web', data)
        if not client.get('client_id') or not client.get('client_secret'):
            return None
        callback = self.origin + PREFIX + '/google/callback'
        if callback not in client.get('redirect_uris', [callback]):
            return None
        return client


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, settings: Settings):
        self.path = settings.database
        self.key = settings.secret_file.read_bytes().strip()
        if len(self.key) < 32:
            raise RuntimeError('Session key must contain at least 32 bytes')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS codes (
                  id TEXT PRIMARY KEY, email TEXT NOT NULL, browser TEXT NOT NULL,
                  code_hash TEXT NOT NULL, expires INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions (
                  token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, csrf TEXT NOT NULL,
                  expires INTEGER NOT NULL, method TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS limits (
                  bucket TEXT NOT NULL, created INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS limit_lookup ON limits(bucket, created);
                CREATE TABLE IF NOT EXISTS oauth (
                  state_hash TEXT PRIMARY KEY, browser TEXT NOT NULL, nonce TEXT NOT NULL,
                  verifier TEXT NOT NULL, expires INTEGER NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def code_hash(self, challenge, code):
        return hmac.new(self.key, (challenge + ':' + code).encode(), hashlib.sha256).hexdigest()

    def limit(self, db, bucket, seconds, maximum):
        now = int(time.time())
        count = db.execute('SELECT count(*) FROM limits WHERE bucket=? AND created>?',
                           (bucket, now - seconds)).fetchone()[0]
        if count >= maximum:
            raise HTTPException(429, '操作过于频繁，请稍后再试')
        db.execute('INSERT INTO limits VALUES (?,?)', (bucket, now))
        db.execute('DELETE FROM limits WHERE created<?', (now - 86400,))

    def new_session(self, email, method):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (int(time.time()),))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                       (digest(token), email, csrf, int(time.time()) + SESSION_SECONDS, method))
        return token

    def session(self, token):
        if not token:
            return None
        with self.connection() as db:
            row = db.execute('SELECT * FROM sessions WHERE token_hash=? AND expires>?',
                             (digest(token), int(time.time()))).fetchone()
            return dict(row) if row else None


def safe_path(path, *, folder=False):
    if not isinstance(path, str) or len(path) > 500 or '\\' in path or '%' in path:
        raise HTTPException(400, '无效文件路径')
    parts = path.split('/')
    if any(not x or x in ('.', '..') or x.startswith('.') or any(ord(c) < 32 for c in x) for x in parts):
        raise HTTPException(400, '无效文件路径')
    if path.startswith(POSTS):
        relative = path[len(POSTS):].split('/')
        if relative[0] not in FOLDERS:
            raise HTTPException(403, '不允许访问此文章目录')
        if folder or (len(relative) >= 2 and PurePosixPath(path).suffix.lower() == '.md'):
            return path
    if path.startswith(MEDIA):
        if folder or PurePosixPath(path).suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
            return path
    if folder and path == MEDIA.rstrip('/'):
        return path
    raise HTTPException(403, '仅允许编辑博客文章和上传图片')


class Repository:
    """No client-supplied host/repository/ref or arbitrary Git operations."""
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()

    def request(self, method, path, **kwargs):
        token = self.settings.github_token_file.read_text().strip()
        headers = {'Authorization': 'Bearer ' + token,
                   'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'whm-blog-content-gateway'}
        try:
            with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
                response = client.request(method,
                    'https://api.github.com/repos/' + self.settings.repository + path,
                    headers=headers, **kwargs)
        except httpx.HTTPError:
            raise HTTPException(502, '仓库连接暂时失败，请稍后重试')
        if response.status_code == 404:
            raise HTTPException(404, '文件不存在')
        if response.status_code in (409, 422):
            raise HTTPException(409, '文件已变化，请刷新后重新保存，避免覆盖其他修改')
        if response.status_code >= 400:
            raise HTTPException(502, '仓库操作失败，请检查服务凭据或稍后重试')
        return response.json() if response.content else {}

    def file(self, path):
        safe_path(path)
        value = self.request('GET', '/contents/' + path, params={'ref': self.settings.branch})
        if not isinstance(value, dict) or value.get('type') != 'file' or value.get('size', 0) > 8_000_000:
            raise HTTPException(400, '文件类型或大小不受支持')
        return value

    def files(self, folder):
        safe_path(folder, folder=True)
        tree = self.request('GET', '/git/trees/' + self.settings.branch, params={'recursive': '1'})
        if tree.get('truncated'):
            raise HTTPException(502, '仓库目录过大，无法完整列出文件')
        result = []
        for value in tree.get('tree', []):
            path = value.get('path', '')
            if value.get('type') != 'blob' or not path.startswith(folder + '/'):
                continue
            try:
                safe_path(path)
            except HTTPException:
                continue
            result.append({'path': path, 'sha': value['sha'], 'size': value.get('size', 0)})
        return result

    def save(self, path, content, sha):
        safe_path(path)
        with self.lock:
            payload = {'message': 'blog: update ' + path,
                       'content': base64.b64encode(content).decode(), 'branch': self.settings.branch}
            if sha:
                payload['sha'] = sha
            return self.request('PUT', '/contents/' + path, json=payload)

    def delete(self, path, sha):
        safe_path(path)
        if not re.fullmatch('[0-9a-f]{40}', sha or ''):
            raise HTTPException(400, '删除需要当前文件版本')
        with self.lock:
            return self.request('DELETE', '/contents/' + path,
                json={'message': 'blog: delete ' + path, 'sha': sha, 'branch': self.settings.branch})


def send_code(settings, address, code):
    config = json.loads(settings.smtp_config_file.read_text())
    if not config.get('enabled'):
        raise RuntimeError('Email service disabled')
    message = EmailMessage()
    message['From'] = config['sender']
    message['To'] = address
    message['Subject'] = '项目手记：写作后台登录验证码'
    message.set_content('你的博客写作后台验证码是：' + code + '\n\n10 分钟内有效，只能使用一次。'
                        '如果不是你本人操作，请忽略此邮件。不要向他人提供验证码。')
    logging.getLogger('blog-auth').info('Connecting to login email service')
    if str(config['security']).lower() in ('ssl', 'smtps', 'tls') and int(config['port']) == 465:
        client = smtplib.SMTP_SSL(config['host'], int(config['port']), timeout=15,
                                 context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(config['host'], int(config['port']), timeout=15)
        client.ehlo()
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
    try:
        logging.getLogger('blog-auth').info('Login email TLS ready')
        client.login(config['username'], Path(config['password_file']).read_text().strip())
        logging.getLogger('blog-auth').info('Login email authenticated')
        client.send_message(message)
        logging.getLogger('blog-auth').info('Login email accepted by SMTP')
    finally:
        client.quit()


def create_app(settings=None, repository=None, mailer=send_code):
    settings = settings or Settings.load()
    store = Store(settings)
    repo = repository or Repository(settings)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.settings = store, settings

    @app.middleware('http')
    async def protect(request, call_next):
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            if request.headers.get('origin') != settings.origin:
                return JSONResponse({'detail': '请求来源无效'}, 403)
            try:
                length = int(request.headers.get('content-length', '-1'))
            except ValueError:
                length = -1
            if length < 0 or length > 12_000_000:
                return JSONResponse({'detail': '请求大小不受支持'}, 413)
            if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'detail': '仅接受 JSON 请求'}, 415)
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; "
                "img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        return response

    def current(request, write=False):
        session = store.session(request.cookies.get(SESSION_COOKIE))
        if not session or session['email'] not in (settings.qq_email, settings.google_email):
            raise HTTPException(401, '登录已过期，请重新登录')
        if write and not hmac.compare_digest(request.headers.get('x-csrf-token', ''), session['csrf']):
            raise HTTPException(403, '请刷新页面后重试')
        return session

    def set_session(response, token):
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, path='/projects/',
                            secure=True, httponly=True, samesite='lax')

    @app.get(PREFIX + '/healthz')
    def health():
        return {'ok': True, 'google_enabled': bool(settings.google()), 'email_enabled': True}

    @app.get(PREFIX + '/session')
    def session(request: Request):
        found = store.session(request.cookies.get(SESSION_COOKIE))
        if not found or found['email'] not in (settings.qq_email, settings.google_email):
            return {'authenticated': False, 'google_enabled': bool(settings.google()), 'email_enabled': True}
        return {'authenticated': True, 'csrf': found['csrf'], 'user': {
            'login': 'blog-owner', 'name': '博客作者', 'email': found['email']},
            'google_enabled': bool(settings.google()), 'email_enabled': True}

    @app.get(PREFIX + '/login')
    def login_page():
        browser = secrets.token_urlsafe(32)
        html = (Path(__file__).parent / 'login.html').read_text()
        response = HTMLResponse(html)
        response.set_cookie(PREAUTH_COOKIE, browser, max_age=1800, path=PREFIX,
                            secure=True, httponly=True, samesite='lax')
        return response

    @app.get(PREFIX + '/login.js')
    def login_js():
        return Response((Path(__file__).parent / 'login.js').read_text(), media_type='text/javascript')

    @app.get(PREFIX + '/login.css')
    def login_css():
        return Response((Path(__file__).parent / 'login.css').read_text(), media_type='text/css')

    @app.post(PREFIX + '/otp/request')
    async def request_code(request: Request):
        browser = request.cookies.get(PREAUTH_COOKIE)
        if not browser or len(browser) > 100:
            raise HTTPException(400, '请先打开登录页面')
        body = await request.json()
        address = str(body.get('email', '')).strip().lower()
        if not re.fullmatch(r'[^\s@]{1,64}@[^\s@]{1,190}', address):
            raise HTTPException(400, '请输入有效邮箱')
        ip = request.headers.get('x-real-ip', request.client.host if request.client else 'unknown')[:100]
        challenge, code = secrets.token_urlsafe(24), '%06d' % secrets.randbelow(1_000_000)
        with store.connection() as db:
            store.limit(db, 'mail-minute:' + digest(address), 60, 1)
            store.limit(db, 'mail-hour:' + digest(address), 3600, 6)
            store.limit(db, 'ip-hour:' + digest(ip), 3600, 12)
            db.execute('DELETE FROM codes WHERE expires<?', (int(time.time()),))
            db.execute('DELETE FROM codes WHERE email=? AND browser=?', (address, digest(browser)))
            db.execute('INSERT INTO codes(id,email,browser,code_hash,expires) VALUES (?,?,?,?,?)',
                       (challenge, address, digest(browser), store.code_hash(challenge, code),
                        int(time.time()) + CODE_SECONDS))
        if address == settings.qq_email:
            try:
                # Run blocking SMTP outside the ASGI event loop.
                from starlette.concurrency import run_in_threadpool
                await run_in_threadpool(mailer, settings, address, code)
            except Exception as error:
                logging.getLogger('blog-auth').warning('Login email failed: %s, SMTP status %s',
                    type(error).__name__, getattr(error, 'smtp_code', None))
                with store.connection() as db:
                    db.execute('DELETE FROM codes WHERE id=?', (challenge,))
                raise HTTPException(503, '邮件暂时发送失败，请稍后重试')
        return {'challenge': challenge, 'message': '若该邮箱已获写作权限，将收到登录验证码。'}

    @app.post(PREFIX + '/otp/verify')
    async def verify_code(request: Request):
        body = await request.json()
        challenge, code = str(body.get('challenge', '')), str(body.get('code', ''))
        browser = request.cookies.get(PREAUTH_COOKIE, '')
        accepted = False
        with store.connection() as db:
            row = db.execute('SELECT * FROM codes WHERE id=?', (challenge,)).fetchone()
            if row and row['expires'] > int(time.time()) and row['attempts'] < 5:
                accepted = (bool(browser) and row['browser'] == digest(browser)
                    and row['email'] == settings.qq_email and re.fullmatch(r'\d{6}', code) is not None
                    and hmac.compare_digest(row['code_hash'], store.code_hash(challenge, code)))
                if accepted:
                    db.execute('DELETE FROM codes WHERE id=?', (challenge,))
                else:
                    db.execute('UPDATE codes SET attempts=attempts+1 WHERE id=?', (challenge,))
        if not accepted:
            raise HTTPException(400, '验证码无效或已过期，请重新获取')
        response = JSONResponse({'ok': True, 'redirect': '/projects/admin/'})
        set_session(response, store.new_session(settings.qq_email, 'email'))
        return response

    @app.get(PREFIX + '/google/start')
    def google_start():
        config = settings.google()
        if not config:
            raise HTTPException(503, 'Google 登录正在配置，请先使用邮箱或 GitHub 登录')
        state, browser, nonce, verifier = [secrets.token_urlsafe(32) for _ in range(4)]
        with store.connection() as db:
            db.execute('DELETE FROM oauth WHERE expires<?', (int(time.time()),))
            db.execute('INSERT INTO oauth VALUES (?,?,?,?,?)',
                       (digest(state), digest(browser), nonce, verifier, int(time.time()) + 600))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        query = {'client_id': config['client_id'], 'redirect_uri': settings.origin + PREFIX + '/google/callback',
                 'response_type': 'code', 'scope': 'openid email profile', 'state': state, 'nonce': nonce,
                 'code_challenge': challenge, 'code_challenge_method': 'S256',
                 'login_hint': settings.google_email, 'prompt': 'select_account'}
        response = RedirectResponse('https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(query), 302)
        response.set_cookie(PREAUTH_COOKIE, browser, max_age=600, path=PREFIX,
                            secure=True, httponly=True, samesite='lax')
        return response

    @app.get(PREFIX + '/google/callback')
    def google_callback(request: Request):
        config = settings.google()
        if not config:
            raise HTTPException(503, 'Google 登录尚未配置')
        state = request.query_params.get('state', '')
        browser = request.cookies.get(PREAUTH_COOKIE, '')
        with store.connection() as db:
            row = db.execute('SELECT * FROM oauth WHERE state_hash=?', (digest(state),)).fetchone()
            if not row or not browser or row['browser'] != digest(browser) or row['expires'] <= int(time.time()):
                raise HTTPException(400, '登录请求已过期，请返回后台重新登录')
            db.execute('DELETE FROM oauth WHERE state_hash=?', (digest(state),))
        if request.query_params.get('error') or not request.query_params.get('code'):
            raise HTTPException(400, 'Google 登录已取消，请返回后台重试')
        try:
            with httpx.Client(timeout=20, trust_env=False) as client:
                reply = client.post('https://oauth2.googleapis.com/token', data={
                    'grant_type': 'authorization_code', 'client_id': config['client_id'],
                    'client_secret': config['client_secret'], 'code': request.query_params['code'],
                    'redirect_uri': settings.origin + PREFIX + '/google/callback', 'code_verifier': row['verifier']})
                reply.raise_for_status()
                token = reply.json()['id_token']
            from google.auth.transport.requests import Request as GoogleRequest
            from google.oauth2.id_token import verify_oauth2_token
            claims = verify_oauth2_token(token, GoogleRequest(), config['client_id'])
            if (claims.get('nonce') != row['nonce'] or claims.get('email_verified') is not True
                    or str(claims.get('email', '')).lower() != settings.google_email):
                raise ValueError('Not an allowed verified identity')
        except Exception:
            raise HTTPException(403, 'Google 身份验证失败或此账号没有写作权限')
        response = RedirectResponse('/projects/admin/', 302)
        set_session(response, store.new_session(settings.google_email, 'google'))
        return response

    @app.post(PREFIX + '/logout')
    def logout(request: Request):
        current(request, write=True)
        token = request.cookies.get(SESSION_COOKIE, '')
        with store.connection() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))
        response = JSONResponse({'ok': True})
        response.delete_cookie(SESSION_COOKIE, path='/projects/', secure=True, httponly=True, samesite='lax')
        return response

    @app.get(PREFIX + '/content/files')
    def files(request: Request, folder: str):
        current(request)
        return {'files': repo.files(folder)}

    @app.get(PREFIX + '/content/file')
    def file(request: Request, path: str):
        current(request)
        item = repo.file(path)
        return {'path': path, 'sha': item['sha'], 'content': item.get('content', ''), 'encoding': 'base64'}

    @app.post(PREFIX + '/content/save')
    async def save(request: Request):
        current(request, write=True)
        body = await request.json()
        path = safe_path(body.get('path'))
        try:
            content = base64.b64decode(body.get('content', ''), validate=True)
        except (ValueError, TypeError):
            raise HTTPException(400, '文件内容编码无效')
        if not content or len(content) > (1_000_000 if path.startswith(POSTS) else 8_000_000):
            raise HTTPException(400, '文件内容为空或过大')
        if path.startswith(POSTS):
            try:
                text = content.decode('utf-8')
                if '\0' in text or not text.startswith('---'):
                    raise ValueError()
            except (UnicodeError, ValueError):
                raise HTTPException(400, '文章必须为带元数据的 UTF-8 Markdown')
        else:
            suffix = PurePosixPath(path).suffix.lower()
            valid = ((suffix == '.png' and content.startswith(b'\x89PNG\r\n\x1a\n'))
                or (suffix in ('.jpg', '.jpeg') and content.startswith(b'\xff\xd8\xff'))
                or (suffix == '.gif' and content.startswith((b'GIF87a', b'GIF89a')))
                or (suffix == '.webp' and content[:4] == b'RIFF' and content[8:12] == b'WEBP'))
            if not valid:
                raise HTTPException(400, '只接受 PNG、JPEG、GIF 或 WebP 图片')
        sha = body.get('sha')
        if sha is not None and not re.fullmatch('[0-9a-f]{40}', str(sha)):
            raise HTTPException(400, '文件版本无效')
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(repo.save, path, content, sha)
        return {'ok': True, 'sha': result['content']['sha'], 'path': path}

    @app.post(PREFIX + '/content/delete')
    async def delete(request: Request):
        current(request, write=True)
        body = await request.json()
        path = safe_path(body.get('path'))
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(repo.delete, path, body.get('sha'))
        return {'ok': True}

    return app
