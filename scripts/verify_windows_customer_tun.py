"""Real Windows customer API + installed Clash acceptance; never print credentials."""
import argparse
import csv
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request

from server_network_assist import clash_control

ARTIFACT = Path(__file__).resolve().parents[1] / 'artifacts/client-release-tun-restart'


def powershell(command):
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr[-500:])
    return result.stdout.strip()


def data_dir():
    identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
    sid = next(csv.reader([identity.strip()]))[1]
    return Path(os.environ['PROGRAMDATA']) / 'ServerNetworkAssist/client' / sid


def request(path, value=None):
    instance = json.loads((data_dir() / 'client-instance.json').read_text(encoding='utf-8'))
    base = f'http://127.0.0.1:{instance["port"]}'
    req = urllib.request.Request(base + path,
        headers={'X-Client-Token': instance['token'], 'Origin': base,
                 'Content-Type': 'application/json'},
        data=json.dumps(value).encode() if value is not None else None)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=80) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read(1000).decode('utf-8', 'replace')) from None
    if result.get('ok') is False:
        raise RuntimeError(result.get('error', 'client API failed'))
    return result


def core():
    path = clash_control.candidates()[0]
    base, secret = clash_control.controller(path)
    return base, secret, clash_control.api(base, secret, 'GET', '/configs')


def route(host):
    return powershell("Find-NetRoute -RemoteIPAddress '" + host +
                      "' | Select-Object -ExpandProperty InterfaceAlias -Unique")


def checks():
    _, _, config = core()
    state = json.loads((data_dir() / 'customer-active-line.json').read_text(encoding='utf-8'))
    tunnel = state['tunnel']
    campus = {}
    for host in ['10.20.32.12', '10.20.32.13', '10.20.32.14']:
        alias = route(host)
        assert tunnel not in alias and 'Mihomo' not in alias and 'Meta' not in alias
        with socket.create_connection((host, 22), timeout=5) as connection:
            assert connection.recv(128).startswith(b'SSH-2.0-')
        campus[host] = alias
    assert 'whm-management' in route('10.201.250.1')
    public_route = route('1.1.1.1')
    if config['tun']['enable']:
        assert any(name in public_route for name in [config['tun'].get('device') or 'Mihomo', 'Meta'])
    else:
        assert tunnel in public_route
    https = {}
    for url in ['https://github.com', 'https://api.github.com']:
        code = ''
        for _ in range(3):
            args = ['curl.exe', '-4', '--connect-timeout', '10', '--max-time', '30', '-sS',
                    '-o', 'NUL', '-w', '%{http_code}', url]
            if config['tun']['enable']:
                args[1:1] = ['--noproxy', '*']
            else:
                args[1:1] = ['--proxy', 'http://127.0.0.1:' + str(config['mixed-port'])]
            result = subprocess.run(args, capture_output=True, text=True, timeout=40)
            code = result.stdout.strip()
            if result.returncode == 0 and code == '200':
                break
            time.sleep(2)
        assert code == '200', f'{url}: {code}, {result.stderr[-200:]}'
        https[url] = code
    request('/api/online/lease/renew', {})
    return {'passed': True, 'tun_enabled': config['tun']['enable'], 'campus_routes': campus,
            'management_preserved': True, 'public_route': public_route, 'https': https,
            'client_pid': json.loads((data_dir() / 'client-instance.json').read_text(encoding='utf-8'))['pid']}


def main():
    # REQ-NET-003: this script performs real network mutations and renewals.
    # The operator's own computer is no longer an authorized test target.
    if socket.gethostname().casefold() != 'desktop-td6b9gn':
        raise SystemExit('本机网络验收已禁止；此工具只允许在明确指定的 Titan Windows 服务器上执行')
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['connect', 'tun-on', 'tun-off', 'check', 'disconnect'])
    args = parser.parse_args()
    if args.action == 'connect':
        if not (data_dir() / 'customer-online-service.json').exists():
            request('/api/online/enroll', {'url': (ARTIFACT / 'enrollment-private.txt').read_text(encoding='utf-8').strip(),
                                         'label': 'Windows TUN coexistence acceptance'})
        routes = request('/api/online/routes')['routes']
        result = request('/api/online/connect', {'grant_id': routes[0]['id']})
        time.sleep(5)
        print(json.dumps({'connected': True, 'tunnel': result['tunnel']}))
    elif args.action in ['tun-on', 'tun-off']:
        base, secret, config = core()
        clash_control.api(base, secret, 'PATCH', '/configs', {'tun': {'enable': args.action == 'tun-on'}})
        time.sleep(5)
        print(json.dumps(checks(), ensure_ascii=False))
    elif args.action == 'disconnect':
        result = request('/api/online/disconnect', {})
        print(json.dumps({'disconnected': result['ok'], 'release_error': result.get('release_error')}))
    else:
        print(json.dumps(checks(), ensure_ascii=False))


if __name__ == '__main__':
    main()
