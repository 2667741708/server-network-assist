"""Small remote Chrome CDP bridge used through an authenticated SSH process."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


SIZE = (1280, 800)


def browser_path():
    choices = [shutil.which(name) for name in ('google-chrome', 'chromium', 'chromium-browser', 'microsoft-edge')]
    if os.name == 'nt':
        choices += [
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        ]
    return next((value for value in choices if value and Path(value).is_file()), None)


def free_port():
    with socket.socket() as value:
        value.bind(('127.0.0.1', 0))
        return value.getsockname()[1]


class WebSocket:
    def __init__(self, url):
        parsed = urlsplit(url)
        self.socket = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=20)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        path = parsed.path + (('?' + parsed.query) if parsed.query else '')
        request = (f'GET {path} HTTP/1.1\r\nHost: {parsed.netloc}\r\nUpgrade: websocket\r\n'
                   f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.socket.sendall(request.encode())
        response = b''
        while b'\r\n\r\n' not in response:
            response += self.socket.recv(4096)
        if b' 101 ' not in response.split(b'\r\n', 1)[0]:
            raise RuntimeError('Chrome rejected the CDP websocket')
        self.lock = threading.Lock()

    def send(self, value):
        payload = json.dumps(value, ensure_ascii=False).encode()
        mask = secrets.token_bytes(4)
        length = len(payload)
        header = bytearray([0x81])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.extend([0xFE])
            header.extend(struct.pack('!H', length))
        else:
            header.extend([0xFF])
            header.extend(struct.pack('!Q', length))
        body = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        with self.lock:
            self.socket.sendall(bytes(header) + mask + body)

    def _read(self, count):
        value = b''
        while len(value) < count:
            part = self.socket.recv(count - len(value))
            if not part:
                raise EOFError
            value += part
        return value

    def receive(self):
        first, second = self._read(2)
        opcode, length = first & 0x0F, second & 0x7F
        if length == 126:
            length = struct.unpack('!H', self._read(2))[0]
        elif length == 127:
            length = struct.unpack('!Q', self._read(8))[0]
        masked = bool(second & 0x80)
        mask = self._read(4) if masked else b''
        payload = self._read(length)
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if opcode == 8:
            raise EOFError
        if opcode == 9:
            return None
        return json.loads(payload.decode()) if opcode == 1 else None


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def valid_url(value):
    parsed = urlsplit(str(value))
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('Only ordinary HTTP(S) browser addresses are supported')
    return str(value)[:4096]


def main():
    initial = valid_url(sys.argv[1] if len(sys.argv) > 1 else 'https://chatgpt.com/')
    executable = browser_path()
    if not executable:
        raise RuntimeError('Chrome or Edge was not found')
    port = free_port()
    profile = Path.home() / '.server-network-assist' / 'browser-profile'
    profile.mkdir(parents=True, exist_ok=True)
    arguments = [executable, '--headless=new', f'--remote-debugging-port={port}',
                 '--remote-debugging-address=127.0.0.1', f'--user-data-dir={profile}',
                 f'--window-size={SIZE[0]},{SIZE[1]}', '--no-first-run', '--no-default-browser-check',
                 '--disable-background-networking', 'about:blank']
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    process = subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=flags)
    try:
        target = None
        for _ in range(80):
            try:
                request = Request(f'http://127.0.0.1:{port}/json/new?{quote(initial, safe="")}', method='PUT')
                target = json.loads(urlopen(request, timeout=2).read())
                break
            except Exception:
                time.sleep(.1)
        if not target:
            raise RuntimeError('Chrome remote debugging did not start')
        channel = WebSocket(target['webSocketDebuggerUrl'])
        counter = 0

        def command(method, params=None):
            nonlocal counter
            counter += 1
            channel.send({'id': counter, 'method': method, 'params': params or {}})

        command('Page.enable')
        command('Runtime.enable')
        command('Page.startScreencast', {'format': 'jpeg', 'quality': 78,
                                        'maxWidth': SIZE[0], 'maxHeight': SIZE[1], 'everyNthFrame': 1})
        emit({'type': 'ready', 'width': SIZE[0], 'height': SIZE[1], 'url': initial})

        def receive():
            while True:
                event = channel.receive()
                if not event:
                    continue
                if event.get('method') == 'Page.screencastFrame':
                    params = event['params']
                    emit({'type': 'frame', 'data': params['data'], 'metadata': params.get('metadata', {})})
                    command('Page.screencastFrameAck', {'sessionId': params['sessionId']})
                elif event.get('method') == 'Page.frameNavigated':
                    frame = event.get('params', {}).get('frame', {})
                    if not frame.get('parentId'):
                        emit({'type': 'location', 'url': frame.get('url', '')})

        threading.Thread(target=receive, daemon=True).start()
        for line in sys.stdin:
            event = json.loads(line)
            kind = event.get('type')
            if kind == 'navigate':
                command('Page.navigate', {'url': valid_url(event.get('url', ''))})
            elif kind == 'click':
                x, y = float(event.get('x', 0)), float(event.get('y', 0))
                command('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': x, 'y': y,
                                                     'button': 'left', 'clickCount': 1})
                command('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': x, 'y': y,
                                                     'button': 'left', 'clickCount': 1})
            elif kind == 'text':
                command('Input.insertText', {'text': str(event.get('text', ''))[:8192]})
            elif kind == 'key':
                key = str(event.get('key', ''))[:40]
                command('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': key})
                command('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': key})
    finally:
        process.terminate()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        emit({'type': 'error', 'message': str(error)[:1000]})
        raise
