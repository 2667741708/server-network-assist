#!/usr/bin/env python3
"""D408's existing Mihomo UI/core over a leased customer tunnel, with owned rules."""
from __future__ import annotations

import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time

DATA = Path('/var/lib/server-network-assist-client/1000')
RESOURCE = Path('/home/d408/clashctl/resources')
CORE = '/home/d408/clashctl/bin/mihomo'
YQ = '/home/d408/clashctl/bin/yq'
UNIT = 'd408-mihomo-tun.service'
NORMAL = 'd408-mihomo-proxy.service'
MARKER = Path('/etc/server-network-assist/customer-tun.json')
HELPER = Path('/usr/local/libexec/d408_customer_tun.py')
PYTHON = '/opt/server-network-assist-client/venv/bin/python'
TABLE = '20482'
MARK = '0xd40a'
TUN = 'd408-tun'
DIRECT = ['10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
          '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16', '224.0.0.0/3']


def run(*args, check=True, timeout=35):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} failed: {result.stderr.strip()[:250]}')
    return result


def active_tunnel():
    active = json.loads((DATA / 'customer-active-line.json').read_text())
    lease = json.loads((DATA / 'customer-online-lease.json').read_text())
    name = active.get('tunnel', '')
    if (active.get('kind') != 'online' or not re.fullmatch(r'sna[0-9a-f]{12}', name)
            or lease.get('expires_at', 0) <= time.time()):
        raise RuntimeError('先在新版客户端连接有效的借网节点，再开启 TUN')
    run('ip', 'link', 'show', 'dev', name)
    return name


def direct_networks():
    policy = DATA / 'client-routing.json'
    extra = json.loads(policy.read_text()).get('direct_cidrs', []) if policy.exists() else []
    # Client-owned fake IPs go to local Clash, never to the physical LAN bypass.
    return list(dict.fromkeys(DIRECT + [str(ipaddress.IPv4Network(x)) for x in extra
                                      if not ipaddress.IPv4Network(x).overlaps(
                                          ipaddress.IPv4Network('198.18.0.0/15'))]))


def rule_plan(networks):
    if len(networks) > 20:
        raise ValueError('TUN 直连网段最多 20 个')
    return [(10030, ['fwmark', MARK, 'lookup', 'main'])] + [
        (10031 + i, ['to', n, 'lookup', 'main']) for i, n in enumerate(networks)
    ] + [(10060, ['lookup', TABLE])]


def cleanup():
    if not MARKER.exists():
        return
    state = json.loads(MARKER.read_text())
    if state.get('service') != UNIT:
        raise RuntimeError('TUN 标记不属于本程序')
    for priority, selectors in state['rules']:
        run('ip', 'rule', 'del', 'priority', str(priority), *selectors, check=False)
    run('ip', 'route', 'flush', 'table', TABLE, check=False)
    run('resolvectl', 'revert', TUN, check=False)
    name = state.get('tunnel', '')
    if re.fullmatch(r'sna[0-9a-f]{12}', name) and run('ip', 'link', 'show', name, check=False).returncode == 0:
        config = Path('/etc/wireguard') / (name + '.conf')
        dns = next((line.split('=', 1)[1].strip().replace(',', ' ').split()
                    for line in config.read_text().splitlines() if line.startswith('DNS =')), [])
        if dns:
            run('resolvectl', 'dns', name, *dns)
        run('resolvectl', 'domain', name, '~.')
        run('resolvectl', 'default-route', name, 'yes')
    run('resolvectl', 'flush-caches', check=False)
    MARKER.unlink(missing_ok=True)


def guard():
    name = active_tunnel()
    rules = rule_plan(direct_networks())
    if MARKER.exists():
        cleanup()
    occupied = {int(x['priority']) for x in json.loads(run('ip', '-j', 'rule', 'show').stdout)}
    if occupied.intersection(p for p, _ in rules):
        raise RuntimeError('TUN 规则编号已被其他程序占用，保持现有网络')
    probe = run('ip', '-j', 'route', 'show', 'table', TABLE, check=False)
    # iproute2 may emit an unfinished '[' when an unused FIB table does not exist.
    if probe.returncode and 'FIB table does not exist' not in probe.stderr:
        raise RuntimeError('无法检查 TUN 路由表：' + probe.stderr[:200])
    routes = json.loads(probe.stdout) if probe.returncode == 0 else []
    if routes:
        raise RuntimeError('TUN 路由表已被其他程序占用')
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps({'service': UNIT, 'tunnel': name, 'rules': rules}))
    MARKER.chmod(0o600)
    try:
        run('ip', 'route', 'add', 'unreachable', 'default', 'metric', '32760', 'table', TABLE)
        for priority, selectors in rules:
            run('ip', 'rule', 'add', 'priority', str(priority), *selectors)
    except Exception:
        cleanup()
        raise


