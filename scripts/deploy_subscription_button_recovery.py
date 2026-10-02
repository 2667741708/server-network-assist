"""Deploy only two reviewed JavaScript assets; preserve networking and roll back failures."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

from deploy_subscription_admin_redesign import ROOT, atomic, digest, probe

NAMES = ['subscriptions.js', 'dashboard.js']


def prepare(stage):
    assert subprocess.check_output(['hostname'], text=True).strip() == 'a-MS-7E06'
    manifest = json.loads((stage / 'manifest.json').read_text())
    assert set(manifest) == set(NAMES)
    for name in NAMES:
        assert digest((ROOT / 'ui' / name).read_bytes()) == manifest[name]['baseline'], name
        assert digest((stage / 'candidate' / name).read_bytes()) == manifest[name]['candidate'], name
    (stage / 'prepared.json').write_text(json.dumps({'manifest': manifest, 'protected': probe()}))
    print(json.dumps({'prepared': True, 'live_assets_changed': False}))


def apply(stage):
    prepared = json.loads((stage / 'prepared.json').read_text())
    manifest = prepared['manifest']
    assert probe() == prepared['protected'], 'Protected state changed since preparation'
    originals = {name: (ROOT / 'ui' / name).read_bytes() for name in NAMES}
    for name in NAMES:
        assert digest(originals[name]) == manifest[name]['baseline'], 'Concurrent change: ' + name
        assert digest((stage / 'candidate' / name).read_bytes()) == manifest[name]['candidate'], name
    backup = stage / ('backup-' + str(time.time_ns()))
    backup.mkdir(mode=0o700)
    for name, body in originals.items():
        (backup / name).write_bytes(body)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for name in NAMES:
            atomic(ROOT / 'ui' / name, (stage / 'candidate' / name).read_bytes())
        for base in ['http://127.0.0.1:9182/', 'http://10.201.250.1:9180/subscription-admin/']:
            for name in NAMES:
                with opener.open(base + name, timeout=10) as response:
                    assert digest(response.read()) == manifest[name]['candidate'], base + name
        with opener.open('http://127.0.0.1:9182/api/session', timeout=5) as response:
            assert json.load(response)['authenticated'] is False
        try:
            opener.open('http://127.0.0.1:9182/api/client-service/dashboard', timeout=5)
            raise AssertionError('Anonymous dashboard access allowed')
        except urllib.error.HTTPError as error:
            assert error.code == 401
        assert probe() == prepared['protected'], 'Protected networking/backend/service changed'
    except BaseException:
        for name, body in originals.items():
            atomic(ROOT / 'ui' / name, body)
        raise
    result = {'deployed': True, 'assets': NAMES, 'both_entrypoints_verified': True,
              'protected_network_backend_services_unchanged': True, 'backup': str(backup)}
    (stage / 'applied.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'apply'])
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    {'prepare': prepare, 'apply': apply}[args.action](args.stage)
