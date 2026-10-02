#!/usr/bin/env python3
"""Pinned 4090 default migration, with narrow grant/source rollback."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket

from server_network_assist.client_store import ClientStore
from server_network_assist.client_egress import validate_egress
from server_network_assist.commercial_proxy import physical_defaults


DATABASE=Path('/home/a/.local/share/server-network-assist-commercial/data/commercial-service.sqlite3')
SNAPSHOT=Path('/home/a/.local/share/server-network-assist-commercial/physical-default-20260917.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('inspect','apply','restore','verify'))
    args=parser.parse_args()
    if socket.gethostname()!='a-MS-7E06':raise RuntimeError('Authorized source only')
    store=ClientStore(DATABASE)
    defaults=physical_defaults()
    if not defaults:raise RuntimeError('No unambiguous physical main-table gateway')
    policy=validate_egress('physical',defaults['egress_gateway'],'223.5.5.5,1.1.1.1')
    with store._connect() as db:
        sources=[dict(row) for row in db.execute('SELECT * FROM sources')]
        grants=[dict(row) for row in db.execute("SELECT id,endpoint,egress_interface,egress_policy,dns,allowed_ips FROM line_grants WHERE endpoint='10.20.32.13:51910'")]
    if args.action=='inspect':
        print(json.dumps(dict(defaults=defaults,source_count=len(sources),grant_count=len(grants),
            grant_modes=[json.loads(row['egress_policy']).get('egress_mode','source_proxy') for row in grants],
            dns=[row['dns'] for row in grants],allowed_ips=[row['allowed_ips'] for row in grants])))
        return
    if args.action=='apply':
        if SNAPSHOT.exists():raise RuntimeError('Migration snapshot already exists')
        selected=[row for row in sources if json.loads(row['configuration']).get('endpoint')=='10.20.32.13:51910']
        if not selected:raise RuntimeError('Expected 4090 source configuration missing')
        for row in grants:
            # Keep active leases only when the already delivered client DNS/IP
            # contract needs no change. Otherwise require an explicit reconnect.
            validate_egress('physical',defaults['egress_gateway'],row['dns'])
            if any(ipaddress.ip_network(v.strip()).version!=4 for v in row['allowed_ips'].split(',')):
                raise RuntimeError('IPv6 grant requires explicit disconnect/reconnect; migration aborted')
        snapshot=dict(sources=selected,grants=grants)
        fd=os.open(SNAPSHOT,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as output:json.dump(snapshot,output)
        with store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in selected:
                config=json.loads(row['configuration'])
                config.update(defaults);config.update(policy);config['proxy_interface']='Meta'
                db.execute('UPDATE sources SET configuration=? WHERE id=?',(json.dumps(config),row['id']))
            for row in grants:
                db.execute('UPDATE line_grants SET egress_interface=?,egress_policy=? WHERE id=?',
                    (defaults['egress_interface'],json.dumps(dict(egress_mode='physical',egress_gateway=defaults['egress_gateway'],require_source_proxy=False)),row['id']))
        print(json.dumps(dict(ok=True,grants_migrated=len(grants),leases_preserved=True)))
    elif args.action=='restore':
        snapshot=json.loads(SNAPSHOT.read_text())
        with store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in snapshot['sources']:
                db.execute('UPDATE sources SET configuration=? WHERE id=?',(row['configuration'],row['id']))
            for row in snapshot['grants']:
                db.execute('UPDATE line_grants SET egress_interface=?,egress_policy=? WHERE id=?',
                           (row['egress_interface'],row['egress_policy'],row['id']))
        print('{"ok":true,"restored":true}')
    else:
        ok=all(json.loads(row['egress_policy']).get('egress_mode')=='physical' for row in grants)
        ok=ok and all(json.loads(row['configuration']).get('egress_mode')=='physical' for row in sources
                      if json.loads(row['configuration']).get('endpoint')=='10.20.32.13:51910')
        print(json.dumps(dict(ok=ok,default='physical',grants=len(grants))))
        if not ok:raise SystemExit(1)


if __name__=='__main__':main()
