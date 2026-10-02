"""Verified dashboard stylesheet update; no service or networking operation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.request

p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--asset',choices=['dashboard.css','dashboard.js'],default='dashboard.css');a=p.parse_args()
path=Path('/home/a/.local/lib/python3.13/site-packages/server_network_assist/ui')/a.asset
expected=json.loads((a.stage/'prepared.json').read_text())['candidate']['ui/'+a.asset]
old=path.read_bytes();assert hashlib.sha256(old).hexdigest()==expected,'Asset changed independently'
backup=a.stage/('dashboard-asset-original-'+a.asset);backup.write_bytes(old)
new=(a.stage/'ui'/a.asset).read_bytes()
def atomic(body):
    tmp=path.with_name(path.name+'.asset-new');tmp.write_bytes(body);tmp.chmod(0o644);os.replace(tmp,path)
try:
    atomic(new);opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:9182/'+a.asset,timeout=5) as r:assert r.read()==new
except BaseException:atomic(old);raise
print(json.dumps({'dashboard_styles_deployed':True,'service_restarted':False,'backup':str(backup)}))
