#!/usr/bin/env python3
"""Exercise the real installed customer's disconnect/reconnect API without exposing tokens."""
import json
from pathlib import Path
import subprocess
import socket
import time
import urllib.request

DATA = Path('/var/lib/server-network-assist-client/1000')


def run(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if check and result.returncode:
        raise RuntimeError(result.stderr[-1000:])
    return result


def request(path, value=None):
    instance = json.loads((DATA / 'client-instance.json').read_text())
    base = f'http://127.0.0.1:{instance["port"]}'
    headers = {'X-Client-Token': instance['token'], 'Origin': base, 'Content-Type': 'application/json'}
    req = urllib.request.Request(base + path, headers=headers,
        data=json.dumps(value).encode() if value is not None else None)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=80) as response:
        result = json.load(response)
    if result.get('ok') is False:
        raise RuntimeError(result.get('error', 'customer API failed'))
    return result


def main():
    active = json.loads((DATA / 'customer-active-line.json').read_text())
    grant, tunnel = active['line_id'], active['tunnel']
    # Restart only the backend; the owned TUN is independent of this process.
    run('systemctl', 'restart', 'server-network-assist-client-1000.service')
    for _ in range(60):
        try:
            request('/api/state')
            break
        except (OSError, ValueError):
            time.sleep(.5)
    else:
        raise RuntimeError('client backend did not recover')
    complete = False
    try:
        run('/usr/local/sbin/d408-tun', 'enable')
        request('/api/online/disconnect', {})
        assert run('ip', 'link', 'show', tunnel, check=False).returncode != 0
        assert run('ip', 'link', 'show', 'd408-tun', check=False).returncode != 0
        assert not Path('/etc/server-network-assist/customer-tun.json').exists()
        rules = json.loads(run('ip', '-j', 'rule', 'show').stdout)
        assert not any(10030 <= x['priority'] <= 10060 for x in rules)
        assert '198.19.0.2' not in run('resolvectl', 'dns').stdout
        for host in ['10.20.32.12', '10.20.32.13']:
            assert 'dev enp4s0' in run('ip', 'route', 'get', host).stdout
            with socket.create_connection((host, 22), timeout=5) as connection:
                assert connection.recv(128).startswith(b'SSH-2.0-')
        print('DISCONNECT VERIFIED: no customer WG/TUN/rules/fake-IP DNS; jump host direct', flush=True)
        request('/api/online/connect', {'grant_id': grant})
        assert 'dev ' + tunnel in run('ip', 'route', 'get', '1.1.1.1').stdout
        assert 'dev enp4s0' in run('ip', 'route', 'get', '198.19.0.2').stdout
        print('RECONNECT VERIFIED: borrowed public routes; fake-IP excluded from WG', flush=True)
        print(run('/usr/local/sbin/d408-tun', 'enable').stdout, flush=True)
        request('/api/online/lease/renew', {})
        assert 'dev d408-tun' in run('ip', 'route', 'get', '1.1.1.1').stdout
        print('RENEW VERIFIED: lease renewal preserves active TUN', flush=True)
        complete = True
    finally:
        if not complete:
            if not (DATA / 'customer-active-line.json').exists():
                request('/api/online/connect', {'grant_id': grant})
            run('/usr/local/sbin/d408-tun', 'off', check=False)


if __name__ == '__main__':
    main()
