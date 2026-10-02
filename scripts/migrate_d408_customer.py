#!/usr/bin/env python3
"""Switch D408's separate legacy borrow tunnel to a subscribed public-only customer tunnel."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time


def run(args):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=45).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--subscription', type=Path, required=True)
    args = parser.parse_args()
    if socket.gethostname() != 'd408-4090' or os.geteuid() != 0:
        raise RuntimeError('Run as root on D408 only')
    from server_network_assist.client import ClientPanel
    data = Path('/var/lib/server-network-assist-client/1000')
    panel = ClientPanel(data)
    if panel.active():
        raise RuntimeError('Customer already has an active node')
    if not panel.online.configured():
        value = args.subscription.read_text().strip()
        url = json.loads(value)['url'] if value.startswith('{') else value
        panel.online.enroll(url, 'd408-4090')
    routes = panel.online.routes()
    grant = next(row['id'] for row in routes if row['endpoint'] == '10.20.32.13:51910')
    backup = Path('/var/backups/d408-network') / (time.strftime('%Y%m%dT%H%M%S') + '-customer')
    backup.mkdir(mode=0o700, parents=True)
    shutil.copy2('/etc/wireguard/wg-d408.conf', backup / 'wg-d408.conf')
    shutil.copy2('/etc/d408-network/mode', backup / 'mode')
    (backup / 'routes.json').write_text(run(['ip', '-j', 'route', 'show', 'table', 'all']))
    units = ['d408-network-boot.service', 'wg-quick@wg-d408.service']
    enabled = {unit: subprocess.run(['systemctl', 'is-enabled', unit], capture_output=True, text=True).stdout.strip() for unit in units}
    (backup / 'units.json').write_text(json.dumps(enabled))
    # Publicly addressed campus DNS remains local as well as all private ranges.
    policy = data / 'client-routing.json'
    policy.write_text(json.dumps({'direct_cidrs': ['202.206.240.0/24']}))
    policy.chmod(0o600)
    switched = False
    try:
        run(['/usr/local/sbin/d408-network', 'direct'])
        switched = True
        result = panel.online_connect(grant)
        for _ in range(12):
            probe = subprocess.run(['curl', '--noproxy', '*', '-4fsS', '--connect-timeout', '3', '--max-time', '5', 'https://api.ipify.org'], capture_output=True, text=True)
            if probe.returncode == 0:
                break
            time.sleep(3)
        else:
            raise RuntimeError('New borrowed Internet probe failed')
        public = json.loads(run(['ip', '-j', 'route', 'get', '1.1.1.1']))[0]
        local = {ip: json.loads(run(['ip', '-j', 'route', 'get', ip]))[0]['dev'] for ip in ['10.20.32.13', '10.20.32.12', '10.136.14.1', '202.206.240.12']}
        if public['dev'] != result['tunnel'] or any(dev != 'enp4s0' for dev in local.values()):
            raise RuntimeError('Split routing validation failed')
        run(['systemctl', 'disable', *units])
        print(json.dumps({'ok': True, 'tunnel': result['tunnel'], 'public_exit': probe.stdout.strip(), 'local_routes': local, 'backup': str(backup)}, ensure_ascii=False))
    except Exception:
        if switched:
            try:
                panel.online_disconnect()
            finally:
                run(['/usr/local/sbin/d408-network', 'enable'])
                for unit, state in enabled.items():
                    if state == 'enabled':
                        run(['systemctl', 'enable', unit])
        raise


if __name__ == '__main__':
    main()
