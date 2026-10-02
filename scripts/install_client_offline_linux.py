#!/usr/bin/env python3
"""Install an isolated Linux customer runtime from a locally verified wheel bundle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--user', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Run as root')
    if hashlib.sha256(args.bundle.read_bytes()).hexdigest() != args.sha256:
        raise ValueError('Bundle checksum mismatch')
    runtime = Path('/opt/server-network-assist-client/venv')
    if runtime.exists():
        raise RuntimeError('Existing customer runtime requires an explicit upgrade')
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(runtime)], check=True)
    python = runtime / 'bin/python'
    site = Path(subprocess.check_output([str(python), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    staging = runtime.parent / 'bundle'
    staging.mkdir(mode=0o700)
    with zipfile.ZipFile(args.bundle) as bundle:
        for name in bundle.namelist():
            if Path(name).name != name:
                raise ValueError('Invalid bundle path')
        bundle.extractall(staging)
    for wheel in staging.glob('*.whl'):
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                if Path(name).is_absolute() or '..' in Path(name).parts:
                    raise ValueError('Invalid wheel path')
            archive.extractall(site)
    subprocess.run([str(python), '-c', 'import server_network_assist.client; from server_network_assist.client_online import OnlineServiceClient; OnlineServiceClient._new_keys()'], check=True)
    subprocess.run([str(python), str(staging / 'install_client_linux.py'), '--user', args.user], check=True)
    print(json.dumps({'ok': True, 'runtime': str(runtime)}))


if __name__ == '__main__':
    main()
