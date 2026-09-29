"""Native window around the existing Framework7 iOS client, with exit recovery."""
import argparse
import ctypes
import json
import faulthandler
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import quote
import webbrowser

from .client import ClientPanel, main as client_main, serve
from .desktop import local_data_dir
from .client_paths import customer_data


def release_native_console():
    """Hide only a private launcher console; never hide an operator shell."""
    if os.name != 'nt':
        return
    from ctypes import wintypes
    kernel = ctypes.windll.kernel32
    kernel.GetConsoleWindow.restype = wintypes.HWND
    pids = (wintypes.DWORD * 32)()
    count = kernel.GetConsoleProcessList(pids, len(pids))
    handle = kernel.GetConsoleWindow()
    # A frozen one-file private console contains launcher + Python child.
    if getattr(sys, 'frozen', False) and handle and 0 < count <= 2:
        ctypes.windll.user32.ShowWindow(ctypes.c_void_p(handle), 0)
    kernel.FreeConsole()


def notify_guard_recovery():
    def show():
        try:
            if os.name == 'nt':
                ctypes.windll.user32.MessageBoxW(None,
                    '客户端意外退出，原网络恢复尚未完成。恢复程序正在持续重试；请勿将当前状态当作已退网。',
                    '入网客户端 · 恢复未完成', 0x10010)
        except Exception:
            pass  # An optional warning cannot block the independent retry loop.
    threading.Thread(target=show, daemon=True).start()


def record_window_shown(panel, log):
    """Optional diagnostics must not prevent tray setup or visible warnings."""
    try:
        faulthandler.cancel_dump_traceback_later()
        if log:
            log.write('native_window_shown\n')
            log.flush()
    except Exception:
        panel.publish_event('diagnostic_log_failed', '诊断日志不可写；窗口、托盘及网络恢复将继续运行。', False)


def native_process_alive(pid):
    if os.name != 'nt':
        return False
    from ctypes import wintypes
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    handle = kernel.OpenProcess(0x101000, False, pid)
    if not handle:
        return False
    try:
        if kernel.WaitForSingleObject(handle, 0) != 0x102:
            return False
        image = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            return False
        return os.path.normcase(os.path.realpath(image.value)) == os.path.normcase(os.path.realpath(sys.executable))
    finally:
        kernel.CloseHandle(handle)


class GuardNoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def existing_panel(data):
    """Authenticate a display target; never authorize network cleanup with this."""
    try:
        info = json.loads((Path(data) / 'client-instance.json').read_text(encoding='utf8'))
        port, pid, token = info['port'], info['pid'], info['token']
        if type(port) is not int or not 0 < port < 65536 or type(pid) is not int or pid <= 0 or not isinstance(token, str) or not token:
            return None
        base = f'http://127.0.0.1:{port}'
        request = urllib.request.Request(base + '/api/health', headers={'X-Client-Token': token})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), GuardNoRedirect())
        with opener.open(request, timeout=2) as response:
            if response.geturl() != request.full_url:
                return None
            health = json.loads(response.read(4096))
        if health.get('app') != 'server-network-assist-client':
            return None
        if 'pid' in health and (type(health['pid']) is not int or health['pid'] != pid):
            return None
        return info, base, health
    except Exception:
        return None


def activate_native_window(pid):
    """Show only this backend's exact client window, including old GUI builds."""
    if os.name != 'nt':
        return False
    from ctypes import wintypes
    user = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    found = []
    def visit(handle, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid:
            title, kind = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(handle, title, len(title))
            user.GetClassNameW(handle, kind, len(kind))
            if title.value in ('入网客户端', '借网客户端') and kind.value.startswith('WindowsForms10.'):
                found.append(handle)
        return True
    user.EnumWindows(callback_type(visit), 0)
    for handle in found:
        user.ShowWindow(handle, 9)
        user.ShowWindow(handle, 5)
        user.SetForegroundWindow(handle)
        if user.IsWindowVisible(handle):
            return True
    return False


def show_existing_panel(data):
    target = existing_panel(data)
    if target is None:
        return False
    info, base, health = target
    if health.get('native_ui') is True:
        try:
            request = urllib.request.Request(base + '/api/ui/show', data=b'{}',
                headers={'X-Client-Token': info['token'], 'Origin': base, 'Content-Type': 'application/json'})
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), GuardNoRedirect()).open(request, timeout=3) as response:
                value = json.loads(response.read(4096))
            if value.get('shown') is True:
                return True
        except Exception:
            pass
    try:
        if activate_native_window(info['pid']):
            return True
    except Exception:
        pass
    # Legacy Python web backends have no tray. Open them rather than stopping
    # or replacing them, which could change the user's current network.
    if not webbrowser.open(base + '/#token=' + quote(info['token'], safe='')):
        raise RuntimeError('现有客户后台正在运行，但浏览器未能打开面板；未调整网络配置')
    return True


