"""D321-only API/network acceptance, not a GUI-click acceptance test."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import time
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clash_control

DATA = Path('C:/ProgramData/ServerNetworkAssist/client/S-1-5-21-1446874470-693334550-1715965273-1001/gui-window-test-20260917')
ROOT = Path(__file__).resolve().parent


def request(path, body=None):
    info = json.loads((DATA / 'client-instance.json').read_text(encoding='utf-8'))
    base = f"http://127.0.0.1:{int(info['port'])}"
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
        headers={'X-Client-Token': info['token'], 'Origin': base, 'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=65) as response:
        return json.load(response)


def core():
    path = clash_control.candidates()[0]
    base, secret = clash_control.controller(path)
    config = clash_control.api(base, secret, 'GET', '/configs')
    return base, secret, config


def probe():
    state = request('/api/state')
    active = state.get('active') or {}
    base, secret, config = core()
    proxies = clash_control.api(base, secret, 'GET', '/proxies').get('proxies') or {}
    candidates = [p for p in proxies.values() if p.get('type') not in
                  ['Selector', 'URLTest', 'Fallback', 'LoadBalance', 'Direct', 'Reject', 'Compatible', 'Relay']]
    candidates.sort(key=lambda p: not bool((p.get('history') or [{}])[-1].get('delay')))
    delays = []
    for index, p in enumerate(candidates[:3]):
        query = urllib.parse.urlencode({'timeout': 6000, 'url': 'https://www.gstatic.com/generate_204'})
        try:
            value = clash_control.api(base, secret, 'GET', '/proxies/' + urllib.parse.quote(p['name'], safe='') + '/delay?' + query)
            delays.append({'sample': index + 1, 'type': p.get('type'), 'delay_ms': value.get('delay')})
        except Exception as exc:
            delays.append({'sample': index + 1, 'type': p.get('type'), 'error_type': type(exc).__name__})
    https = {}
    for label, proxy in [('direct', {}), ('clash', {'https': 'http://127.0.0.1:' + str(config.get('mixed-port', 7897))})]:
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler(proxy)).open('https://api.github.com', timeout=10) as response:
                https[label] = response.status
        except Exception as exc:
            https[label] = type(exc).__name__
    lease = (state.get('online_service') or {}).get('lease') or {}
    return {'time': time.time(), 'active': bool(active), 'tunnel': active.get('tunnel'),
            'lease_valid': lease.get('expires_at', 0) > time.time(), 'mode': config.get('mode'),
            'tun': bool((config.get('tun') or {}).get('enable')), 'interface': config.get('interface-name'),
            'node_samples': delays, 'https': https}


def guard():
    # Independent process can restore this test's owned session after SSH loss.
    time.sleep(240)
    path = ROOT / 'safety.json'
    if not path.exists():
        return
    marker = json.loads(path.read_text())
    if not marker.get('armed'):
        return
    current = request('/api/state').get('active') or {}
    if current.get('started') == marker.get('started') and current.get('tunnel') == marker.get('tunnel'):
        request('/api/network/leave', {})


def main():
    if socket.gethostname().upper() != 'DESKTOP-TD6B9GN':
        raise RuntimeError('D321 only; operator PC network mutations prohibited')
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['probe', 'connect', 'tun-on', 'tun-off', 'leave', 'guard', 'disarm'])
    action = parser.parse_args().action
    if action == 'guard':
        guard(); return
    if action == 'disarm':
        (ROOT / 'safety.json').write_text(json.dumps({'armed': False}))
        print(json.dumps({'safety_disarmed': True})); return
    if action == 'connect':
        if request('/api/state').get('active'):
            raise RuntimeError('Existing active borrowing belongs to the user; not replaced')
        routes = request('/api/online/subscription')['routes']
        request('/api/online/connect', {'grant_id': routes[0]['id']})
        active = request('/api/state')['active']
        (ROOT / 'safety.json').write_text(json.dumps({'armed': True, 'started': active['started'], 'tunnel': active['tunnel']}))
        subprocess.Popen([str(Path(__import__('sys').executable).with_name('pythonw.exe')), str(Path(__file__)), 'guard'],
                         creationflags=subprocess.CREATE_NO_WINDOW)
    elif action in ['tun-on', 'tun-off']:
        base, secret, config = core()
        clash_control.api(base, secret, 'PATCH', '/configs', {'tun': {'enable': action == 'tun-on'}})
        time.sleep(5)
    elif action == 'leave':
        request('/api/network/leave', {})
        (ROOT / 'safety.json').write_text(json.dumps({'armed': False}))
    print(json.dumps(probe(), ensure_ascii=True))


if __name__ == '__main__':
    main()