def attach():
    name = active_tunnel()
    for _ in range(50):
        if run('ip', 'link', 'show', TUN, check=False).returncode == 0:
            break
        time.sleep(.2)
    else:
        raise RuntimeError('Mihomo 没有创建 TUN 网卡')
    run('ip', 'route', 'replace', 'default', 'dev', TUN, 'metric', '10', 'table', TABLE)
    run('resolvectl', 'domain', name, '')
    run('resolvectl', 'default-route', name, 'no')
    run('resolvectl', 'dns', TUN, '198.19.0.2')
    run('resolvectl', 'domain', TUN, '~.')
    run('resolvectl', 'default-route', TUN, 'yes')
    run('resolvectl', 'flush-caches')


def config(tun):
    name = active_tunnel() if tun else None
    expr = '.tun.enable=false | del(.routing-mark) | del(.interface-name)'
    if tun:
        expr = (' .tun.enable=true | .tun.device="d408-tun" | .tun.stack="mixed"'
                ' | .tun.mtu=1380 | .tun.auto-route=false | .tun.auto-redirect=false'
                ' | .tun.auto-redir=false | .tun.auto-detect-interface=false'
                f' | .interface-name="{name}" | .routing-mark=54282 | .ipv6=false'
                ' | .dns.enable=true | .dns.enhanced-mode="fake-ip"'
                ' | .dns.fake-ip-range="198.19.0.1/16"'
                ' | .tun.dns-hijack=["any:53","tcp://any:53"]')
    # Upstream source TUN can hijack plaintext DNS and return its own fake IPs.
    # Keep subscription-specific nameserver-policy; bootstrap its resolver using DoT.
    expr += (' | .dns.default-nameserver=["tls://223.5.5.5","tls://1.12.12.12"]'
             ' | .dns.nameserver=["tls://223.5.5.5","tls://1.12.12.12"]'
             ' | .dns.fallback=["tls://1.1.1.1","tls://8.8.8.8"]')
    target = RESOURCE / ('tun-runtime.yaml' if tun else 'normal-runtime.yaml')
    temporary = target.with_suffix('.pending.yaml')
    temporary.write_text(run(YQ, expr, str(RESOURCE / 'runtime.yaml')).stdout)
    os.chown(temporary, 1000, 1000)
    temporary.chmod(0o600)
    try:
        run('runuser', '-u', 'd408', '--', CORE, '-t', '-d', str(RESOURCE), '-f', str(temporary))
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def cancel_trial():
    run('systemctl', 'stop', 'd408-customer-tun-rollback.timer', check=False)


def normal():
    cancel_trial()
    run('systemctl', 'stop', UNIT)
    cleanup()
    config(False)
    run('systemctl', 'restart', NORMAL)


def verify_https(url):
    error = ''
    for attempt in range(3):
        probe = run('curl', '--noproxy', '*', '-4', '-sS', '-L', '--max-time', '15',
                    '-o', '/dev/null', '-w', '%{http_code}', url, timeout=20, check=False)
        if probe.returncode == 0 and probe.stdout == '200':
            return
        error = probe.stderr.strip()[:200] or ('HTTP ' + probe.stdout)
        if attempt < 2:
            time.sleep(2)
    raise RuntimeError('TUN HTTPS 实测失败：' + error)


