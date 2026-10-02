#!/usr/bin/env python3
"""Read-only TCP checks between the three registered campus servers."""
import json
import socket
import subprocess
import sys

HOSTS = {'c201-MS-7E06': '10.20.32.12', 'a-MS-7E06': '10.20.32.13',
         'd408-4090': '10.20.32.14'}


def main():
    hostname = socket.gethostname()
    if hostname not in HOSTS:
        raise RuntimeError('Unknown campus host; do not infer its networking')
    results = []
    for peer, ip in HOSTS.items():
        if peer == hostname:
            continue
        row = {'from': hostname, 'to': peer, 'ip': ip, 'port': 22}
        try:
            route = subprocess.run(['ip', 'route', 'get', ip], capture_output=True,
                                   text=True, timeout=5, check=True).stdout.strip()
            with socket.create_connection((ip, 22), timeout=5) as connection:
                banner = connection.recv(128).decode(errors='replace').strip()
            row.update(ok=banner.startswith('SSH-2.0-'), route=route, banner=banner)
        except (OSError, subprocess.SubprocessError) as error:
            row.update(ok=False, error=str(error))
        results.append(row)
    print(json.dumps(results, ensure_ascii=False))
    return 0 if all(row['ok'] for row in results) else 1


if __name__ == '__main__':
    sys.exit(main())
