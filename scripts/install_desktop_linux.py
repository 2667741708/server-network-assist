#!/usr/bin/env python3
"""Install an Ubuntu user launcher after installing server-network-assist[desktop]."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def desktop_quote(value):
    # freedesktop Exec field: escape reserved characters, including literal percent.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--autostart', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('Run as the logged-in Ubuntu desktop user, without sudo')
    import server_network_assist.desktop as desktop
    app_dir = Path(os.getenv('XDG_DATA_HOME', str(Path.home()/'.local/share'))) / 'applications'
    app_dir.mkdir(parents=True, exist_ok=True)
    launcher = app_dir / 'server-network-assist.desktop'
    command = desktop_quote(sys.executable) + ' -m server_network_assist.desktop'
    text = '\n'.join(['[Desktop Entry]', 'Type=Application', 'Version=1.0',
        'Name=Server Network Assist', 'Name[zh_CN]=服务器网络助手',
        'Comment=Manage Windows and Ubuntu network sharing', 'Exec='+command,
        'Icon='+str(desktop.ROOT/'desktop_ui/icon.svg'), 'Terminal=false',
        'Categories=Network;Settings;', 'StartupNotify=true', ''])
    launcher.write_text(text, encoding='utf-8')
    launcher.chmod(0o755)
    desktop_dir = Path.home()/'Desktop'
    if shutil.which('xdg-user-dir'):
        result = subprocess.run(['xdg-user-dir', 'DESKTOP'], text=True, capture_output=True, check=True)
        desktop_dir = Path(result.stdout.strip())
    if desktop_dir.is_dir() and desktop_dir != Path.home():
        shortcut = desktop_dir/launcher.name
        shutil.copy2(launcher, shortcut)
        if shutil.which('gio'):
            subprocess.run(['gio', 'set', str(shortcut), 'metadata::trusted', 'true'], check=False)
    if args.autostart:
        autostart = Path(os.getenv('XDG_CONFIG_HOME', str(Path.home()/'.config'))) / 'autostart'
        autostart.mkdir(parents=True, exist_ok=True)
        # Open normal launcher at login: it reuses a healthy backend and starts the tray.
        (autostart/launcher.name).write_text(text, encoding='utf-8')
    print('Installed launcher: '+str(launcher))
    print('Local tunnel service changes need root; fleet SSH helpers use their own scoped privilege checks.')


if __name__ == '__main__':
    main()
