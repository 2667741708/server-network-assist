"""Authenticated customer-client post-install health check."""
import argparse
import json
from pathlib import Path
import time
import urllib.request

from . import __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    if __version__ != args.version:
        raise RuntimeError('Installed package version mismatch')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(30):
        try:
            state = json.loads((args.data / 'client-instance.json').read_text(encoding='utf-8'))
            request = urllib.request.Request(f"http://127.0.0.1:{state['port']}/api/health",
                                             headers={'X-Client-Token': state['token']})
            with opener.open(request, timeout=2) as response:
                health = json.load(response)
            if health.get('version') == args.version:
                print(json.dumps({'ok': True, 'version': args.version}))
                return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.5)
    raise RuntimeError('Customer client backend did not become healthy')


if __name__ == '__main__':
    main()