def confirmed_replacement(info, parent):
    try:
        pid, port, token = info['pid'], info['port'], info['token']
        if type(pid) is not int or pid <= 0 or pid == parent or type(port) is not int or not 0 < port < 65536 or not isinstance(token, str) or not token:
            return False
        if not native_process_alive(pid):
            return False
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/health', headers={'X-Client-Token': token})
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), GuardNoRedirect()).open(request, timeout=1) as response:
            if response.geturl() != request.full_url:
                return False
            value = json.loads(response.read(4096))
        return value.get('app') == 'server-network-assist-client' and type(value.get('pid')) is int and value['pid'] == pid
    except Exception:
        return False


def recover_guard(parent, data):
    from .client_ownership import CustomerLock
    data = Path(data)
    instance = data / 'client-instance.json'
    diagnostic = data / ('guard-recovery-' + str(parent) + '.json')

    def replacement():
        try:
            return confirmed_replacement(json.loads(instance.read_text()), parent)
        except (OSError, ValueError, KeyError, TypeError):
            return False  # UI metadata must not prevent owned network cleanup.

    attempt = 0
    while True:
        # This unlocked hint can only stop us; it never authorizes mutation.
        if replacement():
            return
        try:
            with CustomerLock(data, timeout=5):
                if replacement():
                    return
                ClientPanel(data).leave_network()
            try:
                diagnostic.unlink(missing_ok=True)
            except OSError:
                pass
            return
        except Exception:
            if replacement():
                return
            attempt += 1
            try:
                diagnostic.write_text(json.dumps({'recovering': True, 'attempt': attempt}), encoding='utf-8')
            except OSError:
                pass
            if attempt == 1:
                notify_guard_recovery()
            time.sleep(min(30, 2 ** min(attempt, 4)))


def guard(parent, data):
    from ctypes import wintypes
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100001, False, parent)
    try:
        if handle:
            while True:
                result = kernel.WaitForSingleObject(handle, 5000)
                if result == 0:
                    break
                if result != 0x102:
                    break
                try:
                    from .client_tunnel import recovery_reason
                    reason = recovery_reason(data)
                except Exception:
                    reason = None
                if reason:
                    if kernel.TerminateProcess(handle, 1):
                        kernel.WaitForSingleObject(handle, 10000)
                        break
        recover_guard(parent, data)
    finally:
        if handle:
            kernel.CloseHandle(handle)


