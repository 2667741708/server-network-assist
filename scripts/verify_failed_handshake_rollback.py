#!/usr/bin/env python3
"""D408-only: unreachable relay test with one TEST-NET route, never the public default."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from server_network_assist import client_tunnel


def run(*argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=10)


def main():
    assert os.geteuid() == 0 and run('hostname').stdout.strip() == 'd408-4090'
    real = json.loads(Path('/var/lib/server-network-assist-client/1000/customer-active-line.json').read_text())
    before = run('systemctl', 'show', 'd408-mihomo-tun.service', '-p', 'MainPID', '--value').stdout.strip()
    assert int(before) > 0
    key = base64.b64encode(X25519PrivateKey.generate().private_bytes_raw()).decode()
    lease = {'grant_id': 'acceptance-unreachable-relay-' + str(time.time_ns()),
             'relay_public_key': base64.b64encode(b'x' * 32).decode(),
             'allocated_address': '192.0.2.250/32', 'endpoint': '10.20.32.13:51911',
             'allowed_ips': '203.0.113.7/32', 'dns': '223.5.5.5'}
    name, _ = client_tunnel.configuration(lease, key)
    with tempfile.TemporaryDirectory(prefix='sna-handshake-accept-', dir='/var/lib') as directory:
        try:
            client_tunnel.install(Path(directory), lease, key)
        except ValueError as exc:
            assert 'WireGuard' in str(exc)
        else:
            raise AssertionError('Unreachable relay was incorrectly reported connected')
        assert run('ip', 'link', 'show', name).returncode != 0
        assert run('systemctl', 'is-active', f'wg-quick@{name}.service').returncode != 0
        assert name not in run('resolvectl', 'dns').stdout
        assert 'dev d408-tun' in run('ip', 'route', 'get', '1.1.1.1').stdout
        assert run('ip', 'link', 'show', real['tunnel']).returncode == 0
        assert run('systemctl', 'show', 'd408-mihomo-tun.service', '-p', 'MainPID', '--value').stdout.strip() == before
        for host in ['10.20.32.12', '10.20.32.13']:
            assert 'dev enp4s0' in run('ip', 'route', 'get', host).stdout
            with socket.create_connection((host, 22), timeout=5) as connection:
                assert connection.recv(128).startswith(b'SSH-2.0-')
        config = Path('/etc/wireguard') / (name + '.conf')
        config.unlink(missing_ok=True)
    print(json.dumps({'passed': True, 'unreachable_source_rejected': True,
                      'new_tunnel_and_dns_removed': True, 'existing_borrow_and_tun_preserved': True,
                      'campus_ssh_preserved': True}), flush=True)


if __name__ == '__main__':
    main()
