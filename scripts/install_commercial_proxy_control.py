#!/usr/bin/env python3
"""Install a fixed root-owned Mihomo start helper; no network configuration edits."""
import argparse
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper',type=Path,required=True)
    args=parser.parse_args()
    if os.geteuid()!=0 or socket.gethostname()!='a-MS-7E06':
        raise RuntimeError('Run only on the authorized 4090 source as root')
    helper=Path('/usr/local/sbin/sna-commercial-proxy')
    rule=Path('/etc/sudoers.d/sna-commercial-proxy')
    content=args.helper.read_bytes();compile(content,str(helper),'exec')
    if not content.startswith(b'#!/usr/bin/python3 -I'):
        raise ValueError('The helper must use isolated Python')
    owner=pwd.getpwnam('a')
    if owner.pw_uid!=1000:raise RuntimeError('Unexpected service owner')
    backup=Path('/var/backups/sna-commercial-proxy-control-20260917')
    backup.mkdir(mode=0o700)
    before={path:path.read_bytes() if path.exists() else None for path in (helper,rule)}
    for path,data in before.items():
        if path.is_symlink():raise ValueError('Refusing symlink target')
        if data is not None:(backup/path.name).write_bytes(data)
    temporary=backup/'sudoers-new'
    temporary.write_text('a ALL=(root) NOPASSWD: /usr/local/sbin/sna-commercial-proxy start\n',encoding='ascii')
    temporary.chmod(0o440)
    subprocess.run(['/usr/sbin/visudo','-cf',str(temporary)],check=True,capture_output=True)
    try:
        helper.write_bytes(content);helper.chmod(0o755);os.chown(helper,0,0)
        shutil.copyfile(temporary,rule);rule.chmod(0o440);os.chown(rule,0,0)
        subprocess.run(['/usr/sbin/visudo','-c'],check=True,capture_output=True)
    except Exception:
        for path,data in before.items():
            if data is None:path.unlink(missing_ok=True)
            else:path.write_bytes(data)
        raise
    print('{"ok":true,"allowed_action":"start","service":"mihomo.service"}')


if __name__=='__main__':main()
