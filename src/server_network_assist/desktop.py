"""Loopback-only desktop panel for the local Windows or Ubuntu network stack.

The desktop view needs only Python's standard library. It can start even while
the Internet is unavailable; no frontend resources or dependencies use a CDN.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import csv
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from . import __version__

ROOT = Path(__file__).resolve().parent
TASK_NAME = 'ServerNetworkAssist-Desktop'
WINDOWS = sys.platform == 'win32'


def local_data_dir() -> Path:
    if WINDOWS:
        import winreg
        # SSH can inherit stale USERPROFILE/LOCALAPPDATA values.
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders') as key:
            return Path(winreg.QueryValueEx(key, 'Local AppData')[0]) / 'ServerNetworkAssist'
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'server-network-assist'


def run(args, timeout=20):
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or '操作失败').strip()[:600])
    return result.stdout.strip()


def native_status() -> dict:
    if WINDOWS:
        return json.loads(run(['powershell.exe', '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'desktop_network.ps1')]))
    routes = json.loads(run(['ip', '-j', '-4', 'route', 'show', 'default']))
    units = json.loads(run(['systemctl', 'list-units', '--all', '--type=service',
        '--output=json', '--no-pager', 'wg-quick@*.service']))
    tunnels = []
    for unit in units:
        name = unit['unit'][len('wg-quick@'):-len('.service')]
        row = dict(name=name, active=unit.get('active') == 'active', addresses=[],
                   sent=0, received=0, handshake=0, endpoint='', telemetry=False)
        if row['active']:
            with contextlib.suppress(Exception):
                for line in run(['wg', 'show', name, 'transfer']).splitlines():
                    _, received, sent = line.split()
                    row['received'] += int(received)
                    row['sent'] += int(sent)
                row['handshake'] = max([int(line.split()[1]) for line in
                    run(['wg', 'show', name, 'latest-handshakes']).splitlines()] or [0])
                row['endpoint'] = ', '.join(line.split()[1] for line in
                    run(['wg', 'show', name, 'endpoints']).splitlines())
                row['telemetry'] = True
        tunnels.append(row)
    return dict(tunnels=tunnels, elevated=os.geteuid() == 0,
        routes=[dict(adapter=r.get('dev', ''), gateway=r.get('gateway', ''), metric=r.get('metric', 0)) for r in routes],
        proxy=dict(enabled=bool(urllib.request.getproxies()), server='', pac=False))


def connectivity(direct: bool) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}) if direct else urllib.request.ProxyHandler())
    started = time.monotonic()
    for url in ('https://www.baidu.com', 'https://connectivitycheck.gstatic.com/generate_204'):
        try:
            with opener.open(url, timeout=4) as response:
                if response.status in (200, 204) and response.url.startswith('https://'):
                    return dict(ok=True, milliseconds=round((time.monotonic()-started)*1000))
        except (OSError, urllib.error.URLError):
            pass
    return dict(ok=False, milliseconds=None)


def disable_proxy(data: Path) -> None:
    if not WINDOWS:
        raise ValueError('请在 Ubuntu 的系统代理设置中修改代理')
    import winreg
    path = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        old = {}
        for name in ('ProxyEnable', 'ProxyServer', 'ProxyOverride', 'AutoConfigURL'):
            with contextlib.suppress(FileNotFoundError):
                old[name] = winreg.QueryValueEx(key, name)
        # Keep an undo record before changing a user preference.
        backup = data / ('proxy-before-' + time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3) + '.json')
        backup.write_text(json.dumps(dict(key=path, values=old), ensure_ascii=False, indent=2), encoding='utf-8')
        winreg.SetValueEx(key, 'ProxyEnable', 0, winreg.REG_DWORD, 0)
    for option in (39, 37):
        ctypes.windll.wininet.InternetSetOptionW(None, option, None, 0)


def change_tunnel(name: str, action: str) -> None:
    state = native_status()
    if name not in {t['name'] for t in state['tunnels']}:
        raise ValueError('找不到此隧道；请刷新后重试')
    if not state['elevated']:
        raise ValueError('连接与断开需要管理员权限；请使用安装程序创建的桌面快捷方式')
    if WINDOWS:
        run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', str(ROOT / 'desktop_network.ps1'), action, name])
    else:
        run(['systemctl', 'start' if action == 'connect' else 'stop', '--', f'wg-quick@{name}.service'])


class Panel:
    def __init__(self, data: Path):
        self.data = data
        self.token = secrets.token_urlsafe(32)
        self.origin = ''
        self.lock = threading.Lock()
        self.cached = None
        self.cached_at = 0.0

    def status(self):
        with self.lock:
            if self.cached and time.monotonic() - self.cached_at < 5:
                return self.cached
            with ThreadPoolExecutor(max_workers=3) as pool:
                native = pool.submit(native_status)
                direct = pool.submit(connectivity, True)
                system = pool.submit(connectivity, False)
                result = native.result()
                result.update(direct=direct.result(), system=system.result(),
                    hostname=socket.gethostname(), platform='Windows' if WINDOWS else 'Ubuntu / Linux',
                    version=__version__, timestamp=int(time.time()))
            self.cached, self.cached_at = result, time.monotonic()
            return result


def handler_for(panel: Panel):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log authentication tokens or interfere with network controls.

        def reply(self, status, value, content_type='application/json; charset=utf-8'):
            body = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, dict) else value
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

        def authorized(self):
            return (self.headers.get('Host') == panel.origin.removeprefix('http://')
                and secrets.compare_digest(self.headers.get('X-Desktop-Token', ''), panel.token)
                and self.headers.get('Origin', panel.origin) == panel.origin)

        def do_GET(self):
            assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/desktop.css': ('desktop.css', 'text/css'),
                      '/framework7-bundle.min.css': ('framework7-bundle.min.css', 'text/css'),
                      '/framework7-default-theme.css': ('framework7-default-theme.css', 'text/css'),
                      '/THIRD_PARTY.md': ('THIRD_PARTY.md', 'text/plain; charset=utf-8'),
                      '/FRAMEWORK7-LICENSE.txt': ('FRAMEWORK7-LICENSE.txt', 'text/plain; charset=utf-8'),
                      '/desktop.js': ('desktop.js', 'text/javascript'),
                      '/icon.svg': ('icon.svg', 'image/svg+xml')}
            if self.path in assets:
                name, mime = assets[self.path]
                return self.reply(200, (ROOT / 'desktop_ui' / name).read_bytes(), mime)
            if not self.authorized():
                return self.reply(403, {'error': '请从桌面快捷方式打开面板'})
            if self.path == '/api/health':
                return self.reply(200, {'app': 'server-network-assist-desktop', 'pid': os.getpid(), 'version': __version__})
            if self.path == '/api/status':
                try:
                    return self.reply(200, panel.status())
                except Exception as exc:
                    return self.reply(500, {'error': str(exc)[:800]})
            self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.authorized() or self.headers.get('Origin') != panel.origin:
                return self.reply(403, {'error': '请求来源或令牌无效'})
            if self.path != '/api/action':
                return self.reply(404, {'error': 'Not found'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 1024:
                    raise ValueError('请求大小无效')
                value = json.loads(self.rfile.read(size))
                action = value.get('action')
                with panel.lock:
                    if action in ('connect', 'disconnect'):
                        change_tunnel(str(value.get('tunnel', '')), action)
                    elif action == 'disable-proxy':
                        disable_proxy(panel.data)
                    else:
                        raise ValueError('不支持的操作')
                    panel.cached = None
                self.reply(200, {'ok': True})
            except Exception as exc:
                self.reply(400, {'error': str(exc)[:800]})
    return Handler


@contextlib.contextmanager
def instance_lock(data: Path):
    file = (data / 'desktop.lock').open('a+b')
    file.write(b'0')
    file.flush()
    file.seek(0)
    try:
        if WINDOWS:
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        file.close()


def serve(data: Path):
    data.mkdir(parents=True, exist_ok=True, mode=0o777 if WINDOWS else 0o700)
    if WINDOWS:
        # Elevated processes may create files owned by the Administrators group.
        # Grant the actual user's SID, so the unelevated desktop launcher can
        # read the instance token without making it available to other users.
        sid = next(csv.reader([run(['whoami.exe', '/user', '/fo', 'csv', '/nh'])]))[1]
        run(['icacls.exe', str(data), '/inheritance:r', '/grant:r',
             f'*{sid}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'])
    with instance_lock(data):
        panel = Panel(data)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(panel))
        server.daemon_threads = True
        panel.origin = f'http://127.0.0.1:{server.server_port}'
        state = data / 'desktop-instance.json'
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(port=server.server_port, token=panel.token, pid=os.getpid())), encoding='utf-8')
        if not WINDOWS:
            temporary.chmod(0o600)
        temporary.replace(state)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            state.unlink(missing_ok=True)


def running_instance(data: Path):
    try:
        state = json.loads((data / 'desktop-instance.json').read_text(encoding='utf-8'))
        port = int(state['port'])
        if not 1 <= port <= 65535:
            return None
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/health',
            headers={'X-Desktop-Token': state['token']})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=1) as response:
            health = json.load(response)
            if health.get('app') == 'server-network-assist-desktop' and health.get('pid') == state['pid']:
                return state
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        pass
    return None


def open_window(data: Path, state):
    url = f"http://127.0.0.1:{state['port']}/#" + state['token']
    if WINDOWS:
        choices = [Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
                   Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe']
    else:
        choices = [Path(p) for n in ('microsoft-edge', 'google-chrome', 'chromium', 'chromium-browser') if (p := shutil.which(n))]
    browser = next((p for p in choices if p.is_file()), None)
    if browser:
        subprocess.Popen([str(browser), '--app=' + url, '--window-size=1180,820',
            '--user-data-dir=' + str(data / 'browser-profile'), '--no-first-run'],
            creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description='Server Network Assist desktop panel')
    parser.add_argument('--serve', action='store_true', help='run the loopback backend without opening a window')
    parser.add_argument('--data', type=Path)
    args = parser.parse_args()
    data = args.data or local_data_dir()
    if args.serve:
        return serve(data)
    state = running_instance(data)
    if not state:
        started_task = False
        if WINDOWS:
            with contextlib.suppress(Exception):
                run(['schtasks.exe', '/Run', '/TN', TASK_NAME])
                started_task = True
        if not started_task:
            python = Path(sys.executable)
            if WINDOWS and python.with_name('pythonw.exe').exists():
                python = python.with_name('pythonw.exe')
            subprocess.Popen([str(python), '-m', 'server_network_assist.desktop', '--serve', '--data', str(data)],
                creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            state = running_instance(data)
            if state:
                break
            time.sleep(0.25)
        if not state:
            raise RuntimeError('面板后台启动失败，请重新运行桌面安装程序')
    open_window(data, state)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        if WINDOWS and not ('--serve' in sys.argv):
            ctypes.windll.user32.MessageBoxW(None, str(exc), 'Server Network Assist', 0x10)
        else:
            raise
