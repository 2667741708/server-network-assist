"""Bounded source-side test of a customer's airport transport without nested proxying."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import yaml


def run(argv):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=40)
    if r.returncode:
        raise RuntimeError('Source compatibility action failed: ' + argv[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['apply', 'restore', 'commit'])
    args = parser.parse_args()
    if os.geteuid() != 0 or os.uname().nodename != 'a-MS-7E06':
        raise RuntimeError('Only the explicitly authorized 4090 source')
    path = Path('/etc/mihomo/config.yaml')
    root = Path('/var/backups/sna-titan-tuic-v2')
    saved = root / 'config-original.yaml'
    metadata = root / 'metadata.json'
    marker = root / 'verified'
    if args.action == 'commit':
        marker.write_text('Verified on Titan')
        print(json.dumps({'committed': True}))
        return
    if args.action == 'restore':
        if marker.exists():
            return
        shutil.copy2(saved, path)
        previous = json.loads(metadata.read_text())
        os.chown(path, previous['uid'], previous['gid'])
        path.chmod(previous['mode'])
        run(['systemctl', 'restart', 'mihomo.service'])
        return
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    if saved.exists():
        raise RuntimeError('Existing backup requires explicit inspection')
    original = path.stat()
    metadata.write_text(json.dumps({'uid': original.st_uid, 'gid': original.st_gid,
                                    'mode': original.st_mode & 0o777}))
    shutil.copy2(path, saved)
    document = yaml.safe_load(path.read_text())
    rule = 'IP-CIDR,20.230.223.128/32,DIRECT,no-resolve'
    if rule not in document['rules']:
        document['rules'].insert(0, rule)
    temporary = path.with_suffix('.sna-tuic-test.yaml')
    temporary.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False))
    temporary.chmod(0o600)
    os.chown(temporary, original.st_uid, original.st_gid)
    run(['/usr/local/bin/mihomo', '-t', '-d', '/var/lib/mihomo', '-f', str(temporary)])
    run(['systemd-run', '--unit=sna-titan-tuic-v2-rollback', '--on-active=4m',
         '/usr/bin/python3', str(Path(__file__).resolve()), 'restore'])
    try:
        temporary.replace(path)
        run(['systemctl', 'restart', 'mihomo.service'])
        run(['systemctl', 'is-active', 'mihomo.service'])
    except Exception:
        shutil.copy2(saved, path)
        os.chown(path, original.st_uid, original.st_gid)
        path.chmod(original.st_mode & 0o777)
        run(['systemctl', 'restart', 'mihomo.service'])
        raise
    print(json.dumps({'applied': True, 'rule': rule, 'rollback': '4 minutes unless verified'}))


if __name__ == '__main__':
    main()
