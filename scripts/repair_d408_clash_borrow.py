#!/usr/bin/env python3
"""Remove obsolete physical-interface pinning from D408's normal local proxy."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time


def main():
    if os.geteuid() != 0 or socket.gethostname() != 'd408-4090':
        raise RuntimeError('Run as root on D408')
    root = Path('/home/d408/clashctl')
    config = root / 'resources/normal-runtime.yaml'
    manager = Path('/usr/local/sbin/d408-tun')
    backup = Path('/var/backups/d408-network') / (time.strftime('%Y%m%dT%H%M%S') + '-clash-compat')
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(config, backup / config.name)
    shutil.copy2(manager, backup / manager.name)
    original = manager.read_text()
    old = '.tun.enable=false | del(.routing-mark) | .interface-name=strenv(IFACE)'
    new = '.tun.enable=false | del(.routing-mark) | del(.interface-name)'
    if old not in original and new not in original:
        raise RuntimeError('Clash manager differs from inspected version')
    generated = subprocess.check_output([str(root / 'bin/yq'), new, str(config)], text=True)
    temporary = config.with_suffix('.compat.yaml')
    temporary.write_text(generated)
    temporary.chmod(0o600)
    os.chown(temporary, 1000, 1000)
    subprocess.run(['runuser','-u','d408','--',str(root / 'bin/mihomo'),'-t','-d',str(root / 'resources'),'-f',str(temporary)], check=True)
    manager.write_text(original.replace(old, new))
    manager.chmod(0o755)
    temporary.replace(config)
    subprocess.run(['systemctl','restart','d408-mihomo-proxy.service'], check=True)
    print('Repaired normal proxy interface binding; backup:', backup)


if __name__ == '__main__':
    main()