def windowed_notice(message, error=False):
    if os.name == 'nt' and getattr(sys, 'frozen', False):
        ctypes.windll.user32.MessageBoxW(None, message, '入网客户端', 0x10 if error else 0x40)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', '--data-dir', dest='data', type=Path, default=customer_data())
    parser.add_argument('--guard', type=int)
    parser.add_argument('--disconnect', '--leave-network', dest='disconnect', action='store_true')
    parser.add_argument('--show', action='store_true', help='仅打开现有面板，不启动或修改网络')
    args = parser.parse_args()
    if args.guard:
        release_native_console()
        guard(args.guard, args.data)
        return
    if args.disconnect:
        sys.argv = [sys.argv[0], '--disconnect', '--data', str(args.data)]
        try:
            client_main()
        except (Exception, SystemExit):
            windowed_notice('退网未完成，请在客户端查看提醒并重试退出入网。', True)
            raise
        windowed_notice('退网命令已完成，客户端拥有的网络配置已恢复。')
        return
    if show_existing_panel(args.data):
        return
    if args.show:
        windowed_notice('没有可打开的客户后台；未启动服务或修改网络配置。', True)
        raise SystemExit('没有可打开的客户后台；未启动服务或修改网络配置')
    from .client_ownership import CustomerLock
    lifecycle_started = False
    try:
        with CustomerLock(args.data, timeout=1):
            lifecycle_started = True
            run_native(args)
    except Exception as exc:
        if not lifecycle_started:
            # A busy owner (including its recovery guard) must never cause a
            # second launcher to start network recovery or a new backend.
            for _ in range(5):
                if show_existing_panel(args.data):
                    return
                time.sleep(.3)
            if os.name == 'nt':
                ctypes.windll.user32.MessageBoxW(None,
                    '客户端正在启动或恢复中，暂时无法打开窗口，请稍后再次打开。未启动另一服务或修改网络配置。\n' + str(exc)[:500],
                    '入网客户端', 0x40)
            raise SystemExit('现有客户端尚未就绪；未启动另一服务或修改网络配置') from None
        if show_existing_panel(args.data):
            return  # Another launch may have become ready while we waited.
        if os.name == 'nt':
            # The lifecycle lock is released. Recover before a modal can keep
            # this parent alive and prevent its crash guard from taking over.
            recover_guard(os.getpid(), args.data)
            ctypes.windll.user32.MessageBoxW(None, '客户端未能启动或完成恢复，请重试：' + str(exc)[:500], '入网客户端', 0x10)
        raise


def run_native(args):
    if os.name == 'nt' and not ctypes.windll.shell32.IsUserAnAdmin():
        raise SystemExit('请以管理员身份打开入网客户端')
    if os.name == 'nt':
        release_native_console()
        args.data.mkdir(parents=True, exist_ok=True)
        subprocess.run(['icacls.exe', str(args.data), '/inheritance:r', '/grant:r',
            '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'],
            capture_output=True, check=True, creationflags=0x08000000)
    if show_existing_panel(args.data):
        return
    # Arm recovery before startup cleanup/imports can fail, not after GUI setup.
    if os.name == 'nt' and getattr(sys, 'frozen', False):
        subprocess.Popen([sys.executable, '--guard', str(os.getpid()), '--data', str(args.data)],
                         creationflags=0x08000000,
                         env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'})
    ClientPanel(args.data).leave_network()  # Recover a crashed previous session first.
    import webview
    log = None
    try:
        log = (args.data / ('native-' + str(os.getpid()) + '.log')).open('w', encoding='utf-8')
        faulthandler.dump_traceback_later(30, file=log)
    except Exception:
        pass  # Auxiliary diagnostics cannot block borrowing/recovery.
    available = threading.Event()
    context = {}

    def ready(panel, server):
        context.update(panel=panel, server=server)
        available.set()

    backend = threading.Thread(target=serve, args=(args.data, ready), daemon=True)
    backend.start()
    if not available.wait(15):
        raise RuntimeError('客户端后台未能启动')
    panel, server = context['panel'], context['server']
    window = webview.create_window('入网客户端', panel.origin + '/#token=' + panel.token,
                                   width=580, height=820, min_size=(420, 560))
    def reveal():
        window.show()
        window.restore()
        return True
    panel.ui_activate = reveal
    closed = threading.Event()
    tray = None

    def closing():
        if closed.is_set():
            return
        try:
            with panel.lock:
                panel.leave_network()
        except Exception as exc:
            ctypes.windll.user32.MessageBoxW(None, '恢复未完成，请重试退出：' + str(exc), '入网客户端', 0x10)
            return False
        server.shutdown()
        backend.join(10)
        closed.set()

    window.events.closing += closing
    def shown():
        nonlocal tray
        record_window_shown(panel, log)
        if os.name == 'nt' and tray is None:
            from .client_native_tray import NativeTray
            try:
                tray = NativeTray(window, panel, closing)
                tray.start()
                panel.event_callbacks.append(tray.event)
                window.events.minimized += tray.minimize
            except Exception:
                panel.publish_event('tray_failed', '托盘不可用，客户端窗口将保持可见；关闭仍会恢复网络。', True)
    window.events.shown += shown
    try:
        webview.start(gui='edgechromium' if os.name == 'nt' else None)
    finally:
        panel.ui_activate = None
        if tray:
            tray.stop()
        with panel.lock:
            panel.leave_network()
        server.shutdown()
        backend.join(10)


if __name__ == '__main__':
    main()
