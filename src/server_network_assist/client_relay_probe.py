"""Private WireGuard challenge listener for a remote commercial relay.

The listener accepts only peers in the configured customer subnet. It sends
the observed socket addresses to the authenticated control plane, which checks
the short probe lease before returning a challenge proof. It never installs a
route or handles customer Internet traffic.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import secrets
import threading

from .client_relay_agent import RelayControlClient


class ProbeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, control, customer_network):
        super().__init__(address, ProbeHandler)
        self.control = control
        self.customer_network = ipaddress.IPv4Network(customer_network, strict=False)


class ProbeHandler(BaseHTTPRequestHandler):
    server: ProbeHTTPServer

    def log_message(self, _format, *_args):
        # Probe IDs and peer addresses must not leak through access logging.
        pass

    def _reply(self, status, value):
        data = json.dumps(value, separators=(',', ':')).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status, code, message):
        self._reply(status, {'protocol_version': 1, 'error_code': code,
            'component': 'source_relay', 'phase': 'challenge', 'error': message,
            'retryable': status >= 500,
            'next_action': '检查检测租约和中继状态后重试',
            'correlation_id': secrets.token_hex(12)})

    def do_POST(self):
        self.connection.settimeout(3)
        if self.path != '/client/v1/tunnel-probe':
            self._error(404, 'PROBE_PATH_UNKNOWN', '检测接口不存在')
            return
        try:
            peer = ipaddress.IPv4Address(self.client_address[0])
            local = ipaddress.IPv4Address(self.connection.getsockname()[0])
        except (ValueError, TypeError):
            self._error(403, 'PROBE_TUNNEL_REQUIRED', '检测请求必须经过授权隧道')
            return
        if peer not in self.server.customer_network or str(local) != self.server.server_address[0]:
            self._error(403, 'PROBE_TUNNEL_REQUIRED', '检测请求必须经过授权隧道')
            return
        try:
            size = int(self.headers.get('Content-Length', ''))
            if not 0 < size <= 1024:
                raise ValueError()
            value = json.loads(self.rfile.read(size))
            probe_id = self.headers.get('X-Probe-ID', '')
            device_id = self.headers.get('X-Device-ID', '')
            nonce = value.get('nonce') if isinstance(value, dict) else None
            if (not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{32}', nonce) or
                    not re.fullmatch(r'[0-9a-f]{64}', probe_id) or
                    not 1 <= len(device_id) <= 256):
                raise ValueError()
        except (OSError, ValueError, UnicodeError):
            self._error(400, 'PROBE_CHALLENGE_INVALID', '检测挑战无效')
            return
        try:
            result = self.server.control.request('/relay/v1/tunnel-probe', {
                'probe_id': probe_id, 'device_id': device_id, 'nonce': nonce,
                'peer_address': str(peer), 'local_address': str(local)}, timeout=3)
            if (result.get('ok') is not True or result.get('nonce') != nonce or
                    not isinstance(result.get('lease_id'), str) or
                    not isinstance(result.get('proof'), str) or
                    not re.fullmatch(r'[0-9a-f]{64}', result['proof'])):
                raise ValueError('invalid control response')
        except Exception:
            self._error(503, 'PROBE_CONTROL_UNAVAILABLE', '中继未能确认检测租约')
            return
        self._reply(200, {'ok': True, 'lease_id': result['lease_id'],
                          'nonce': nonce, 'proof': result['proof']})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind-address', required=True, action='append',
                        help='private relay address; repeat for each v2 challenge alias')
    parser.add_argument('--port', type=int, default=9183)
    parser.add_argument('--customer-subnet', required=True)
    parser.add_argument('--control-url', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--relay-id', required=True)
    args = parser.parse_args(argv)
    addresses = [ipaddress.IPv4Address(item) for item in args.bind_address]
    if len(addresses) != len(set(addresses)):
        parser.error('duplicate probe listener address')
    network = ipaddress.IPv4Network(args.customer_subnet, strict=False)
    private_ranges = (ipaddress.IPv4Network('10.0.0.0/8'),
                      ipaddress.IPv4Network('172.16.0.0/12'),
                      ipaddress.IPv4Network('192.168.0.0/16'))
    if (not all(any(address in value for value in private_ranges)
                for address in addresses) or args.relay_id == '*'):
        parser.error('the probe listener requires a dedicated private relay address')
    if not 1 <= args.port <= 65535:
        parser.error('invalid probe port')
    token = args.token_file.read_text(encoding='utf-8').strip()
    control = RelayControlClient(args.control_url, token, relay_id=args.relay_id)
    with ExitStack() as stack:
        # Bind every address before serving any of them.  A missing interface
        # alias fails startup instead of leaving partial readiness coverage.
        servers = [stack.enter_context(ProbeHTTPServer((str(address), args.port),
                    control, str(network))) for address in addresses]
        threads = [threading.Thread(target=server.serve_forever,
                    kwargs={'poll_interval': .2}, daemon=True) for server in servers]
        for thread in threads:
            thread.start()
        try:
            threads[0].join()
        finally:
            for server in servers:
                server.shutdown()
            for thread in threads:
                thread.join(timeout=3)


if __name__ == '__main__':
    main()
