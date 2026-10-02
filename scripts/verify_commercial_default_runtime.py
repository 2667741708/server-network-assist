#!/usr/bin/env python3
"""Read-only source route baseline / service acceptance for default migration."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


def run(argv):
    return subprocess.run(argv,capture_output=True,text=True,check=True,timeout=10).stdout


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('baseline','check'))
    args=parser.parse_args()
    if os.geteuid()!=0 or socket.gethostname()!='a-MS-7E06':raise RuntimeError('4090 root verification only')
    path=Path('/var/backups/sna-default-egress-baseline-20260917.json')
    main_routes=json.loads(run(['/usr/sbin/ip','-4','-j','route','show','table','main']))
    rules=json.loads(run(['/usr/sbin/ip','-4','-j','rule','show']))
    if args.action=='baseline':
        descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(descriptor,'w') as out:json.dump(dict(main_routes=main_routes,rules=rules),out)
        print('{"baseline_saved":true}');return
    before=json.loads(path.read_text())
    sys.path.insert(0,'/opt/server-network-assist-relay/venv/lib/python3.13/site-packages')
    from server_network_assist.client_egress import route_table
    state=json.loads(Path('/var/lib/server-network-assist-relay/state.json').read_text())
    active=[row['policy'] for row in state['peers'].values() if row.get('status')=='active']
    owned={str(route_table(row)) for row in active if row.get('egress_mode')=='physical'}
    remaining=[row for row in rules if not (row.get('priority')==100 and str(row.get('table')) in owned and row.get('iif')=='sna-commercial')]
    campus={}
    for host in ('10.20.31.134','10.20.32.12','10.20.32.14'):
        try:
            with socket.create_connection((host,22),timeout=3) as conn:
                conn.settimeout(3);campus[host]=conn.recv(100).startswith(b'SSH-')
        except OSError:campus[host]=False
    checks=dict(main_routes_preserved=main_routes==before['main_routes'],existing_rules_preserved=remaining==before['rules'],
        active_customer_modes_physical=bool(active) and all(row.get('egress_mode')=='physical' for row in active),campus_tcp=campus)
    ok=all(v for k,v in checks.items() if k!='campus_tcp') and all(campus.values())
    print(json.dumps(dict(ok=ok,checks=checks,active_customers=len(active))))
    if not ok:raise SystemExit(1)


if __name__=='__main__':main()
