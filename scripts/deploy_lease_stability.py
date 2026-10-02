"""Narrow user-service patch; test first, hash guard, restore on API failure."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

STAGE = Path('/home/a/lease-stability-release-20260918')
TARGET = Path('/home/a/.local/lib/python3.13/site-packages/server_network_assist/client_store.py')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(*argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()

def tcp(port):
    with socket.create_connection(('127.0.0.1', port), timeout=3):
        return True

def unit_snapshot():
    return run('/usr/bin/systemctl', 'show', 'mihomo.service', 'wg-quick@sna-commercial.service',
               '-p', 'ActiveState', '-p', 'ActiveEnterTimestamp', '-p', 'NRestarts')

def candidate_test():
    path = STAGE / 'client_store.candidate.py'
    compile(path.read_text(), str(path), 'exec')
    spec = importlib.util.spec_from_file_location('server_network_assist.lease_candidate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(dir=STAGE) as temp:
        store = module.ClientStore(Path(temp) / 'test.sqlite3')
        store.create_plan('test', plan_id='p', quota_bytes=10000, lease_seconds=300, now=1000)
        store.create_customer('test', 'p', customer_id='c', now=1000)
        secret = store.create_enrollment_token('c', now=1000)
        store.enroll_device(secret, 'signing-key', 'test', device_id='d', now=1001)
        store.grant_line('c', 'test', 'opaque', '127.0.0.1:51910', grant_id='g', now=1000,
                         relay_public_key='relay-key', allocated_address='10.213.40.99/32',
                         relay_interface='sna-commercial', egress_interface='enp4s0')
        lease = store.issue_lease('d', 'g', now=1002)
        store.record_usage(lease['token'], 'first', 100, 200, now=1003)
        for at in [1182, 1362, 1542, 1722]:
            renewed = store.renew_lease('d', lease['id'], now=at)
            assert renewed['id'] == lease['id'] and renewed['last_rx'] == 100
            assert renewed['expires_at'] == at + 300
            lease = renewed
        assert len(store.list_leases()) == 1
        assert store.record_usage(lease['token'], 'second', 120, 220, now=1723)['used_bytes'] == 340
    return {'candidate_test': 'PASS', 'production_database_mutated': False}

def deploy():
    baseline = STAGE / 'client_store.baseline.py'
    candidate = STAGE / 'client_store.candidate.py'
    if sha(TARGET) != sha(baseline):
        raise RuntimeError('Production changed since baseline; refusing overwrite')
    tcp(22)
    tcp(9182)
    network_before = unit_snapshot()
    backup = STAGE / ('client_store.backup-' + str(time.time_ns()) + '.py')
    shutil.copy2(TARGET, backup)
    temporary = TARGET.with_name('client_store.lease-stability.tmp')
    try:
        shutil.copy2(candidate, temporary)
        os.replace(temporary, TARGET)
        run('/usr/bin/systemctl', '--user', 'restart', 'sna-commercial.service')
        for _ in range(20):
            try:
                tcp(9182)
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError('Control API did not recover')
        tcp(22)
        assert unit_snapshot() == network_before, 'Network services changed unexpectedly'
        assert sha(TARGET) == sha(candidate)
        return {'deployed': True, 'backup': str(backup), 'sha256': sha(TARGET),
                'control_tcp': True, 'ssh_tcp': True, 'network_units_unchanged': True}
    except BaseException:
        shutil.copy2(backup, TARGET)
        run('/usr/bin/systemctl', '--user', 'restart', 'sna-commercial.service')
        raise
    finally:
        temporary.unlink(missing_ok=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--deploy', action='store_true')
    args = parser.parse_args()
    result = candidate_test()
    if args.deploy:
        result.update(deploy())
    print(json.dumps(result))
