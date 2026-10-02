"""Deploy only dashboard query and navigation assets; preserve network/contracts."""
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

STAGE = Path('/home/a/smooth-navigation-20260918')
spec = importlib.util.spec_from_file_location('guards', '/home/a/subscription-controls-20260918/deploy_subscription_controls.py')
guards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guards)
NAMES = ['subscription_dashboard.py', 'ui/subscriptions.html', 'ui/subscriptions.js',
         'ui/dashboard.js', 'ui/smooth-navigation.js', 'ui/smooth-navigation.css']
guards.NAMES = ['unused', 'unused', *NAMES[1:]]

def atomic(path, body):
    temporary = path.with_name(path.name + '.navigation-new')
    temporary.write_bytes(body)
    temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
    os.replace(temporary, path)

def main():
    assert subprocess.check_output(['hostname'], text=True).strip() == 'a-MS-7E06'
    expected = json.loads((STAGE/'baseline.json').read_text())
    originals = {name: (guards.ROOT/name).read_bytes() if (guards.ROOT/name).exists() else None for name in NAMES}
    for name, body in originals.items():
        assert (guards.digest(body) if body is not None else None) == expected[name], 'Baseline drift: '+name
    for name in NAMES:
        candidate = (STAGE/'candidate'/name).read_bytes()
        if name.endswith('.py'): compile(candidate, name, 'exec')
    network = guards.network(); identities = guards.identities()
    backup = STAGE/('backup-'+str(time.time_ns())); backup.mkdir(mode=0o700)
    for name, body in originals.items():
        if body is not None:
            (backup/name).parent.mkdir(parents=True, exist_ok=True)
            (backup/name).write_bytes(body)
    try:
        for name in NAMES: atomic(guards.ROOT/name, (STAGE/'candidate'/name).read_bytes())
        subprocess.run(['systemctl', '--user', 'restart', 'sna-commercial.service'], check=True, timeout=20)
        result = guards.verify(STAGE)
        assert guards.identities() == identities, 'Customer contract drift'
        assert guards.network() == network, 'Protected network drift'
    except BaseException:
        for name, body in originals.items():
            if body is None: (guards.ROOT/name).unlink(missing_ok=True)
            else: atomic(guards.ROOT/name, body)
        subprocess.run(['systemctl', '--user', 'restart', 'sna-commercial.service'], check=True, timeout=20)
        raise
    result.update(deployed=True, backup=str(backup), protected_network_unchanged=True,
                  customer_contracts_unchanged=True, campus_tcp=network['campus_tcp'])
    (STAGE/'applied.json').write_text(json.dumps(result))
    print(json.dumps(result))

if __name__ == '__main__': main()
