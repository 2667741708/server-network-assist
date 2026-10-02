"""Bounded compressed transport chunks for prebuilt admin assets."""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path

NAMES=['subscriptions.html','subscriptions.js','subscriptions.css','dashboard.js','dashboard.css',
       'tabler.min.css','tabler.min.js','TABLER-LICENSE.txt','tabler-assets.json']
p=argparse.ArgumentParser();p.add_argument('--asset',choices=NAMES);p.add_argument('--part',type=int,default=0);a=p.parse_args()
ui=Path(__file__).resolve().parents[1]/'src/server_network_assist/subscription_admin_ui'
def metadata(n):
    raw=(ui/n).read_bytes();encoded=base64.b64encode(gzip.compress(raw,mtime=0)).decode()
    return {'name':n,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'parts':(len(encoded)+19999)//20000},encoded
if a.asset:
    info,encoded=metadata(a.asset);assert 0<=a.part<info['parts']
    print(json.dumps(info|{'part':a.part,'content':encoded[a.part*20000:(a.part+1)*20000]}))
else:print(json.dumps({n:metadata(n)[0] for n in NAMES}))
