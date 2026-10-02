"""Read-only C201 proxy/DNS path checks. Never output proxy credentials."""
import json
import argparse
from pathlib import Path
import socket
import subprocess
import urllib.request
import yaml

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host',choices=('github.com','chatgpt.com','gemini.google.com','api.openai.com'))
args=parser.parse_args()

config=yaml.safe_load(Path('/etc/mihomo/config.yaml').read_text())
port=config.get('mixed-port') or config.get('port')
controller=str(config.get('external-controller','127.0.0.1:9090'))
if controller.startswith('0.0.0.0:'):
    controller='127.0.0.1:'+controller.rsplit(':',1)[1]
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
headers={'Authorization':'Bearer '+str(config.get('secret',''))}
try:
    request=urllib.request.Request('http://'+controller+'/proxies',headers=headers)
    with opener.open(request,timeout=10) as response:
        proxies=json.load(response)['proxies']
    groups=[{'name':g['name'],'type':proxies.get(g['name'],{}).get('type'),
        'selected':proxies.get(g['name'],{}).get('now'),
        'options':proxies.get(g['name'],{}).get('all',[])[:8]} for g in config.get('proxy-groups',[])]
except Exception as error:
    groups={'error':type(error).__name__}
print(json.dumps({'mode':config.get('mode'),'mixed_port':port,'groups':groups,
    'sniffer':config.get('sniffer'),'tun':config.get('tun')}),flush=True)
for host in ([args.host] if args.host else []):
    try:
        addresses=sorted({r[4][0] for r in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})
    except OSError as error:
        addresses={'error':str(error)}
    print(json.dumps({'host':host,'system_dns':addresses}),flush=True)
    for via in ('tun','explicit_proxy'):
        command=['curl','--silent','--show-error','--output','/dev/null',
            '--write-out','HTTP %{http_code} TLS %{ssl_verify_result} IP %{remote_ip}',
            '--max-time','8']
        if via=='explicit_proxy':
            command+=['--proxy',f'http://127.0.0.1:{port}']
        else:
            command+=['--noproxy','*']
        command+=['https://'+host]
        result=subprocess.run(command,capture_output=True,text=True,timeout=12)
        print(json.dumps({'host':host,'via':via,'code':result.returncode,
            'result':result.stdout,'error':result.stderr[:700]}),flush=True)
