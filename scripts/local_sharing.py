"""Operate one local desktop tunnel through its authenticated, serialized API."""
import argparse
import json
from pathlib import Path
import urllib.request

from server_network_assist.desktop import local_data_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['pause-sharing', 'restore-startup'])
    parser.add_argument('tunnel')
    parser.add_argument('--data', type=Path, default=local_data_dir())
    args = parser.parse_args()
    state = json.loads((args.data / 'desktop-instance.json').read_text(encoding='utf-8'))
    origin = 'http://127.0.0.1:' + str(int(state['port']))
    request = urllib.request.Request(origin + '/api/action',
        data=json.dumps({'action': args.action, 'tunnel': args.tunnel}).encode(),
        headers={'Content-Type': 'application/json', 'Origin': origin,
                 'X-Desktop-Token': state['token']})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=60) as response:
        result = json.load(response)
    if result.get('ok') is not True:
        raise RuntimeError('The desktop action did not complete')
    print(json.dumps({'ok': True, 'action': args.action, 'tunnel': args.tunnel}))


if __name__ == '__main__':
    main()
