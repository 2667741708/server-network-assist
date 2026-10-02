"""D321 read-only evidence; never enroll, connect, leave, or print credentials."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import ssl
import subprocess
import time
import urllib.request

DATA = Path('C:/ProgramData/ServerNetworkAssist/client/S-1-5-21-1446874470-693334550-1715965273-1001/gui-window-test-20260917')


def load(name):
    path = DATA / name
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def powershell(command):
    command = '$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); ' + command
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        return {'error': 'read-only PowerShell probe failed'}
    try:
        return json.loads(result.stdout.decode('utf-8-sig'))
    except (UnicodeError, ValueError):
        return {'error': 'read-only PowerShell JSON decoding failed'}


def main():
    if socket.gethostname().upper() != 'DESKTOP-TD6B9GN':
        raise RuntimeError('D321 only')
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    instance = load('client-instance.json')
    active = load('customer-active-line.json')
    lease = load('customer-online-lease.json')
    report = {'time': datetime.now().astimezone().isoformat(), 'hostname': socket.gethostname(),
              'configured': bool(load('customer-online-service.json')),
              'active': bool(active), 'kind': active.get('kind'), 'tunnel': active.get('tunnel'),
              'recovering': (DATA / 'customer-leaving-network.json').exists(),
              'lease_endpoint': lease.get('endpoint'), 'lease_valid': lease.get('expires_at', 0) > time.time(),
              'network_changed_by_probe': False}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(f"http://127.0.0.1:{int(instance['port'])}/api/state",
                                         headers={'X-Client-Token': instance['token']})
        with opener.open(request, timeout=5) as response:
            state = json.load(response)
        report['gui_backend'] = {'configured': state.get('online_service', {}).get('configured'),
                                 'active': bool(state.get('active')), 'recovering': state.get('recovering')}
    except Exception as error:
        report['gui_backend'] = {'error_type': type(error).__name__}
    tunnel = active.get('tunnel', '')
    if tunnel and tunnel.isalnum() and tunnel.startswith('sna'):
        wg = Path('C:/Program Files/WireGuard/wg.exe')
        result = subprocess.run([str(wg), 'show', tunnel, 'latest-handshakes'], capture_output=True,
                                timeout=7, creationflags=subprocess.CREATE_NO_WINDOW)
        ages = []
        for row in result.stdout.decode('ascii', errors='replace').splitlines():
            fields = row.split()
            if len(fields) == 2 and fields[1].isdigit():
                stamp = int(fields[1])
                ages.append(int(time.time()) - stamp if stamp else None)
        report['handshake_ages_seconds'] = ages
        report['recent_handshake'] = result.returncode == 0 and any(age is not None and 0 <= age <= 180 for age in ages)
    if args.full:
        report['physical_adapters'] = powershell("Get-NetAdapter -Physical | Select-Object Name,InterfaceDescription,Status,MediaType,ifIndex | ConvertTo-Json -Compress")
        report['management_running'] = powershell("(Get-Service -Name 'WireGuardTunnel$fleet-titan').Status -eq 'Running' | ConvertTo-Json")
        report['wireguard_services'] = powershell("Get-Service -Name 'WireGuardTunnel*' | Select-Object Name,Status | ConvertTo-Json -Compress")
        report['public_route'] = powershell("Find-NetRoute -RemoteIPAddress 1.1.1.1 | Select-Object InterfaceAlias,InterfaceIndex,IPAddress,DestinationPrefix,NextHop,RouteMetric | ConvertTo-Json -Compress")
        report['campus_route'] = powershell("Find-NetRoute -RemoteIPAddress 10.20.32.13 | Select-Object InterfaceAlias,InterfaceIndex,IPAddress,DestinationPrefix,NextHop,RouteMetric | ConvertTo-Json -Compress")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(4)
                sock.bind(('10.20.31.134', 0))
                sock.connect(('10.20.32.13', 9182))
                report['campus_tcp_9182'] = True
        except OSError:
            report['campus_tcp_9182'] = False
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                 urllib.request.HTTPSHandler(context=ssl.create_default_context()))
            request = urllib.request.Request('https://api.github.com', headers={'User-Agent': 'D321-SNA-readonly-check'})
            with opener.open(request, timeout=7) as response:
                report['https_status_without_http_proxy'] = response.status
        except Exception as error:
            report['https_probe_error_type'] = type(error).__name__
    if args.report:
        try:
            args.report.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding='utf-8')
        except OSError:
            report['optional_report_write_failed'] = True
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
