"""Optional native pystray menu. Its lifecycle never controls network services."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.request

STATE = 'desktop-tray.json'


def background_status(data: Path) -> dict:
    available = False
    with contextlib.suppress(Exception):
        available = all(importlib.util.find_spec(name) for name in ('pystray', 'PIL'))
    if sys.platform != 'win32' and not (os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY')):
        available = False
    result = dict(running=True, tray_available=bool(available), tray_running=False,
                  notifications_enabled=False)
    with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
        state = json.loads((data / STATE).read_text(encoding='utf-8'))
        if 0 <= time.time() - float(state['updated']) < 45:
            result.update(tray_running=bool(state.get('running')),
                          notifications_enabled=bool(state.get('notifications_enabled')))
    return result


def start_tray(data: Path) -> None:
    """Best effort; a missing desktop/session must never stop the panel."""
    with contextlib.suppress(Exception):
        status = background_status(data)
        if not status['tray_available'] or status['tray_running']:
            return
        python = Path(sys.executable)
        if sys.platform == 'win32' and python.with_name('pythonw.exe').exists():
            python = python.with_name('pythonw.exe')
        subprocess.Popen([str(python), '-m', __name__, '--data', str(data)],
                         creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def connection_label(status: dict) -> str:
    direct, system = status.get('direct', {}).get('ok'), status.get('system', {}).get('ok')
    if direct is True and system is True:
        return '网络连接正常'
    if direct is True:
        return '直连正常，应用连接异常'
    if system is True:
        return '应用连接正常，直连不可用'
    return '网络连接待检查'


@contextlib.contextmanager
def tray_lock(data: Path):
    with (data / 'desktop-tray.lock').open('a+b') as lock:
        lock.write(b'0')
        lock.flush()
        lock.seek(0)
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def serve_tray(data: Path):
    import pystray
    from PIL import Image
    from .desktop import ROOT, running_instance, open_window

    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tray_lock(data):
        stopped = threading.Event()
        current = {'label': '正在检查网络', 'notify': True}
        state_path = data / STATE

        def write_state(running=True):
            # Optional telemetry failures cannot interrupt tray/network operations.
            with contextlib.suppress(OSError):
                temporary = state_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(dict(pid=os.getpid(), updated=time.time(),
                    running=running, notifications_enabled=running and current['notify'] and icon.HAS_NOTIFICATION)),
                    encoding='utf-8')
                temporary.replace(state_path)

        def open_panel(_icon=None, _item=None):
            state = running_instance(data)
            if state:
                open_window(data, state)

        def toggle_notify(_icon, _item):
            current['notify'] = not current['notify']
            write_state()
            icon.update_menu()

        def quit_tray(_icon, _item):
            stopped.set()
            icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem('打开网络助手', open_panel, default=True),
            pystray.MenuItem(lambda _item: current['label'], None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('网络变化通知', toggle_notify,
                             checked=lambda _item: current['notify'], enabled=pystray.Icon.HAS_NOTIFICATION),
            pystray.MenuItem('退出托盘（保留网络连接）', quit_tray))
        icon = pystray.Icon('server-network-assist', Image.open(ROOT / 'desktop_ui/icon.ico'),
                            '服务器网络助手', menu)

        def poll():
            previous = None
            while not stopped.is_set():
                label = '面板后台未响应'
                try:
                    state = running_instance(data)
                    if state:
                        request = urllib.request.Request(f"http://127.0.0.1:{state['port']}/api/status",
                            headers={'X-Desktop-Token': state['token']})
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        with opener.open(request, timeout=20) as response:
                            label = connection_label(json.load(response))
                except Exception:
                    pass
                current['label'] = label
                with contextlib.suppress(Exception):
                    icon.title = '服务器网络助手 · ' + label
                    icon.update_menu()
                    if previous is not None and previous != label and current['notify'] and icon.HAS_NOTIFICATION:
                        icon.notify(label, '服务器网络助手')
                previous = label
                write_state()
                stopped.wait(10)

        def setup(_icon):
            _icon.visible = True
            write_state()
            threading.Thread(target=poll, daemon=True).start()

        try:
            icon.run(setup=setup)
        finally:
            stopped.set()
            write_state(False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True, type=Path)
    args = parser.parse_args()
    # Missing AppIndicator/display, a duplicate icon or logging failure is optional.
    with contextlib.suppress(Exception):
        serve_tray(args.data)


if __name__ == '__main__':
    main()
