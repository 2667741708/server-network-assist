#!/usr/bin/env python3
"""Load Linux traffic-control modules before the restricted relay service starts."""
import os
import subprocess
from pathlib import Path
import shutil


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Run as root')
    for module in ('sch_htb', 'sch_ingress', 'cls_u32', 'act_police'):
        result = subprocess.run(['modprobe', module], text=True, capture_output=True)
        print(module, result.returncode, result.stderr.strip())
        if result.returncode:
            raise RuntimeError('Required traffic-control module unavailable: ' + module)
    Path('/etc/modules-load.d/server-network-assist-relay.conf').write_text('sch_htb\nsch_ingress\ncls_u32\nact_police\n')
    source = Path('/home/a/client_relay.py')
    target = Path('/opt/server-network-assist-relay/venv/lib/python3.13/site-packages/server_network_assist/client_relay.py')
    subprocess.run(['systemctl', 'stop', 'server-network-assist-relay.timer'], check=True)
    try:
        shutil.copy2(target, target.with_suffix('.py.previous'))
        shutil.copyfile(source, target)
        subprocess.run(['/opt/server-network-assist-relay/venv/bin/python', '-m', 'py_compile', str(target)], check=True)
    finally:
        subprocess.run(['systemctl', 'start', 'server-network-assist-relay.timer'], check=True)
    subprocess.run(['systemctl', 'start', 'server-network-assist-relay.service'], check=True)


if __name__ == '__main__':
    main()
