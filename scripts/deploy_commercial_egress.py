#!/usr/bin/env python3
"""Deploy only commercial egress modules/UI; preserve proxy and network policy.

Inspects and compiles first, backs up changed files and schema, pauses only the
application/reconciler, validates startup, and restores on failure.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

MODULES=('client_egress.py','client_relay.py','client_relay_agent.py','client_store.py',
         'client_service_admin.py','client_service_api.py','commercial_proxy.py','subscription_archive.py')
APP_BLOCK="""            elif action == 'grant-egress':
                result = state.client_service.set_grant_egress(object_id,
                    egress_mode=str(p.get('egress_mode', '')), egress_interface=str(p.get('egress_interface', '')),
                    egress_gateway=str(p.get('egress_gateway', '')), dns=str(p.get('dns', '')))
                state.audit('client_grant_egress_updated', object_id)
                return web.json_response({'grant': result})
"""


def run(argv,*,check=True,env=None):
    return subprocess.run(argv,text=True,capture_output=True,check=check,env=env,timeout=20)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--scope',choices=('source','cloud'),required=True)
    parser.add_argument('--source-tree',type=Path,required=True);parser.add_argument('--backup',type=Path,required=True)
    args=parser.parse_args();source=args.scope=='source'
    if os.geteuid()!=0 or socket.gethostname()!=('a-MS-7E06' if source else 'VM-0-12-ubuntu'):
        raise RuntimeError('Run on the authorized Linux host as root')
    if args.backup.parent!=Path('/var/backups') or not args.backup.name.startswith('sna-egress-deploy-') or args.backup.exists():
        raise ValueError('Use a new /var/backups/sna-egress-deploy-* directory')
    package=args.source_tree/'server_network_assist'
    roots=[Path('/home/a/.local/lib/python3.13/site-packages/server_network_assist'),
           Path('/opt/server-network-assist-relay/venv/lib/python3.13/site-packages/server_network_assist')] if source else [Path('/opt/server-network-assist/venv/lib/python3.12/site-packages/server_network_assist')]
    updates=[]
    for root in roots:
        if not root.is_dir():raise RuntimeError('Expected installed package missing')
        for name in MODULES:
            content=(package/name).read_bytes();compile(content,str(root/name),'exec');updates.append((root/name,content))
        if root==roots[0]:
            app=root/'app.py';text=app.read_text(encoding='utf-8')
            anchor="            elif action == 'lease-revoke':\n"
            if "elif action == 'grant-egress':" not in text:
                if text.count(anchor)!=1:raise RuntimeError('Cannot safely insert grant-egress endpoint')
                text=text.replace(anchor,APP_BLOCK+anchor)
            # Replace only the commercial handlers, not the rest of a host's
            # independently deployed management app.
            canonical=(package/'app.py').read_text(encoding='utf-8')
            for start,end in (("        if path == '/api/client-service':\n","        if path == '/api/clash':\n"),
                              ("        if path == '/api/client-service/action':\n","        if path == '/api/host/save':\n")):
                if text.count(start)!=1 or text.count(end)!=1 or canonical.count(start)!=1 or canonical.count(end)!=1:
                    raise RuntimeError('Commercial handler anchors changed')
                begin,finish=text.index(start),text.index(end,text.index(start))
                text=text[:begin]+canonical[canonical.index(start):canonical.index(end,canonical.index(start))]+text[finish:]
            compile(text,str(app),'exec');updates.append((app,text.encode('utf-8')))
            for file in (package/'ui').rglob('*'):
                if file.is_file():updates.append((root/'ui'/file.relative_to(package/'ui'),file.read_bytes()))
    unit='sna-commercial' if source else 'server-network-assist.service'
    def service(action):
        if source:
            env=dict(os.environ,XDG_RUNTIME_DIR='/run/user/1000')
            return run(['runuser','-u','a','--','systemctl','--user',action,unit],env=env)
        return run(['systemctl',action,unit])
    unit_was_active=service('is-active').returncode==0
    relay_was_active=source and run(['systemctl','is-active','server-network-assist-relay.timer'],check=False).returncode==0
    args.backup.mkdir(mode=0o700);manifest=[]
    for index,(path,content) in enumerate(updates):
        previous=path.read_bytes() if path.exists() else None
        stat=path.stat() if path.exists() else path.parent.stat()
        row=dict(path=str(path),existed=previous is not None,uid=stat.st_uid,gid=stat.st_gid,
                 mode=stat.st_mode&0o777 if previous is not None else 0o644,
                 expected_sha256=hashlib.sha256(content).hexdigest(),backup=str(index))
        if previous is not None:
            (args.backup/str(index)).write_bytes(previous);(args.backup/str(index)).chmod(0o600)
        manifest.append(row)
    manifest_path=args.backup/'manifest.json';manifest_path.write_text(json.dumps(manifest));manifest_path.chmod(0o600)
    # Only the additive column can be introduced by this release. Rollback drops
    # it, retaining other user records instead of replacing a live database.
    databases=[]
    if source:databases=[Path('/home/a/.local/share/server-network-assist-commercial/data/commercial-service.sqlite3')]
    else:
        # Resolve cloud --data without exposing other command line arguments.
        pid=run(['systemctl','show',unit,'-p','MainPID','--value']).stdout.strip()
        argv=Path('/proc')/pid/'cmdline'
        values=argv.read_bytes().decode().split('\0')
        if '--data' in values:databases=[Path(values[values.index('--data')+1])/'commercial-service.sqlite3']
    schema_before={}
    for db in databases:
        if db.is_file():
            connection=sqlite3.connect(db)
            try:schema_before[str(db)]={row[1] for row in connection.execute('PRAGMA table_info(line_grants)')}
            finally:connection.close()
    def write(path,content,row):
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix(path.suffix+'.egress-new');temporary.write_bytes(content)
        temporary.chmod(row['mode']);os.chown(temporary,row['uid'],row['gid']);temporary.replace(path)
    try:
        if source:
            run(['systemctl','stop','server-network-assist-relay.timer']);run(['systemctl','stop','server-network-assist-relay.service'])
        service('stop')
        for (path,content),row in zip(updates,manifest):write(path,content,row)
        service('start')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(10):
            try:
                with opener.open('http://127.0.0.1:'+('9182' if source else '9180')+'/api/session',timeout=2) as response:
                    value=json.load(response)
                if not isinstance(value,dict):raise RuntimeError('Invalid app response')
                break
            except Exception:time.sleep(.5)
        else:raise RuntimeError('Application did not recover after update')
        if source:
            if relay_was_active:run(['systemctl','start','server-network-assist-relay.timer'])
            run(['systemctl','start','server-network-assist-relay.service'])
            if run(['systemctl','show','server-network-assist-relay.service','-p','Result','--value']).stdout.strip()!='success':
                raise RuntimeError('Relay reconciliation failed')
        for row in manifest:
            if hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()!=row['expected_sha256']:
                raise RuntimeError('Deployed file verification failed')
    except Exception:
        service('stop')
        if source:run(['systemctl','stop','server-network-assist-relay.timer'],check=False)
        for row in reversed(manifest):
            path=Path(row['path'])
            if row['existed']:write(path,(args.backup/row['backup']).read_bytes(),row)
            elif path.exists():path.unlink()
        for db,columns in schema_before.items():
            if 'egress_policy' not in columns:
                connection=sqlite3.connect(db)
                try:
                    now={row[1] for row in connection.execute('PRAGMA table_info(line_grants)')}
                    if 'egress_policy' in now:connection.execute('ALTER TABLE line_grants DROP COLUMN egress_policy');connection.commit()
                finally:connection.close()
        if unit_was_active:service('start')
        if relay_was_active:run(['systemctl','start','server-network-assist-relay.timer'])
        raise
    print(json.dumps({'ok':True,'scope':args.scope,'files_verified':len(manifest),'backup':str(args.backup),'default_egress_changed':False}))


if __name__=='__main__':main()
