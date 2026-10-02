#!/usr/bin/env python3
"""Temporary independent rescue: restore only migration-owned DB fields."""
import os
from pathlib import Path
import socket
import subprocess


def main():
    if os.geteuid()!=0 or socket.gethostname()!='a-MS-7E06':raise RuntimeError('4090 root rescue only')
    snapshot=Path('/home/a/.local/share/server-network-assist-commercial/physical-default-20260917.json')
    if not snapshot.exists():return
    script=Path(__file__).with_name('configure_commercial_physical_default.py')
    subprocess.run(['/usr/sbin/runuser','-u','a','--','/usr/bin/python3',str(script),'restore'],check=True,timeout=20)
    subprocess.run(['/usr/bin/systemctl','start','server-network-assist-relay.service'],check=True,timeout=25)


if __name__=='__main__':main()
