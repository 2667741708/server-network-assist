#!/usr/bin/env python3
"""Small self-hosted GitHub OAuth proxy for Decap CMS.

The process is intentionally dependency-free and only listens on localhost.
Caddy exposes /projects/oauth/{auth,callback,healthz} publicly and strips the
public prefix before forwarding requests here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from threading import Thread
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen


LOG = logging.getLogger("decap-oauth")
COOKIE_NAME = "decap_oauth_state"
STATE_TTL_SECONDS = 600
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"


class OAuthError(Exception):
    """A user-safe OAuth failure."""


@dataclass(frozen=True)
class Config:
    github_client_id: str
    github_client_secret: str
    public_base_url: str
    cms_origin: str
    state_secret: str
    github_scope: str
    github_repo: str
    allowed_users: tuple[str, ...]
    bind_host: str
    port: int

    @classmethod
    def from_env(cls) -> "Config":
        allowed = tuple(
            item.strip().lower()
            for item in os.environ.get("GITHUB_ALLOWED_USERS", "").split(",")
            if item.strip()
        )
        return cls(
            github_client_id=os.environ.get("GITHUB_OAUTH_ID", "").strip(),
            github_client_secret=os.environ.get("GITHUB_OAUTH_SECRET", "").strip(),
            public_base_url=os.environ.get("OAUTH_PUBLIC_BASE_URL", "").strip().rstrip("/"),
            cms_origin=os.environ.get("CMS_ORIGIN", "").strip().rstrip("/"),
            state_secret=os.environ.get("OAUTH_STATE_SECRET", "").strip(),
            github_scope=os.environ.get("GITHUB_OAUTH_SCOPE", "public_repo,user").strip(),
            github_repo=os.environ.get(
                "GITHUB_REPO", "2667741708/server-network-assist"
            ).strip(),
            allowed_users=allowed,
            bind_host=os.environ.get("OAUTH_BIND", "127.0.0.1").strip(),
            port=int(os.environ.get("OAUTH_PORT", "9190")),
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.github_client_id
            and self.github_client_secret
            and self.public_base_url.startswith("https://")
            and self.cms_origin.startswith("https://")
            and len(self.state_secret) >= 32
            and "/" in self.github_repo
        )

    @property
    def callback_url(self) -> str:
        return f"{self.public_base_url}/callback"

    @property
    def cookie_path(self) -> str:
        path = urlsplit(self.public_base_url).path.rstrip("/")
        return path or "/"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_state(config: Config, now: int | None = None) -> str:
    issued = int(time.time() if now is None else now)
    payload = _b64(
        json.dumps(
            {"exp": issued + STATE_TTL_SECONDS, "nonce": _b64(secrets.token_bytes(32))},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = hmac.new(
        config.state_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_state(config: Config, state: str, cookie_state: str, now: int | None = None) -> None:
    if not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
        raise OAuthError("OAuth 状态校验失败，请重新点击登录")
    try:
        payload, signature = state.split(".", 1)
        expected = hmac.new(
            config.state_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise OAuthError("OAuth 状态校验失败，请重新点击登录")
        data = json.loads(_unb64(payload))
        if int(data["exp"]) < int(time.time() if now is None else now):
            raise OAuthError("OAuth 登录窗口已过期，请重新点击登录")
        if not data.get("nonce"):
            raise OAuthError("OAuth 状态校验失败，请重新点击登录")
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, base64.binascii.Error):
        raise OAuthError("OAuth 状态校验失败，请重新点击登录") from None


def _github_json(request: Request) -> tuple[int, dict[str, Any]]:
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read()
            status = response.status
    except HTTPError as error:
        body = error.read()
        status = error.code
    except (URLError, TimeoutError) as error:
        LOG.warning("GitHub request failed: %s", error.__class__.__name__)
        raise OAuthError("GitHub 暂时无法访问，请稍后重试") from None
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise OAuthError("GitHub 返回了无法识别的响应") from None
    if not isinstance(data, dict):
        raise OAuthError("GitHub 返回了无效响应")
    return status, data


def exchange_code(config: Config, code: str) -> str:
    form = urlencode(
        {
            "client_id": config.github_client_id,
            "client_secret": config.github_client_secret,
            "code": code,
            "redirect_uri": config.callback_url,
        }
    ).encode("ascii")
    token_request = Request(GITHUB_TOKEN_URL, data=form, method="POST")
    token_request.add_header("Accept", "application/json")
    token_request.add_header("Content-Type", "application/x-www-form-urlencoded")
    _, token_data = _github_json(token_request)
    token = token_data.get("access_token")
    if not isinstance(token, str) or not token:
        LOG.warning("GitHub token exchange rejected: %s", token_data.get("error", "unknown"))
        raise OAuthError("GitHub 授权失败，请检查 OAuth App 与回调地址")

    user_request = Request(f"{GITHUB_API_URL}/user", method="GET")
    user_request.add_header("Accept", "application/vnd.github+json")
    user_request.add_header("X-GitHub-Api-Version", "2022-11-28")
    user_request.add_header("Authorization", f"Bearer {token}")
    user_request.add_header("User-Agent", "server-network-assist-decap-oauth")
    user_status, user_data = _github_json(user_request)
    login = user_data.get("login")
    if user_status != 200 or not isinstance(login, str):
        raise OAuthError("无法确认 GitHub 登录身份")
    if config.allowed_users and login.lower() not in config.allowed_users:
        raise OAuthError("此 GitHub 账号未被允许编辑该博客")

    owner, repo = config.github_repo.split("/", 1)
    repo_request = Request(f"{GITHUB_API_URL}/repos/{owner}/{repo}", method="GET")
    repo_request.add_header("Accept", "application/vnd.github+json")
    repo_request.add_header("X-GitHub-Api-Version", "2022-11-28")
    repo_request.add_header("Authorization", f"Bearer {token}")
    repo_request.add_header("User-Agent", "server-network-assist-decap-oauth")
    repo_status, repo_data = _github_json(repo_request)
    permissions = repo_data.get("permissions") or {}
    if repo_status != 200 or permissions.get("push") is not True:
        raise OAuthError("此 GitHub 账号没有该仓库的写入权限")
    return token


def _js_string(value: str) -> str:
    # Keep untrusted values inside a JavaScript string, never executable HTML.
    return (
        json.dumps(value, ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def callback_html(config: Config, status: str, payload: dict[str, str]) -> str:
    message = f"authorization:github:{status}:{json.dumps(payload, separators=(',', ':'))}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Decap authorization</title>
<meta name="referrer" content="no-referrer"></head><body>
<p>Returning to the editor…</p>
<script>
(() => {{
  const targetOrigin = {_js_string(config.cms_origin)};
  const authMessage = {_js_string(message)};
  const opener = window.opener;
  const finish = () => {{
    if (opener && !opener.closed) opener.postMessage(authMessage, targetOrigin);
    window.removeEventListener('message', finish, false);
    window.setTimeout(() => window.close(), 120);
  }};
  window.addEventListener('message', finish, false);
  if (opener && !opener.closed) opener.postMessage('authorizing:github', targetOrigin);
}})();
</script></body></html>"""


