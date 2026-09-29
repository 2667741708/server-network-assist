"""Installed customer data location, independent of stale Windows profile variables."""
import csv
import os
from pathlib import Path
import subprocess


def customer_data():
    if os.name != 'nt':
        return Path('/var/lib/server-network-assist-client') / str(int(os.environ.get('SUDO_UID') or os.getuid()))
    identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True,
                                       creationflags=0x08000000)
    sid = next(csv.reader([identity.strip()]))[1]
    return Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'ServerNetworkAssist/client' / sid
