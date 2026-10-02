"""Narrow control-plane dashboard deployment; protect live relay and routing."""
import argparse
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request

ROOT=Path('/home/a/.local/lib/python3.13/site-packages/server_network_assist')
DATA=Path('/home/a/.local/share/server-network-assist-commercial/data')
NAMES=['app.py','subscription_dashboard.py','client_service_admin.py',
       *['ui/'+n for n in ['subscriptions.html','subscriptions.js','subscriptions.css','dashboard.js','dashboard.css']]]

def digest(body): return hashlib.sha256(body).hexdigest()

def network():
    result={}
    for key,argv in [('routes',['ip','-j','-4','route','show','table','all']),('rules',['ip','-j','rule','show'])]:
        rows=json.loads(subprocess.run(argv,check=True,capture_output=True,text=True).stdout)
        for r in rows:
            for k in ('expires','cache'): r.pop(k,None)
        result[key]=digest(json.dumps(rows,sort_keys=True).encode())
    for unit in ['mihomo.service','wg-quick@sna-commercial.service','server-network-assist-relay.timer']:
        result[unit]=subprocess.run(['systemctl','show',unit,'-p','InvocationID','-p','MainPID','-p','ActiveState'],check=True,capture_output=True,text=True).stdout
    return result

def identity():
    with sqlite3.connect(DATA/'commercial-service.sqlite3') as db:
        devices=db.execute('SELECT id,customer_id,wireguard_public_key FROM devices ORDER BY id').fetchall()
        tokens=db.execute('SELECT digest,customer_id,used_at FROM enrollment_tokens ORDER BY digest').fetchall()
        addresses=db.execute('SELECT * FROM subscription_addresses ORDER BY customer_id').fetchall()
    return digest(json.dumps([devices,tokens,addresses],default=str).encode())

def atomic(path,body):
    tmp=path.with_name(path.name+'.dashboard-new');tmp.write_bytes(body)
    tmp.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644);os.replace(tmp,path)

def prepare(stage):
    assert subprocess.check_output(['hostname'],text=True).strip()=='a-MS-7E06'
    original=(ROOT/'app.py').read_text();app=original
    for start,end,file in [("        if path == '/api/client-service':","        if path == '/api/clash':",'get-block.txt'),
                           ("        if path == '/api/client-service/action':","        if path == '/api/host/save':",'post-block.txt')]:
        assert app.count(start)==1 and app.count(end)==1
        a=app.index(start);b=app.index(end,a);app=app[:a]+(stage/file).read_text()+app[b:]
    candidate=stage/'candidate';candidate.mkdir(exist_ok=True)
    (candidate/'app.py').write_text(app)
    for name in NAMES[1:]:
        (candidate/name).parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(stage/name,candidate/name)
    for name in NAMES[:3]:compile((candidate/name).read_bytes(),name,'exec')
    runtime=stage/'test-runtime/server_network_assist'
    shutil.copytree(ROOT,runtime,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','ui','subscription_admin_ui'))
    for name in NAMES[:3]:shutil.copy2(candidate/name,runtime/name)
    report={'baseline':{n:digest((ROOT/n).read_bytes()) if (ROOT/n).exists() else None for n in NAMES},
            'candidate':{n:digest((candidate/n).read_bytes()) for n in NAMES},'network':network()}
    (stage/'prepared.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'prepared':True,'compiled':True,'live_files_changed':False}))

def verify(stage):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def call(api,payload=None,csrf=''):
        request=urllib.request.Request('http://127.0.0.1:9182/api/'+api,data=None if payload is None else json.dumps(payload).encode(),
            headers={'Origin':'http://10.20.32.13:9182','Content-Type':'application/json','X-CSRF-Token':csrf})
        with opener.open(request,timeout=10) as r:return json.load(r),r.headers
    for attempt in range(30):
        try:
            assert call('session')[0]['authenticated'] is False;break
        except urllib.error.URLError:
            if attempt==29:raise
            time.sleep(.2)
    try:call('client-service/dashboard');raise AssertionError('anonymous dashboard accepted')
    except urllib.error.HTTPError as e:assert e.code==401
    credentials=json.loads((DATA/'initial-login.json').read_text())
    session=call('login',{'username':'admin','key':credentials['key'],'remember':False})[0]
    try:
        listing=call('client-service')[0];data,headers=call('client-service/dashboard')
        assert headers.get('Cache-Control')=='no-store'
        assert set(c['id'] for c in listing['customers'])==set(c['id'] for c in data['customers'])
        assert not any(k in json.dumps(data) for k in ['encrypted_url','enrollment_token','wireguard_public_key','device_public_key'])
        for n in NAMES[3:]:
            with opener.open('http://127.0.0.1:9182/'+Path(n).name,timeout=5) as response:assert response.read()==(stage/'candidate'/n).read_bytes()
        return {'authenticated_dashboard':True,'anonymous_dashboard_denied':True,'static_assets_verified':True,'customer_count':len(data['customers']),
                'summary':data['summary'],'real_customer_services_modified':False}
    finally:call('logout',{},session['csrf'])

def apply(stage):
    report=json.loads((stage/'prepared.json').read_text())
    for n,expected in report['baseline'].items():assert (digest((ROOT/n).read_bytes()) if (ROOT/n).exists() else None)==expected,'Live file changed: '+n
    for n,expected in report['candidate'].items():assert digest((stage/'candidate'/n).read_bytes())==expected
    baseline=network();identities=identity();backup=stage/('backup-'+str(time.time_ns()));backup.mkdir(mode=0o700)
    original={n:(ROOT/n).read_bytes() if (ROOT/n).exists() else None for n in NAMES}
    for n,body in original.items():
        if body is not None:(backup/n).parent.mkdir(parents=True,exist_ok=True);(backup/n).write_bytes(body)
    with sqlite3.connect(DATA/'commercial-service.sqlite3') as src:
        with sqlite3.connect(backup/'commercial-service.sqlite3') as dst:src.backup(dst)
    (backup/'commercial-service.sqlite3').chmod(0o600)
    try:
        for n in NAMES:atomic(ROOT/n,(stage/'candidate'/n).read_bytes())
        subprocess.run(['systemctl','--user','restart','sna-commercial.service'],check=True,timeout=20)
        result=verify(stage)
        assert network()==baseline,'Protected network/services changed'
        assert identity()==identities,'Enrollment or device identities changed'
    except BaseException:
        for n,body in original.items():
            if body is None:(ROOT/n).unlink(missing_ok=True)
            else:atomic(ROOT/n,body)
        subprocess.run(['systemctl','--user','restart','sna-commercial.service'],check=True,timeout=20)
        raise
    result.update(deployed=True,backup=str(backup),protected_routes_rules_proxy_relay_unchanged=True,subscription_and_device_identities_unchanged=True)
    (stage/'applied.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','apply','verify']);p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
    {'prepare':prepare,'apply':apply,'verify':verify}[a.action](a.stage)
