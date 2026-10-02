#!/usr/bin/env python3
"""Local authenticated management API acceptance; never print credentials."""
import http.cookiejar
import json
from pathlib import Path
import socket
import urllib.request


def main():
    if socket.gethostname()!='a-MS-7E06':raise RuntimeError('Authorized commercial source only')
    # Use the same source Origin as the configured public reverse proxy.
    base='http://10.20.32.13:9182'
    jar=http.cookiejar.CookieJar()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar))
    csrf=''
    def api(path,body=None):
        request=urllib.request.Request(base+'/api/'+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json','Origin':base,'X-CSRF-Token':csrf})
        with opener.open(request,timeout=25) as response:return json.load(response)
    credentials=json.loads(Path('/home/a/.local/share/server-network-assist-commercial/data/initial-login.json').read_text())
    csrf=api('login',credentials)['csrf']
    try:
        before=api('client-service')
        after_start=api('client-service/action',{'action':'source-proxy-start'})
        after=api('client-service')
        fields=lambda value:[(v['id'],v['egress_interface'],v['egress_policy']) for v in value['grants']]
        checks=dict(physical_source_default=all(v['egress_mode']=='physical' for v in after['sources']),
                    physical_grants=all(json.loads(v['egress_policy']).get('egress_mode')=='physical' for v in after['grants'] if v['endpoint']=='10.20.32.13:51910'),
                    real_start_endpoint=after_start['source_proxy']['available'],
                    starting_proxy_preserves_grant_modes=fields(before)==fields(after),
                    detects_local_source=bool(after['local_proxy_source_ids']))
        print(json.dumps(dict(ok=all(checks.values()),checks=checks)))
        if not all(checks.values()):raise SystemExit(1)
    finally:api('logout',{})


if __name__=='__main__':main()
