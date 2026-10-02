"""Read-only, credential-free snapshot of the user's client/Clash coexistence."""
import json
import os
from pathlib import Path
import sys
import urllib.request
import urllib.parse
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import yaml
from server_network_assist import clash_control

result = {'clash': None, 'clients': []}
parser = argparse.ArgumentParser()
parser.add_argument('--delays', action='store_true')
measure = parser.parse_args().delays
paths = clash_control.candidates()
if paths:
    path = paths[0]
    doc = yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
    base, secret = clash_control.controller(path)
    row = {'path': str(path), 'file_interface': doc.get('interface-name'),
           'file_tun': {k: (doc.get('tun') or {}).get(k) for k in
                        ['enable', 'device', 'auto-detect-interface', 'auto-route']},
           'dns': {k: (doc.get('dns') or {}).get(k) for k in
                   ['enable', 'enhanced-mode', 'default-nameserver', 'nameserver',
                    'proxy-server-nameserver', 'respect-rules']},
           'proxy_count': len(doc.get('proxies') or []),
           'proxy_interface_overrides': [p.get('interface-name') for p in doc.get('proxies', [])
                                         if p.get('interface-name')],
           'provider_interface_overrides': [v.get('override', {}).get('interface-name')
                                          for v in (doc.get('proxy-providers') or {}).values()
                                          if v.get('override', {}).get('interface-name')]}
    if base:
        try:
            runtime = clash_control.api(base, secret, 'GET', '/configs')
            row['runtime'] = {k: runtime.get(k) for k in ['mode', 'interface-name', 'mixed-port']}
            row['runtime']['tun'] = {k: (runtime.get('tun') or {}).get(k) for k in
                                     ['enable', 'device', 'auto-detect-interface', 'auto-route']}
            proxies = clash_control.api(base, secret, 'GET', '/proxies').get('proxies') or {}
            row['runtime_proxy_count'] = len(proxies)
            row['recent_delay_counts'] = {'ok': sum(bool((p.get('history') or [{}])[-1].get('delay'))
                                                   for p in proxies.values()),
                                           'zero_or_unknown': sum(not bool((p.get('history') or [{}])[-1].get('delay'))
                                                                  for p in proxies.values())}
            if measure:
                candidates = [p for p in proxies.values() if p.get('type') not in
                              ['Selector', 'URLTest', 'Fallback', 'LoadBalance', 'Direct', 'Reject', 'Compatible', 'Relay']]
                candidates.sort(key=lambda p: not bool((p.get('history') or [{}])[-1].get('delay')))
                row['node_samples'] = []
                for index, node in enumerate(candidates[:3]):
                    query = urllib.parse.urlencode({'timeout': 6000, 'url': 'https://www.gstatic.com/generate_204'})
                    try:
                        value = clash_control.api(base, secret, 'GET', '/proxies/' + urllib.parse.quote(node['name'], safe='') + '/delay?' + query)
                        row['node_samples'].append({'sample': index + 1, 'type': node.get('type'), 'delay_ms': value.get('delay')})
                    except Exception as exc:
                        row['node_samples'].append({'sample': index + 1, 'type': node.get('type'), 'error_type': type(exc).__name__})
        except Exception as exc:
            row['controller_error_type'] = type(exc).__name__
    result['clash'] = row
root = Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData')) / 'ServerNetworkAssist/client'
for p in root.glob('*/**/client-instance.json'):
    try:
        info = json.loads(p.read_text(encoding='utf-8'))
        req = urllib.request.Request(f"http://127.0.0.1:{int(info['port'])}/api/state",
                                     headers={'X-Client-Token': info['token']})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as response:
            state = json.loads(response.read())
        active_path = p.parent / 'customer-active-line.json'
        active = json.loads(active_path.read_text(encoding='utf-8')) if active_path.exists() else {}
        row = {'data': str(p.parent), 'pid': info.get('pid'),
               'active': bool(state.get('active') or any(x.get('connected') for x in state.get('lines', []))),
               'configured': state.get('online_service', {}).get('configured'),
               'tunnel': active.get('tunnel')}
        own = p.parent / 'customer-clash-coexist.json'
        if own.exists():
            bridge = json.loads(own.read_text(encoding='utf-8'))
            row['coexist'] = {'tunnel': bridge.get('tunnel'), 'owned_keys': list(bridge.get('owned', {}))}
        result['clients'].append(row)
    except Exception:
        pass
print(json.dumps(result, ensure_ascii=True))
