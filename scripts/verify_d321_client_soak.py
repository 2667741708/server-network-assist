"""D321-only real-client soak monitor. No network configuration is changed by monitoring."""
import concurrent.futures
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

DATA = Path(r'C:\ProgramData\ServerNetworkAssist\client\S-1-5-21-1446874470-693334550-1715965273-1001\gui-window-test-20260917')
ROOT = Path(r'C:\Users\86133\d321-pure-steady-20260918')
FLAGS = 0x08000000
if socket.gethostname().upper() != 'DESKTOP-TD6B9GN':
    raise RuntimeError('D321 only')
ROOT.mkdir(exist_ok=True)
def api(ep, value=None):
    i = json.loads((DATA/'client-instance.json').read_text())
    base = 'http://127.0.0.1:'+str(i['port'])
    h = {'X-Client-Token':i['token'],'Origin':base,'Content-Type':'application/json'}
    r = urllib.request.Request(base+ep,headers=h,data=None if value is None else json.dumps(value).encode())
    return json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(r,timeout=90 if value is not None else 5))
def state():
    try:
        v=api('/api/state')
        lease=(v.get('online_service') or {}).get('lease') or {}
        a=v.get('active') or {}
        return {'active':bool(a),'tunnel':a.get('tunnel'),'generation':a.get('counter_generation'),
                'recovering':v.get('recovering'),'error':v.get('error'),
                'lease_id':lease.get('id'),'expires_at':lease.get('expires_at'),
                'events':v.get('network_events')}
    except Exception as e:return {'api_error':type(e).__name__}
def tcp(host,port):
    try:
        with socket.create_connection((host,port),timeout=2):return True
    except OSError:return False
def curl(url):
    try:
        # Check response headers, not a full page download under a short deadline.
        r=subprocess.run(['curl.exe','--head','--noproxy','*','--connect-timeout','2','--max-time','8','-sS','-o','NUL','-w','%{http_code}',url],capture_output=True,timeout=10,creationflags=FLAGS)
        return {'ok':r.returncode==0,'code':r.stdout.decode(errors='replace'),'exit':r.returncode}
    except subprocess.TimeoutExpired:return {'ok':False,'exit':'timeout'}
def wgstate():
    result={}
    try:
        n=(state().get('tunnel') or '')
        if not n.startswith('sna'):return {'present':False}
        for field in ['latest-handshakes','transfer']:
            r=subprocess.run([r'C:\Program Files\WireGuard\wg.exe','show',n,field],capture_output=True,timeout=3,creationflags=FLAGS)
            result[field]=[x.split()[1:] for x in r.stdout.decode().splitlines()]
            result['present']=r.returncode==0
        return result
    except Exception as e:return {'probe_error':type(e).__name__}
def snapshot():
    p=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command','Get-NetRoute -AddressFamily IPv4 | Select-Object DestinationPrefix,NextHop,InterfaceIndex,RouteMetric | ConvertTo-Json'],capture_output=True,timeout=20,creationflags=FLAGS)
    return p.stdout.decode(errors='replace')
def publish(report):
    tmp=ROOT/'report.tmp'
    tmp.write_text(json.dumps(report,ensure_ascii=True))
    tmp.replace(ROOT/'report.json')
action=sys.argv[1]
if action=='connect':
    s=state()
    (ROOT/'baseline-routes.json').write_text(snapshot())
    if s.get('active'):raise RuntimeError('Already active; inspect first')
    catalog=api('/api/online/subscription')
    route=next(x for x in catalog['routes'] if x['available'])
    try:
        v=api('/api/online/connect',{'grant_id':route['id']})
        print(json.dumps({'ok':v.get('ok'),'tunnel':v.get('tunnel'),'state':state()}))
    except urllib.error.HTTPError as e:
        print(e.read().decode())
        raise
elif action=='launch':
    log=open(ROOT/'monitor.log','ab')
    p=subprocess.Popen([sys.executable,__file__,'monitor'],stdout=log,stderr=log,creationflags=FLAGS,close_fds=True)
    print(json.dumps({'pid':p.pid,'report':str(ROOT/'report.json')}))
elif action=='status':
    v=json.loads((ROOT/'report.json').read_text())
    print(json.dumps({k:x for k,x in v.items() if k!='samples'}))
elif action=='monitor':
    start=time.monotonic(); rows=[]; failures={}; transitions=[]; previous={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        while time.monotonic()-start<1260:
            elapsed=round(time.monotonic()-start,1)
            jobs={'source':pool.submit(tcp,'10.20.32.13',9182),
                  'campus5080':pool.submit(tcp,'10.20.32.12',22),
                  'public_tcp':pool.submit(tcp,'1.1.1.1',443)}
            if len(rows)%3==0:jobs['client']=pool.submit(state)
            if len(rows)%5==0:
                jobs['wireguard']=pool.submit(wgstate)
                jobs['baidu']=pool.submit(curl,'https://www.baidu.com/')
                jobs['github']=pool.submit(curl,'https://github.com/')
            row={'time':int(time.time()),'elapsed':elapsed}
            for key,job in jobs.items():
                try:row[key]=job.result()
                except Exception as e:row[key]={'probe_error':type(e).__name__}
            if 'client' in row:
                s=row['client']
                signature={k:s.get(k) for k in ['active','recovering','generation','lease_id','expires_at','error','api_error']}
                if signature!=previous:
                    transitions.append({'elapsed':elapsed,'state':signature})
                    previous=signature
            for key,val in row.items():
                if val is False or isinstance(val,dict) and val.get('ok') is False:
                    failures[key]=failures.get(key,0)+1
            rows.append(row)
            publish({'complete':False,'elapsed':elapsed,'count':len(rows),'failures':failures,
                     'transitions':transitions,'latest':row,'samples':rows})
            time.sleep(max(0,2-(time.monotonic()-start-elapsed)))
    v={'complete':True,'elapsed':round(time.monotonic()-start,1),'count':len(rows),'failures':failures,
       'transitions':transitions,'latest':rows[-1],'samples':rows}
    publish(v)
    (ROOT/'final-routes.json').write_text(snapshot())
else:raise RuntimeError('unknown action')
