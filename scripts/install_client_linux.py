#!/usr/bin/env python3
"""Install the customer client as a root backend plus a per-user Ubuntu launcher."""
import argparse
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys


def systemd_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True)
    parser.add_argument('--autostart-ui', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('Run this installer as root on Ubuntu')
    account = pwd.getpwnam(args.user)
    if account.pw_uid < 1000 or account.pw_dir in ('', '/'):
        raise ValueError('Choose a normal desktop user')
    import server_network_assist.client
    data = Path('/var/lib/server-network-assist-client') / str(account.pw_uid)
    data.mkdir(parents=True, exist_ok=True, mode=0o711)
    data.chmod(0o711)
    service_name = f'server-network-assist-client-{account.pw_uid}.service'
    service = Path('/etc/systemd/system') / service_name
    service.write_text('\n'.join([
        '[Unit]', 'Description=Server Network Assist customer client',
        'After=network-online.target', 'Wants=network-online.target', '',
        '[Service]', 'Type=simple', f'Environment=SNA_CLIENT_UI_UID={account.pw_uid}',
        'ExecStart=' + systemd_quote(sys.executable) + ' -m server_network_assist.client --serve --data ' + systemd_quote(data),
        'Restart=on-failure', 'RestartSec=3', 'NoNewPrivileges=false', 'PrivateTmp=true',
        'ProtectHome=true', 'ProtectSystem=strict', 'ReadWritePaths=' + str(data), '',
        '[Install]', 'WantedBy=multi-user.target', '']), encoding='utf-8')
    os.chmod(service, 0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', service_name], check=True)
    app_dir = Path(account.pw_dir) / '.local/share/applications'
    app_dir.mkdir(parents=True, exist_ok=True)
    launcher = app_dir / 'server-network-assist-client.desktop'
    launcher.write_text('\n'.join([
        '[Desktop Entry]', 'Type=Application', 'Version=1.0', 'Name=Borrow Network Client',
        'Name[zh_CN]=借网客户端', 'Comment=Use an authorized signed network subscription',
        'Exec=' + systemd_quote(sys.executable) + ' -m server_network_assist.client --data ' + systemd_quote(data),
        'Terminal=false', 'Categories=Network;Settings;', 'StartupNotify=true', '']), encoding='utf-8')
    os.chown(app_dir, account.pw_uid, account.pw_gid)
    os.chown(launcher, account.pw_uid, account.pw_gid)
    launcher.chmod(0o755)
    if args.autostart_ui:
        auto = Path(account.pw_dir) / '.config/autostart'
        auto.mkdir(parents=True, exist_ok=True)
        target = auto / launcher.name
        shutil.copy2(launcher, target)
        os.chown(auto, account.pw_uid, account.pw_gid)
        os.chown(target, account.pw_uid, account.pw_gid)
    print(f'Installed {service_name} and {launcher}')


if __name__ == '__main__':
    main()