def enable():
    name = active_tunnel()
    # The relay reconciles grants asynchronously. Avoid negative DNS caching before
    # its peer and forwarding policy have actually become ready after reconnect.
    for _ in range(60):
        handshakes = run('wg', 'show', name, 'latest-handshakes').stdout.splitlines()
        if any(int(line.split()[-1]) > 0 for line in handshakes):
            break
        time.sleep(.5)
    else:
        raise RuntimeError('借网中继尚未握手，保持当前代理模式，请稍后重试')
    config(True)
    cancel_trial()
    run('systemd-run', '--unit=d408-customer-tun-rollback', '--on-active=3m',
        '/usr/local/sbin/d408-tun', 'off')
    try:
        run('systemctl', 'stop', NORMAL)
        run('systemctl', 'restart', UNIT, timeout=50)
        for url in ['https://github.com', 'https://api.github.com']:
            verify_https(url)
        for host in ['10.20.32.12', '10.20.32.13', '202.206.240.12']:
            if 'dev enp4s0' not in run('ip', 'route', 'get', host).stdout:
                raise RuntimeError('校园内网或跳板路由没有保持直连')
        for host in ['10.20.32.12', '10.20.32.13']:
            with socket.create_connection((host, 22), timeout=5) as connection:
                if not connection.recv(128).startswith(b'SSH-2.0-'):
                    raise RuntimeError('校园 SSH 服务没有保持可达')
        cancel_trial()
        print('TUN VERIFIED: GitHub/API 200; LAN and SSH jump host remain direct')
    except Exception:
        normal()
        raise


def install():
    stamp = datetime.datetime.now().strftime('%Y%m%dT%H%M%S-customer-tun')
    backup = Path('/var/backups/d408-network') / stamp
    backup.mkdir(parents=True, mode=0o700)
    paths = [Path('/usr/local/sbin/d408-tun'), Path('/etc/systemd/system') / UNIT,
             RESOURCE / 'tun-runtime.yaml', RESOURCE / 'normal-runtime.yaml']
    for path in paths:
        if path.exists():
            shutil.copy2(path, backup / path.name)
    HELPER.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), HELPER)
    HELPER.chmod(0o755)
    Path('/usr/local/sbin/d408-tun').write_text(f'#!/bin/sh\nexec {PYTHON} {HELPER} "$@"\n')
    Path('/usr/local/sbin/d408-tun').chmod(0o755)
    unit = f'''[Unit]
Description=D408 Mihomo TUN over leased customer network
Wants=server-network-assist-client-1000.service
After=server-network-assist-client-1000.service network-online.target
[Service]
Type=simple
User=d408
Group=d408
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW
Environment=HOME=/home/d408
WorkingDirectory=/home/d408/clashctl
ExecStartPre=+{PYTHON} {HELPER} guard
ExecStart={CORE} -d {RESOURCE} -f {RESOURCE}/tun-runtime.yaml
ExecStartPost=+{PYTHON} {HELPER} attach
ExecStopPost=+{PYTHON} {HELPER} cleanup
Restart=on-failure
RestartSec=3
TimeoutStartSec=40
TimeoutStopSec=15
[Install]
WantedBy=multi-user.target
'''
    (Path('/etc/systemd/system') / UNIT).write_text(unit)
    # The lease client owns connection lifecycle, never revive the obsolete VPN on boot.
    run('systemctl', 'disable', UNIT)
    run('systemctl', 'daemon-reload')
    package = Path(PYTHON).parent.parent / 'lib/python3.12/site-packages/server_network_assist'
    module = Path(__file__).with_name('client_tunnel.py')
    shutil.copy2(package / 'client_tunnel.py', backup / 'client_tunnel.py')
    shutil.copy2(module, package / 'client_tunnel.py')
    routing = DATA / 'client-routing.json'
    if routing.exists():
        shutil.copy2(routing, backup / routing.name)
    policy = json.loads(routing.read_text()) if routing.exists() else {}
    policy['direct_cidrs'] = list(dict.fromkeys(policy.get('direct_cidrs', []) + ['198.18.0.0/15']))
    routing.write_text(json.dumps(policy))
    routing.chmod(0o600)
    # No backend restart: preserve its current lease; it loads this module on demand.
    print('Installed; backup:', backup)


def main():
    if os.geteuid() != 0 or run('hostname').stdout.strip() != 'd408-4090':
        raise RuntimeError('此升级工具仅适用于已核验的 D408，且需要 sudo')
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    actions = {'install': install, 'enable': enable, 'on': enable, 'off': normal,
               'normal': normal, 'guard': guard, 'attach': attach, 'cleanup': cleanup}
    if action == 'stop':
        cancel_trial()
        run('systemctl', 'stop', UNIT, NORMAL)
        cleanup()
    elif action == 'status':
        print(run('systemctl', 'is-active', UNIT, NORMAL, check=False).stdout.strip())
        print(run('ip', 'rule', 'show').stdout.strip())
    elif action in actions:
        actions[action]()
    else:
        raise ValueError('命令：enable / off / stop / status')


if __name__ == '__main__':
    main()
