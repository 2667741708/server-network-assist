"""Authenticated post-install check without changing any network setting."""
import argparse
import json
from pathlib import Path
import time

from .desktop import running_instance
from . import __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    if __version__ != args.version:
        raise RuntimeError('Installed package version mismatch')
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(30):
        state = running_instance(args.data)
        if state:
            request = urllib.request.Request(f"http://127.0.0.1:{state['port']}/api/health",
                headers={'X-Desktop-Token': state['token']})
            try:
                with opener.open(request, timeout=2) as response:
                    health = json.load(response)
                if health.get('version') == args.version:
                    print(json.dumps(dict(ok=True, version=args.version, pid=health['pid'])))
                    return
            except (OSError, ValueError, KeyError):
                pass  # A restart between the two probes is retryable.
        time.sleep(0.5)
    raise RuntimeError('New backend version did not become healthy')


if __name__ == '__main__':
    main()