class OAuthHandler(http.server.BaseHTTPRequestHandler):
    server_version = "DecapOAuth/1.0"
    sys_version = ""

    @property
    def config(self) -> Config:
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        LOG.info("%s %s", self.command, self.path.split("?", 1)[0])

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/healthz":
            self.send_json(HTTPStatus.OK if self.config.configured else HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": self.config.configured,
            })
            return
        if path == "/auth":
            self.handle_auth()
            return
        if path == "/callback":
            self.handle_callback()
            return
        self.send_text(HTTPStatus.OK, "Decap OAuth proxy")

    def handle_auth(self) -> None:
        if not self.config.configured:
            self.send_text(HTTPStatus.SERVICE_UNAVAILABLE, "OAuth proxy is not configured")
            return
        query = parse_qs(urlsplit(self.path).query)
        if query.get("provider", [""])[0] != "github":
            self.send_text(HTTPStatus.BAD_REQUEST, "Invalid provider")
            return
        state = make_state(self.config)
        location = GITHUB_AUTHORIZE_URL + "?" + urlencode(
            {
                "client_id": self.config.github_client_id,
                "redirect_uri": self.config.callback_url,
                "scope": self.config.github_scope,
                "state": state,
            }
        )
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Set-Cookie", self.state_cookie(state))
        self.send_security_headers()
        self.end_headers()

    def handle_callback(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        try:
            if not self.config.configured:
                raise OAuthError("OAuth proxy 尚未配置")
            if query.get("provider", [""])[0] != "github":
                raise OAuthError("OAuth provider 无效")
            state = query.get("state", [""])[0]
            code = query.get("code", [""])[0]
            if query.get("error", [""])[0]:
                raise OAuthError("GitHub 用户取消了授权")
            if not code:
                raise OAuthError("GitHub 未返回授权码")
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            cookie_state = cookie.get(COOKIE_NAME)
            verify_state(self.config, state, cookie_state.value if cookie_state else "")
            token = exchange_code(self.config, code)
            html = callback_html(self.config, "success", {"token": token})
            self.send_html(HTTPStatus.OK, html, clear_cookie=True)
        except OAuthError as error:
            LOG.warning("OAuth callback rejected: %s", error)
            html = callback_html(self.config, "error", {"error": str(error)})
            self.send_html(HTTPStatus.BAD_REQUEST, html, clear_cookie=True)
        except Exception:
            LOG.exception("Unexpected OAuth callback failure")
            html = callback_html(self.config, "error", {"error": "OAuth 服务内部错误，请稍后重试"})
            self.send_html(HTTPStatus.INTERNAL_SERVER_ERROR, html, clear_cookie=True)

    def state_cookie(self, state: str) -> str:
        return (
            f"{COOKIE_NAME}={state}; Path={self.config.cookie_path}; Max-Age={STATE_TTL_SECONDS}; "
            "HttpOnly; Secure; SameSite=Lax"
        )

    def clear_state_cookie(self) -> str:
        return f"{COOKIE_NAME}=; Path={self.config.cookie_path}; Max-Age=0; HttpOnly; Secure; SameSite=Lax"

    def send_security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")

    def send_text(self, status: HTTPStatus, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: HTTPStatus, value: dict[str, object]) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, status: HTTPStatus, html: str, clear_cookie: bool = False) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
        if clear_cookie:
            self.send_header("Set-Cookie", self.clear_state_cookie())
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(body)


class OAuthHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: Config):
        super().__init__(address, OAuthHandler)
        self.config = config


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    config = Config.from_env()
    if not config.configured:
        LOG.warning("OAuth proxy is starting without production credentials")
    server = OAuthHTTPServer((config.bind_host, config.port), config)
    LOG.info("Decap OAuth proxy listening on %s:%s", config.bind_host, config.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
