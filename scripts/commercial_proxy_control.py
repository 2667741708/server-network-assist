#!/usr/bin/python3 -I
"""Install root-owned at /usr/local/sbin/sna-commercial-proxy; start only."""
import json
import os
import subprocess
import sys
import time


def run(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=8, check=False)


def main():
    if os.geteuid() != 0 or sys.argv[1:] != ['start']:
        raise SystemExit('Only the fixed start action is allowed')
    before = run(['/usr/bin/systemctl', 'is-active', 'mihomo.service']).returncode == 0
    success = False
    try:
        if run(['/usr/bin/systemctl', 'start', 'mihomo.service']).returncode:
            raise RuntimeError('Mihomo failed to start')
        for _ in range(20):
            result = run(['/usr/sbin/ip', '-j', '-d', 'link', 'show', 'dev', 'Meta'])
            values = json.loads(result.stdout or '[]')
            if values and values[0].get('linkinfo', {}).get('info_kind') == 'tun' and 'UP' in values[0].get('flags', []):
                success = True
                print('{"ok":true}')
                return
            time.sleep(.25)
        raise RuntimeError('Mihomo TUN not ready')
    finally:
        if not success and not before:
            run(['/usr/bin/systemctl', 'stop', 'mihomo.service'])


if __name__ == '__main__':
    main()
