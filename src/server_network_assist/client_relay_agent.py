"""One-shot control-plane synchronization for a Linux customer relay."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import urllib.request
from urllib.parse import urlsplit

from .client_relay import RelayManager


class RelayControlClient:
    def __init__(self, base_url: str, token: str, opener=None, relay_id: str = ''):
        parts = urlsplit(base_url.rstrip('/'))
        if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password:
            raise ValueError('control URL must be HTTPS')
        self.base = base_url.rstrip('/')
        self.host = parts.hostname
        self.token = token.strip()
        self.relay_id = relay_id
        if len(self.token) < 24:
            raise ValueError('relay token is invalid')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise RuntimeError('control plane redirects are not allowed')
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def request(self, path: str, value=None):
        body = json.dumps(value, separators=(',', ':')).encode() if value is not None else None
        request = urllib.request.Request(self.base + path, data=body,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json',
                     'X-Relay-ID': self.relay_id})
        with self.opener.open(request, timeout=20) as response:
            final = urlsplit(response.url)
            if final.scheme != 'https' or final.hostname != self.host:
                raise RuntimeError('control plane redirected to another origin')
            data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise RuntimeError('control response is too large')
        result = json.loads(data)
        if not isinstance(result, dict):
            raise RuntimeError('control response is invalid')
        return result


def sync(manager: RelayManager, control: RelayControlClient, cursor_path: Path, default_interface: str = '') -> dict:
    try:
        cursor = max(0, int(cursor_path.read_text(encoding='ascii')))
    except (OSError, ValueError):
        cursor = 0
    desired = control.request('/relay/v1/reconcile?since=' + str(cursor))
    policies = desired.get('policies')
    if not isinstance(policies, list):
        raise RuntimeError('control response has no policy list')
    interfaces = {row['interface'] for row in policies if isinstance(row, dict) and isinstance(row.get('interface'), str)}
    if default_interface:
        interfaces.add(default_interface)
    current = manager.status()
    interfaces.update(record['policy']['interface'] for record in current['peers'].values()
                      if record.get('status') == 'active')
    for interface in sorted(interfaces):
        manager.measure(interface)
    measured = manager.status()
    for peer_id, record in measured['peers'].items():
        if record.get('accounted_received') is None or record.get('accounted_sent') is None:
            continue
        control.request('/relay/v1/usage', {'lease_id': peer_id,
            'report_id': f"{int(time.time())}-{record['accounted_received']}-{record['accounted_sent']}",
            'rx_total': record['accounted_received'], 'tx_total': record['accounted_sent']})
    result = manager.reconcile(policies)
    cursor_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = cursor_path.with_suffix('.tmp')
    temporary.write_text(str(int(desired.get('generated_at', time.time()))), encoding='ascii')
    temporary.chmod(0o600)
    temporary.replace(cursor_path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('/var/lib/server-network-assist-relay'))
    parser.add_argument('--control-url', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--interface', default='wg-customer')
    parser.add_argument('--relay-id', required=True)
    args = parser.parse_args()
    token = args.token_file.read_text(encoding='utf-8').strip()
    result = sync(RelayManager(args.data), RelayControlClient(args.control_url, token, relay_id=args.relay_id), args.data / 'cursor', args.interface)
    print(json.dumps({'ok': True, 'peers': len(result['peers'])}))


if __name__ == '__main__':
    main()
