"""Prepare/apply only static admin assets with rollback and no service restart."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

ROOT=Path('/home/a/.local/lib/python3.13/site-packages/server_network_assist')
NAMES=['subscriptions.html','subscriptions.js','subscriptions.css','dashboard.js','dashboard.css',
       'tabler.min.css','tabler.min.js','TABLER-LICENSE.txt','tabler-assets.json']
def digest(body):return hashlib.sha256(body).hexdigest()
def probe():
    out={}
    for key,args in [('routes',['ip','-j','-4','route','show','table','all']),('rules',['ip','-j','rule','show'])]:
        rows=json.loads(subprocess.check_output(args,text=True))
        for r in rows:
            for k in ['expires','cache']:r.pop(k,None)
        out[key]=digest(json.dumps(rows,sort_keys=True).encode())
    for unit in ['mihomo.service','wg-quick@sna-commercial.service','server-network-assist-relay.timer']:
        out[unit]=subprocess.check_output(['systemctl','show',unit,'-p','InvocationID','-p','MainPID','-p','ActiveState'],text=True)
    out['commercial_api']=subprocess.check_output(['systemctl','--user','show','sna-commercial.service','-p','InvocationID','-p','MainPID','-p','ActiveState'],text=True)
    out['backend_hashes']={n:digest((ROOT/n).read_bytes()) for n in ['app.py','client_store.py','subscription_dashboard.py']}
    return out
def atomic(path,body):
    tmp=path.with_name(path.name+'.redesign-new');tmp.write_bytes(body)
    tmp.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644);os.replace(tmp,path)
def prepare(stage):
    assert subprocess.check_output(['hostname'],text=True).strip()=='a-MS-7E06'
    manifest=json.loads((stage/'manifest.json').read_text());assert set(manifest)==set(NAMES)
    candidate=stage/'candidate';candidate.mkdir(exist_ok=True)
    for n in NAMES:
        raw=gzip.decompress((stage/(n+'.gz')).read_bytes())
        assert len(raw)==manifest[n]['bytes'] and digest(raw)==manifest[n]['sha256'],n
        (candidate/n).write_bytes(raw)
    baseline={n:digest((ROOT/'ui'/n).read_bytes()) if (ROOT/'ui'/n).exists() else None for n in NAMES}
    (stage/'prepared.json').write_text(json.dumps({'baseline':baseline,'manifest':manifest,'network':probe()},indent=2))
    print(json.dumps({'prepared':True,'asset_count':len(NAMES),'live_files_changed':False}))
def apply(stage):
    prepared=json.loads((stage/'prepared.json').read_text());manifest=prepared['manifest']
    for n in NAMES:
        actual=digest((ROOT/'ui'/n).read_bytes()) if (ROOT/'ui'/n).exists() else None
        assert actual==prepared['baseline'][n],'Live file changed: '+n
        assert digest((stage/'candidate'/n).read_bytes())==manifest[n]['sha256']
    baseline=probe();backup=stage/('backup-'+str(time.time_ns()));backup.mkdir(mode=0o700)
    originals={n:(ROOT/'ui'/n).read_bytes() if (ROOT/'ui'/n).exists() else None for n in NAMES}
    for n,b in originals.items():
        if b is not None:(backup/n).write_bytes(b)
    try:
        for n in NAMES:atomic(ROOT/'ui'/n,(stage/'candidate'/n).read_bytes())
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for n in NAMES:
            with opener.open('http://127.0.0.1:9182/'+n,timeout=8) as r:assert digest(r.read())==manifest[n]['sha256'],n
        with opener.open('http://127.0.0.1:9182/api/session',timeout=5) as r:assert json.load(r)['authenticated'] is False
        try:opener.open('http://127.0.0.1:9182/api/client-service/dashboard',timeout=5);raise AssertionError('Anonymous dashboard allowed')
        except urllib.error.HTTPError as e:assert e.code==401
        assert probe()==baseline,'Protected networking/backend/service changed'
    except BaseException:
        for n,b in originals.items():
            if b is None:(ROOT/'ui'/n).unlink(missing_ok=True)
            else:atomic(ROOT/'ui'/n,b)
        raise
    result={'static_redesign_deployed':True,'assets_verified':len(NAMES),'backup':str(backup),'services_restarted':False,'protected_network_backend_services_unchanged':True}
    (stage/'applied.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','apply']);p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
    {'prepare':prepare,'apply':apply}[a.action](a.stage)
