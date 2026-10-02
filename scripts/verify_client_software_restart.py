#!/usr/bin/env python3
"""D408: repair unit dependency and accept software restart without an OS reboot."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.request

DATA = Path('/var/lib/server-network-assist-client/1000')
BACKEND = 'server-network-assist-client-1000.service'
TUN = 'd408-mihomo-tun.service'


def run(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=50)
    if check and result.returncode:
        raise RuntimeError(result.stderr[-500:] or result.stdout[-500:])
    return result.stdout.strip()


def pid(unit):
    return int(run('systemctl', 'show', unit, '-p', 'MainPID', '--value'))


def request(path, value=None):
    instance = json.loads((DATA / 'client-instance.json').read_text())
    base = f'http://127.0.0.1:{instance["port"]}'
    req = urllib.request.Request(base + path,
        headers={'X-Client-Token': instance['token'], 'Origin': base,
                 'Content-Type': 'application/json'},
        data=json.dumps(value).encode() if value is not None else None)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as response:
        result = json.load(response)
    if result.get('ok') is False:
        raise RuntimeError(result.get('error', 'client API failed'))
    return result


def wait_api():
    for _ in range(40):
        try:
            request('/api/state')
            return
        except Exception:
            time.sleep(.5)
    raise RuntimeError('Client API did not recover')


def checks(tunnel):
    assert run('systemctl', 'is-active', BACKEND, TUN, f'wg-quick@{tunnel}.service').splitlines() == ['active'] * 3
    for host in ['10.20.32.12', '10.20.32.13']:
        assert 'dev enp4s0' in run('ip', 'route', 'get', host)
        with socket.create_connection((host, 22), timeout=5) as connection:
            assert connection.recv(128).startswith(b'SSH-2.0-')
    assert 'dev d408-tun' in run('ip', 'route', 'get', '1.1.1.1')
    assert 'dev ' + tunnel in run('ip', 'route', 'get', '1.1.1.1', 'mark', '0xd40a')
    rules = json.loads(run('ip', '-j', 'rule', 'show'))
    owned = [x['priority'] for x in rules if 10030 <= x['priority'] <= 10060]
    assert len(owned) == len(set(owned)) and 10030 in owned and 10060 in owned
    statuses = {}
    for url in ['https://github.com', 'https://api.github.com']:
        code = ''
        for attempt in range(3):
            code = run('curl', '-4', '--noproxy', '*', '--connect-timeout', '10',
                '--max-time', '25', '-sS', '-o', '/dev/null', '-w', '%{http_code}', url, check=False)
            if code == '200':
                break
            time.sleep(2)
        assert code == '200', f'{url}: {code}'
        statuses[url] = code
    return {'campus_ssh': True, 'tun_public_route': True,
            'clash_outbound_wireguard': True, 'no_duplicate_rules': True, 'https': statuses}


def main():
    assert os.geteuid() == 0 and run('hostname') == 'd408-4090'
    unit = Path('/etc/systemd/system') / TUN
    before = unit.read_text()
    old = f'Requires={BACKEND}'
    backup = Path('/var/backups/d408-network') / (time.strftime('%Y%m%dT%H%M%S') + '-software-restart')
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(unit, backup / unit.name)
    helper = Path('/usr/local/libexec/d408_customer_tun.py')
    staged_helper = Path('/home/d408/d408_customer_tun.py')
    if staged_helper.exists():
        shutil.copy2(helper, backup / helper.name)
        shutil.copy2(staged_helper, helper)
        helper.chmod(0o755)
    module = Path('/opt/server-network-assist-client/venv/lib/python3.12/site-packages/server_network_assist/client_tunnel.py')
    staged_module = Path('/home/d408/client_tunnel.py')
    if staged_module.exists():
        shutil.copy2(module, backup / module.name)
        shutil.copy2(staged_module, module)
        module.chmod(0o644)
    if old in before:
        unit.write_text(before.replace(old, f'Wants={BACKEND}'))
        run('systemctl', 'daemon-reload')
    assert old not in unit.read_text()
    active = json.loads((DATA / 'customer-active-line.json').read_text())
    tunnel = active['tunnel']
    request('/api/online/lease/renew', {})
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    original_tun_pid = pid(TUN)
    results = []
    for cycle in range(2):
        old_pid = pid(BACKEND)
        checks(tunnel)
        print(f'Software restart cycle {cycle + 1}: starting', flush=True)
        run('systemctl', 'restart', BACKEND)
        wait_api()
        assert pid(BACKEND) != old_pid and pid(BACKEND) > 0
        assert pid(TUN) == original_tun_pid and original_tun_pid > 0
        assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == boot_id
        assert json.loads((DATA / 'customer-active-line.json').read_text())['tunnel'] == tunnel
        request('/api/online/lease/renew', {})
        result = checks(tunnel)
        result.update(cycle=cycle + 1, backend_pid_before=old_pid,
                      backend_pid_after=pid(BACKEND), clash_pid=original_tun_pid)
        results.append(result)
        print(f'Software restart cycle {cycle + 1}: PASSED', flush=True)
    print(json.dumps({'passed': True, 'os_rebooted': False,
        'backup': str(backup), 'cycles': results}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
