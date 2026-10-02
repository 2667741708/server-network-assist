"""Prepare/apply a narrow 4090 control-plane upgrade with file rollback.

Run prepare, candidate tests, then apply as separate commands. Never restore the
SQLite backup over a live database: leases and other users may have changed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import http.cookiejar

ROOT = Path('/home/a/.local/lib/python3.13/site-packages/server_network_assist')
DATA = Path('/home/a/.local/share/server-network-assist-commercial/data')
BASELINES = {'app.py':'2fb8cf8fa203fafc29418e935189e760b4795be775f9435ca29817cb6175ae7d',
    'client_store.py':'67ead62209bb4009f078497b3da9513e28eb54e6f04206ba52b295f193affc5e'}
NAMES = ['app.py','client_store.py','subscription_archive.py',
    'ui/subscriptions.html','ui/subscriptions.js','ui/subscriptions.css']


def digest(body):
    return hashlib.sha256(body).hexdigest()


def replace_once(text, old, new):
    assert text.count(old) == 1, 'Patch context changed'
    return text.replace(old, new, 1)


def probe():
    with socket.create_connection(('10.201.250.1',9180),timeout=5):
        pass
    routes = json.loads(subprocess.run(['ip','-j','-4','route','show','table','all'],
        check=True,capture_output=True,text=True).stdout)
    stable = [{k:v for k,v in row.items() if k in ('dst','gateway','dev','table','metric','prefsrc','protocol','scope','type')} for row in routes]
    services = {}
    for unit in ['wg-quick@sna-commercial.service','server-network-assist-client-1000.service']:
        services[unit] = subprocess.run(['systemctl','show',unit,'-p','InvocationID','-p','MainPID','-p','ActiveState'],
            check=True,capture_output=True,text=True).stdout
    return {'route_hash':digest(json.dumps(stable,sort_keys=True).encode()),'protected_services':services,'cloud_wg_tcp_9180':True}


def prepare(stage):
    for name, expected in BASELINES.items():
        assert digest((ROOT/name).read_bytes()) == expected, 'Remote baseline changed: '+name
    app = (ROOT/'app.py').read_text()
    start = app.index("            elif action == 'subscription-generate':")
    end = app.index("            elif action == 'customer-enable':",start)
    app = app[:start] + (stage/'app-action-block.txt').read_text() + app[end:]
    app = replace_once(app,"        if path == '/api/client-service':\n",
        "        if path == '/api/client-service':\n            from .subscription_archive import SubscriptionArchive\n")
    app = replace_once(app,"                'customers': state.client_service.list_customers(),\n",
        "                'customers': state.client_service.list_customers(),\n                'subscription_addresses': SubscriptionArchive(state.client_service, state.cipher).statuses(),\n")
    store = (ROOT/'client_store.py').read_text()
    store = replace_once(store,'from typing import Any\n','from typing import Any, Callable\n')
    store = replace_once(store,'                                      upload_bps: int | None = 10000000, now: int | None = None) -> dict:\n',
        '                                      upload_bps: int | None = 10000000, now: int | None = None,\n                                      archive: Callable[[sqlite3.Connection, str, str, int], None] | None = None) -> dict:\n')
    old = "                       (secret_digest(token, 'enrollment'), customer_id, now + 86400, None, now))\n"
    store = replace_once(store,old,old+'            if archive is not None:\n                archive(db, token, customer_id, now)\n')
    candidate = stage/'candidate'
    candidate.mkdir(exist_ok=True)
    (candidate/'app.py').write_text(app)
    (candidate/'client_store.py').write_text(store)
    shutil.copy2(stage/'subscription_archive.py',candidate/'subscription_archive.py')
    (candidate/'ui').mkdir(exist_ok=True)
    for name in NAMES[3:]:
        shutil.copy2(stage/Path(name).name,candidate/name)
    for name in NAMES[:3]:
        compile((candidate/name).read_bytes(),name,'exec')
    runtime = stage/'test-runtime/server_network_assist'
    shutil.copytree(ROOT,runtime,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    for name in NAMES:
        shutil.copy2(candidate/name,runtime/name)
    report = {'baseline_hashes':BASELINES,'candidate_hashes':{name:digest((candidate/name).read_bytes()) for name in NAMES},'network_baseline':probe()}
    (stage/'prepared.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'prepared':True,'candidate_compiled':True,'test_runtime':str(runtime.parent),'live_files_changed':False}))


def atomic(path, body):
    temporary = path.with_name(path.name+'.archive-new')
    temporary.write_bytes(body)
    temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
    os.replace(temporary,path)


def verify_api():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    base = 'http://127.0.0.1:9182/api/'
    def call(path,payload=None,csrf=''):
        headers={'Origin':'http://10.20.32.13:9182','X-CSRF-Token':csrf,'Content-Type':'application/json'}
        request=urllib.request.Request(base+path,data=None if payload is None else json.dumps(payload).encode(),headers=headers)
        with opener.open(request,timeout=8) as response:
            return json.load(response)
    for attempt in range(30):
        try:
            assert call('session')['authenticated'] is False
            break
        except (OSError, urllib.error.URLError):
            if attempt==29:
                raise
            time.sleep(0.2)
    credentials = json.loads((DATA/'initial-login.json').read_text())
    session = call('login',{'username':'admin','key':credentials['key'],'remember':False})
    try:
        listing = call('client-service')
        assert 'subscription_addresses' in listing
        assert any(s['id']=='c201-4090-commercial' for s in listing['sources'])
        for status in listing['subscription_addresses']:
            assert 'url' not in status and 'encrypted_url' not in status and 'digest' not in status
        legacy = next((s for s in listing['subscription_addresses'] if not s['available']),None)
        if legacy:
            try:
                call('client-service/action',{'action':'subscription-view','id':legacy['customer_id']},session['csrf'])
                raise AssertionError('Legacy URL cannot be reconstructed')
            except urllib.error.HTTPError as error:
                assert error.code==400 and '无法还原' in json.load(error)['error']
        return {'authenticated_source_listing':True,'legacy_view_checked':bool(legacy),'customer_count':len(listing['customers']),'new_enrollment_requested':False}
    finally:
        call('logout',{},session['csrf'])


def apply(stage):
    prepared=json.loads((stage/'prepared.json').read_text())
    for name,expected in BASELINES.items():
        assert digest((ROOT/name).read_bytes())==expected,'Remote file changed since prepare: '+name
    candidate=stage/'candidate'
    for name,expected in prepared['candidate_hashes'].items():
        assert digest((candidate/name).read_bytes())==expected,'Candidate changed: '+name
    baseline=probe()
    backup=stage/('backup-'+str(time.time_ns()))
    backup.mkdir(mode=0o700)
    originals={name:(ROOT/name).read_bytes() if (ROOT/name).exists() else None for name in NAMES}
    for name,body in originals.items():
        if body is not None:
            (backup/name).parent.mkdir(parents=True,exist_ok=True)
            (backup/name).write_bytes(body)
    with sqlite3.connect(DATA/'commercial-service.sqlite3') as source:
        with sqlite3.connect(backup/'commercial-service.sqlite3') as target:
            source.backup(target)
    (backup/'commercial-service.sqlite3').chmod(0o600)
    try:
        for name in NAMES:
            atomic(ROOT/name,(candidate/name).read_bytes())
        subprocess.run(['systemctl','--user','restart','sna-commercial.service'],check=True,timeout=20)
        assert subprocess.run(['systemctl','--user','is-active','sna-commercial.service'],check=True,capture_output=True,text=True).stdout.strip()=='active'
        verified=verify_api()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for name in NAMES[3:]:
            with opener.open('http://127.0.0.1:9182/'+Path(name).name,timeout=8) as response:
                assert response.read()==(candidate/name).read_bytes()
        after=probe()
        assert after==baseline,'Protected network or service changed'
    except BaseException:
        for name,body in originals.items():
            if body is None:
                (ROOT/name).unlink(missing_ok=True)
            else:
                atomic(ROOT/name,body)
        subprocess.run(['systemctl','--user','restart','sna-commercial.service'],check=True,timeout=20)
        raise
    report={'deployed':True,'backup':str(backup),'only_commercial_control_api_restarted':True,'protected_network_and_services_unchanged':True,**verified}
    (stage/'applied.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','apply'])
    parser.add_argument('--stage',type=Path,required=True)
    args=parser.parse_args()
    {'prepare':prepare,'apply':apply}[args.action](args.stage)
