"""Narrow subscription-control deployment with candidate test and automatic rollback."""
import argparse, hashlib, http.cookiejar, json, os, shutil, socket, sqlite3, subprocess, time, urllib.error, urllib.request
from pathlib import Path
ROOT=Path('/home/a/.local/lib/python3.13/site-packages/server_network_assist')
DATA=Path('/home/a/.local/share/server-network-assist-commercial/data')
NAMES=['client_store.py','subscription_archive.py','ui/subscriptions.html','ui/subscriptions.js','ui/dashboard.js']
def digest(body):return hashlib.sha256(body).hexdigest()
def network():
    result={}
    for key,args in [('routes',['ip','-j','-4','route','show','table','all']),('rules',['ip','-j','rule','show'])]:
        rows=json.loads(subprocess.check_output(args,text=True))
        for row in rows:
            for field in ['expires','cache']:row.pop(field,None)
        result[key]=digest(json.dumps(rows,sort_keys=True).encode())
    for unit in ['mihomo.service','wg-quick@sna-commercial.service','server-network-assist-relay.timer']:
        result[unit]=subprocess.check_output(['systemctl','show',unit,'-p','InvocationID','-p','MainPID','-p','ActiveState'],text=True)
    result['campus_tcp']={}
    for ip in ['10.20.32.12','10.20.32.14','10.20.31.134']:
        try:
            with socket.create_connection((ip,22),timeout=2):result['campus_tcp'][ip]=True
        except OSError:result['campus_tcp'][ip]=False
    return result
def identities():
    with sqlite3.connect(DATA/'commercial-service.sqlite3') as db:
        rows=[db.execute(query).fetchall() for query in [
            'SELECT id,plan_id,enabled FROM customers ORDER BY id',
            'SELECT * FROM plans ORDER BY id',
            'SELECT id,customer_id,enabled,wireguard_public_key FROM devices ORDER BY id',
            'SELECT * FROM line_grants ORDER BY id',
            'SELECT * FROM enrollment_tokens ORDER BY digest',
            'SELECT * FROM subscription_addresses ORDER BY customer_id']]
    return digest(json.dumps(rows,default=str).encode())
def prepare(stage):
    assert subprocess.check_output(['hostname'],text=True).strip()=='a-MS-7E06'
    changes=json.loads((stage/'changes.json').read_text())
    candidate=stage/'candidate';candidate.mkdir(exist_ok=True)
    bodies={n:(ROOT/n).read_text() for n in NAMES}
    baseline={n:digest((ROOT/n).read_bytes()) for n in NAMES}
    for patch in changes['patches']:
        assert patch['path'] in NAMES[:2]
        name=patch['path']
        assert bodies[name].count(patch['old'])==1,'Live patch context changed: '+name
        bodies[name]=bodies[name].replace(patch['old'],patch['new'],1)
    assert set(changes['assets'])==set(NAMES[2:])
    bodies.update(changes['assets'])
    for name,body in bodies.items():
        (candidate/name).parent.mkdir(parents=True,exist_ok=True)
        (candidate/name).write_text(body)
        if name.endswith('.py'):compile(body,name,'exec')
    runtime=stage/'test-runtime/server_network_assist'
    shutil.copytree(ROOT,runtime,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','ui','subscription_admin_ui'))
    for name in NAMES[:2]:shutil.copy2(candidate/name,runtime/name)
    (stage/'prepared.json').write_text(json.dumps({'baseline':baseline,'candidate':{n:digest((candidate/n).read_bytes()) for n in NAMES}}))
    print(json.dumps({'prepared':True,'live_files_changed':False,'runtime':str(runtime.parent)}))
def verify(stage,base='http://127.0.0.1:9182/',origin='http://10.20.32.13:9182'):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def call(path,payload=None,csrf=''):
        req=urllib.request.Request(base+'api/'+path,data=None if payload is None else json.dumps(payload).encode(),headers={
            'Origin':origin,'Content-Type':'application/json','X-CSRF-Token':csrf})
        with opener.open(req,timeout=25) as resp:return json.load(resp),resp.headers
    for attempt in range(20):
        try:
            assert call('session')[0]['authenticated'] is False;break
        except urllib.error.URLError:
            if attempt==19:raise
            time.sleep(.2)
    try:call('client-service/dashboard');raise AssertionError('Anonymous dashboard accepted')
    except urllib.error.HTTPError as exc:assert exc.code==401
    session=call('login',{'username':'admin','key':json.loads((DATA/'initial-login.json').read_text())['key'],'remember':False})[0]
    try:
        data,headers=call('client-service/dashboard')
        assert headers.get('Cache-Control')=='no-store'
        assert isinstance(data['customers'],list)
        for name in NAMES[2:]:
            with opener.open(base+Path(name).name,timeout=8) as response:
                assert digest(response.read())==digest((stage/'candidate'/name).read_bytes()),name
        return {'authenticated_api':True,'anonymous_dashboard_denied':True,'assets_verified':True,'customer_count':len(data['customers']),'real_customer_services_modified':False}
    finally:call('logout',{},session['csrf'])
def atomic(path,body):
    temp=path.with_name(path.name+'.controls-new')
    temp.write_bytes(body);temp.chmod(path.stat().st_mode & 0o777);os.replace(temp,path)
def apply(stage):
    prepared=json.loads((stage/'prepared.json').read_text())
    for name in NAMES:
        assert digest((ROOT/name).read_bytes())==prepared['baseline'][name],'Baseline changed: '+name
        assert digest((stage/'candidate'/name).read_bytes())==prepared['candidate'][name]
    baseline=network();before=identities()
    backup=stage/('backup-'+str(time.time_ns()));backup.mkdir(mode=0o700)
    originals={name:(ROOT/name).read_bytes() for name in NAMES}
    for name,body in originals.items():
        (backup/name).parent.mkdir(parents=True,exist_ok=True);(backup/name).write_bytes(body)
    try:
        for name in NAMES:atomic(ROOT/name,(stage/'candidate'/name).read_bytes())
        subprocess.run(['systemctl','--user','restart','sna-commercial.service'],check=True,timeout=20)
        result=verify(stage)
        assert identities()==before,'Customer contract or identity changed'
        assert network()==baseline,'Protected routes, proxy, relay, or campus TCP changed'
    except BaseException:
        for name,body in originals.items():atomic(ROOT/name,body)
        subprocess.run(['systemctl','--user','restart','sna-commercial.service'],check=True,timeout=20)
        raise
    result.update(deployed=True,backup=str(backup),protected_network_unchanged=True,customer_contracts_unchanged=True,campus_tcp=baseline['campus_tcp'])
    (stage/'applied.json').write_text(json.dumps(result))
    print(json.dumps(result))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','apply','verify','verify-public']);p.add_argument('--stage',type=Path,required=True);args=p.parse_args()
    if args.action=='verify-public':print(json.dumps(verify(args.stage,'https://whm12.art/subscription-admin/','https://whm12.art')))
    else:
        result={'prepare':prepare,'apply':apply,'verify':verify}[args.action](args.stage)
        if args.action=='verify':print(json.dumps(result))
