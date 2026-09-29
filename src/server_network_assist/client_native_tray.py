"""Existing pystray controls and the project's existing desktop icon."""
from pathlib import Path
import threading
import queue


class NativeTray:
    def __init__(self, window, panel, close):
        import pystray
        from PIL import Image
        self.window, self.panel = window, panel
        self.ready = threading.Event()
        self.notifications = queue.Queue(maxsize=64)
        self.stopped = threading.Event()
        menu = pystray.Menu(
            pystray.MenuItem('打开客户端', lambda icon, item: self.show(), default=True),
            pystray.MenuItem('退出入网 · 保持客户端打开', lambda icon, item: self.leave()),
            pystray.MenuItem('恢复原网络并退出程序', lambda icon, item: self.quit(close)))
        self.icon = pystray.Icon('server-network-assist-customer',
            Image.open(Path(__file__).with_name('desktop_ui') / 'icon.ico'), '入网客户端 · 未入网', menu)

    def start(self):
        def setup(icon):
            icon.visible = True
            self.ready.set()
        threading.Thread(target=self.icon.run, kwargs={'setup': setup}, daemon=True, name='customer-tray').start()
        if not self.ready.wait(8):
            raise RuntimeError('托盘未能启动，客户端窗口将保持可见')
        threading.Thread(target=self.notify_worker, daemon=True, name='customer-notifications').start()

    def notify_worker(self):
        while not self.stopped.is_set():
            try:
                value = self.notifications.get(timeout=.5)
            except queue.Empty:
                continue
            try:
                self._display_event(value)
            except Exception:
                pass

    def show(self):
        self.window.show()
        self.window.restore()

    def quit(self, close):
        if close() is not False:
            self.window.destroy()

    def minimize(self):
        if self.ready.is_set():
            self.window.hide()

    def leave(self):
        try:
            with self.panel.lock:
                self.panel.leave_network()
            self.panel.publish_event('left_network', '已退出入网，原网络已恢复；客户端和订阅保留。')
        except Exception:
            self.panel.publish_event('recovery_failed', '原网络恢复尚未完成，请打开客户端重试退出入网。', True)

    def event(self, value):
        # Never synchronously Invoke the UI while the backend owns panel.lock.
        try:
            self.notifications.put_nowait(value)
        except queue.Full:
            try:
                self.notifications.get_nowait()
            except queue.Empty:
                pass
            try:
                self.notifications.put_nowait(value)
            except queue.Full:
                pass

    def _display_event(self, value):
        message = value['message'].replace('借网', '入网').replace('退出组网', '退出入网')
        self.icon.title = '入网客户端 · ' + message[:80]
        if value.get('error') or value['code'] in ('attachment_lost', 'network_changed', 'monitor_failed', 'recovery_failed'):
            self.show()  # Alerts remain visible even if Windows suppresses tray balloons.
        try:
            self.icon.notify(message, '入网客户端')
        except Exception:
            pass

    def stop(self):
        self.stopped.set()
        self.icon.stop()
