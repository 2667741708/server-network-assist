"""Owner-authorized subscription issuance; read credentials only on source host.

--inspect authenticates and reads only. --create creates exactly two physical
customers and one source-proxy customer; refuses to run if proxy is unavailable.
No customer enrollment/lease/network connection is performed.
"""
import argparse
import http.cookiejar
import json
from pathlib import Path
import urllib.request

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--create',action='store_true')
parser.add_argument('--source-id')
args=parser.parse_args()
base='http://10.201.250.1:9180/subscription-admin/'
origin='http://10.201.250.1:9180'
credentials=json.loads(Path('/home/a/.local/share/server-network-assist-commercial/data/initial-login.json').read_text())
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def call(path,payload=None,csrf=''):
    req=urllib.request.Request(base+'api/'+path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Origin':origin,'Content-Type':'application/json','X-CSRF-Token':csrf})
    with opener.open(req,timeout=25) as response:
        return json.load(response)
session=call('login',{'username':credentials['username'],'key':credentials['key'],'remember':False})
try:
    dashboard=call('client-service/dashboard')
    service=call('client-service')
    if not args.create:
        print(json.dumps({'authenticated_requested_gateway':True,
            'username':credentials['username'],'management_key':credentials['key'],
            'dashboard_fields':list(dashboard),'sources':service['sources'],
            'source_proxy':service['source_proxy'],'customer_count':len(dashboard['customers'])}))
    else:
        assert args.source_id
        assert service['source_proxy']['available'],'Source proxy is unavailable; no customer issued.'
        result=[]
        for index,mode in enumerate(('physical','physical','source_proxy'),1):
            payload={'action':'subscription-generate', 'name':f'20260917-测试-{index}-{mode}',
                'quota_gb':None,'source_ids':[args.source_id],
                'proxy_source_ids':[args.source_id] if mode=='source_proxy' else [],
                'base_url':'http://10.20.32.13:9182',
                'download_bps':50000000,'upload_bps':10000000}
            existing=[c for c in service['customers'] if c['display_name']==payload['name']]
            assert len(existing)<=1,'Ambiguous existing customer; no automatic duplicate issuance.'
            if existing:
                issued=call('client-service/action',{'action':'subscription-view','id':existing[0]['id']},session['csrf'])
            else:
                issued=call('client-service/action',payload,session['csrf'])
            result.append(issued|{'mode':mode,'name':payload['name']})
        after=call('client-service/dashboard')
        persisted=call('client-service')
        verified=[]
        for issued in result:
            customer=next(c for c in after['customers'] if c['id']==issued['customer_id'])
            grants=[g for g in persisted['grants'] if g['customer_id']==customer['id']]
            assert len(grants)==1
            policy=json.loads(grants[0]['egress_policy'])
            assert policy['egress_mode']==issued['mode']
            assert grants[0]['egress_interface']==('enp4s0' if issued['mode']=='physical' else 'Meta')
            status=next(s for s in persisted['subscription_addresses'] if s['customer_id']==customer['id'])
            assert status['status']=='ready'
            assert customer['devices']==[]
            assert customer['plan']['quota_bytes'] is None
            assert customer['plan']['download_bps']==50000000
            assert customer['plan']['upload_bps']==10000000
            issued['expires_at']=grants[0]['expires_at']
            verified.append({'customer_id':customer['id'],'mode':policy['egress_mode'],
                'interface':grants[0]['egress_interface'],'address':grants[0]['allocated_address'],
                'subscription_status':status['status'],'device_count':len(customer['devices'])})
        print(json.dumps({'subscriptions':result,'persisted_customers':verified,
            'customer_count':len(after['customers']),'devices_redeemed':False,
            'client_network_changed':False}))
finally:
    call('logout',{},session['csrf'])
