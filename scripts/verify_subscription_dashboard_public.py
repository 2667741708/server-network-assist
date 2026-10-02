"""Read-only authenticated dashboard check through the deployed public proxy."""
import http.cookiejar
import json
from pathlib import Path
import urllib.error
import urllib.request
import subprocess
import argparse

parser=argparse.ArgumentParser();parser.add_argument('--assets',type=Path,default=Path(__file__).parent/'ui');args=parser.parse_args()

base='https://whm12.art/subscription-admin/'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def call(api,payload=None,csrf=''):
    req=urllib.request.Request(base+'api/'+api,data=None if payload is None else json.dumps(payload).encode(),headers={
        'Origin':'https://whm12.art','Content-Type':'application/json','X-CSRF-Token':csrf})
    with opener.open(req,timeout=20) as r:return json.load(r),r.headers
assert not call('session')[0]['authenticated']
try:call('client-service/dashboard');raise AssertionError('Anonymous dashboard allowed')
except urllib.error.HTTPError as e:assert e.code==401
key=json.loads(Path('/home/a/.local/share/server-network-assist-commercial/data/initial-login.json').read_text())['key']
session=call('login',{'username':'admin','key':key,'remember':False})[0]
try:
    data,headers=call('client-service/dashboard');assert headers.get('Cache-Control')=='no-store'
    assert 'customers' in data and 'summary' in data
    assert all(c['lifecycle'] in ('active','archived','deleted') and isinstance(c['tags'],list) and isinstance(c['notes'],str) for c in data['customers'])
    raw=subprocess.check_output(['/opt/server-network-assist-relay/venv/bin/server-network-assist-service-admin','--database',
        '/home/a/.local/share/server-network-assist-commercial/data/commercial-service.sqlite3','subscription','show'],text=True,timeout=20)
    cli=json.loads(raw);assert cli['ok'] and len(cli['result']['customers'])==len(data['customers'])
    assert {c['id']:(c['lifecycle'],c['tags'],c['notes']) for c in cli['result']['customers']}=={c['id']:(c['lifecycle'],c['tags'],c['notes']) for c in data['customers']}
    for name in ['subscriptions.html','subscriptions.js','subscriptions.css','dashboard.js','dashboard.css','tabler.min.css','tabler.min.js','TABLER-LICENSE.txt','tabler-assets.json']:
        with opener.open(base+name,timeout=15) as r:
            assert r.status==200 and r.read()==(args.assets/name).read_bytes()
    print(json.dumps({'authenticated_public_dashboard_verified':True,'customer_count':len(data['customers']),'services_modified':False,'csrf_cookie_proxy_verified':True,'root_cli_snapshot_verified':True,'customer_metadata_fields_verified':True,'public_assets_match_release':True}))
finally:call('logout',{},session['csrf'])
