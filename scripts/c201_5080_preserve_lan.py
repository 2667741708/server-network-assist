#!/usr/bin/env python3
"""Preserve private LAN routes alongside the existing 5080 wg-fleet tunnel.

Run apply as root on c201-MS-7E06. This does not restart any tunnel.
"""
import json
import fcntl
import os
from pathlib import Path
import socket
import subprocess
import sys

STATE = Path('/var/lib/server-network-assist/5080-lan-routes.json')
PREFIXES = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def save(value):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(STATE)


def apply():
    routes = json.loads(run(['ip', '-j', '-4', 'route', 'show', 'table', 'main']))
    original = next((r for r in routes if r.get('dst') == 'default'
                     and r.get('dev') == 'enp4s0' and r.get('gateway')), None)
    if not original:
        raise RuntimeError('Original enp4s0 default gateway missing; no routes changed')
    owned = json.loads(STATE.read_text()) if STATE.exists() else []
    desired = [(prefix, 'enp4s0', original['gateway']) for prefix in PREFIXES]
    # Preserve tunnel-local access used by the existing repair tool and Titan.
    if Path('/sys/class/net/wg-fleet').exists():
        desired.append(('10.203.49.0/24', 'wg-fleet', None))
    for prefix, dev, gateway in desired:
        existing = [r for r in routes if r.get('dst') == prefix]
        entry = {'prefix': prefix, 'dev': dev, 'gateway': gateway}
        if existing and not any(e['prefix'] == prefix for e in owned):
            # Never overwrite a route owned by another administrator/service.
            raise RuntimeError('Unowned route already exists for ' + prefix)
        if existing and any(str(r.get('protocol')) not in ('186', 'bgp') for r in existing):
            raise RuntimeError('Owned route was changed externally: ' + prefix)
        if entry not in owned:
            owned = [e for e in owned if e['prefix'] != prefix] + [entry]
            save(owned)
        command = ['ip', '-4', 'route', 'replace', prefix]
        if gateway:
            command += ['via', gateway]
        command += ['dev', dev, 'proto', '186', 'metric', '25']
        run(command)
    print(json.dumps({'ok': True, 'owned_routes': owned}))


def remove():
    owned = json.loads(STATE.read_text()) if STATE.exists() else []
    for entry in list(owned):
        current = json.loads(run(['ip', '-j', '-4', 'route', 'show', entry['prefix']]))
        for route in current:
            if route.get('dst') != entry['prefix']:
                continue
            if (str(route.get('protocol')) not in ('186', 'bgp') or route.get('dev') != entry['dev']
                    or route.get('gateway') != entry['gateway']):
                raise RuntimeError('Route changed externally; refusing removal: ' + entry['prefix'])
            run(['ip', '-4', 'route', 'del', entry['prefix'], 'dev', entry['dev'], 'proto', '186', 'metric', '25'])
        owned.remove(entry)
        save(owned)
    print(json.dumps({'ok': True, 'removed': True}))


if __name__ == '__main__':
    if socket.gethostname() != 'c201-MS-7E06' or os.geteuid() != 0:
        raise SystemExit('Run as root only on c201-MS-7E06')
    action = sys.argv[1] if len(sys.argv) > 1 else 'apply'
    with open('/run/sna-5080-lan.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == 'apply':
            apply()
        elif action == 'remove':
            remove()
        else:
            raise SystemExit('Usage: c201_5080_preserve_lan.py [apply|remove]')
