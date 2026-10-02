"""Temporary management-only CONNECT relay for the explicitly requested D321 CLI.

Only the existing D321 management peer may reach chatgpt.com:443. No TLS
interception, credential logging, global proxy changes or persistent service.
"""
import select
import socket
import socketserver
import time

DEADLINE = time.monotonic() + 900


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if self.client_address[0] != '10.203.49.3':
            return
        self.request.settimeout(10)
        header = b''
        try:
            while b'\r\n\r\n' not in header and len(header) < 65536:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                header += chunk
            first = header.split(b'\r\n', 1)[0].split()
            if len(first) != 3 or first[:2] != [b'CONNECT', b'chatgpt.com:443']:
                self.request.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
                return
            with socket.create_connection(('127.0.0.1', 7897), timeout=10) as upstream:
                upstream.sendall(header)
                self.request.settimeout(None)
                upstream.settimeout(None)
                while time.monotonic() < DEADLINE:
                    ready, _, _ = select.select([self.request, upstream], [], [], 1)
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        target = upstream if source is self.request else self.request
                        target.sendall(data)
        except OSError:
            return


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True


if __name__ == '__main__':
    with Server(('10.203.49.1', 19097), Handler) as server:
        server.timeout = 0.5
        while time.monotonic() < DEADLINE:
            server.handle_request()
