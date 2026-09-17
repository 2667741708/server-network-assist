#!/usr/bin/env python3
"""4090 recovery controller. No cross-host mutations, credential output or global flushes."""
import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time

BASE = Path('/var/lib/gateway-health')
CONFIG = Path('/etc/gateway-health.json')
ENV = {k: v for k, v in os.environ.items() if k.lower() not in
       ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')}
ENV.update(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', LC_ALL='C.UTF-8')

def run(args, timeout=15, stdin=None):
    try:
        p = subprocess.run(args, input=stdin, text=True, capture_output=True,
                           timeout=timeout, env=ENV)
        return p.returncode, p.stdout.strip()
    except subprocess.TimeoutExpired:
        return 124, ''

def tcp(host, port=22):
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False

def write_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    temp.replace(path)

class Controller:
    def __init__(self, config, state, repair=False):
        self.c, self.s, self.repair = config, state, repair
        self.now = time.time()
        self.result = {'time': self.now, 'checks': {}, 'actions': []}

    def action(self, key, args, cooldown=300, threshold=1, failed=True, timeout=30, stdin=None):
        counts = self.s.setdefault('failures', {})
        counts[key] = counts.get(key, 0) + 1 if failed else 0
        if not failed or not self.repair or counts[key] < threshold:
            return False
        last = self.s.setdefault('last_action', {}).get(key, 0)
        if self.now - last < cooldown:
            return False
        # Persist throttle BEFORE any disruptive operation, including a killed worker.
        self.s['last_action'][key] = self.now
        write_json(BASE / 'state.json', self.s)
        code, _ = run(args, timeout=timeout, stdin=stdin)
        self.result['actions'].append({'component': key, 'exit_code': code})
        return code == 0

    def enabled(self, unit):
        return run(['systemctl', 'is-enabled', unit])[1] == 'enabled'

    def start_missing(self, unit, present=True):
        if not self.enabled(unit):
            self.result['checks'][unit] = 'disabled-by-operator'
            return False
        active = run(['systemctl', 'is-active', unit])[1] == 'active'
        self.result['checks'][unit] = active and present
        if not active or not present:
            # A failed oneshot may be start-limited. reset-failed does not restart services.
            if self.repair:
                run(['systemctl', 'reset-failed', unit])
            self.action(unit, ['systemctl', 'restart', unit], cooldown=120)
        return True

    def portal(self):
        cmd = ['runuser', '-u', 'a', '--', '/usr/bin/python3', self.c['netlogin']]
        code, output = run(cmd + ['current-status', '--json'], timeout=40)
        try:
            status = json.loads(output).get('summary', {}) if code == 0 else {}
        except ValueError:
            status = {}
        account = next(x for x in json.loads(Path(self.c['accounts']).read_text(encoding='utf-8-sig'))['accounts']
                       if x['name'] == self.c['account_name'])
        correct = bool(status.get('online') and status.get('service') == account['service']
                       and str(status.get('userId')) == str(account['username'])
                       and status.get('userIp') == self.c['address'])
        self.result['checks']['campus_auth'] = {'state': status.get('state', 'unknown'),
                                                'expected_account_online': correct,
                                                'service': status.get('service', '')}
        if correct:
            self.s['login_attempts'] = 0
            self.s.setdefault('failures', {})['campus-login'] = 0
        elif status.get('online'):
            # Never kick a different active account off the network automatically.
            self.result['checks']['campus_auth']['needs_attention'] = 'different-active-session'
        else:
            offline = status.get('state') == 'offline'
            # Portal status unknown is not evidence of an expired account.
            if offline:
                attempts = self.s.get('login_attempts', 0)
                cooldown = min(1800, 60 * 2 ** min(attempts, 5))
                payload = json.dumps({'username': account['username'], 'password': account['password'], 'service': '1'})
                before = len(self.result['actions'])
                self.action('campus-login', cmd + ['login-stdin'], cooldown=cooldown,
                            threshold=2, timeout=55, stdin=payload)
                if len(self.result['actions']) > before:
                    self.s['login_attempts'] = attempts + 1
                    # Exit code alone is not acceptance. The next cycle rechecks online status.
                    self.result['checks']['campus_auth']['verification'] = 'pending-next-cycle'
        return correct

    def firewall(self, group):
        if not self.enabled(group['unit']):
            return
        healthy = True
        for table, specification in group['tables'].items():
            chains = specification['chains']
            for chain, wanted in chains.items():
                code, output = run(['iptables', '-w', '3', '-t', table, '-S', chain])
                actual = [x for x in output.splitlines() if x.startswith('-A ')]
                if code != 0 or actual != wanted:
                    healthy = False
                    # Atomic table transaction, scoped to ONE owned chain. No global flush.
                    restore = '*' + table + '\n:' + chain + ' - [0:0]\n-F ' + chain + '\n'
                    restore += '\n'.join(wanted) + '\nCOMMIT\n'
                    self.action('chain:' + chain, ['iptables-restore', '-w', '3', '--noflush'],
                                cooldown=60, stdin=restore)
            for line in specification['jumps']:
                parts = shlex.split(line)
                code, _ = run(['iptables', '-w', '3', '-t', table, '-C'] + parts[1:])
                if code:
                    healthy = False
                    self.action('jump:' + table + ':' + line, ['iptables', '-w', '3', '-t', table,
                                '-I', parts[1], '1'] + parts[2:], cooldown=60)
        self.result['checks'][group['unit'] + ':firewall'] = healthy

    def routes(self, group):
        if not self.enabled(group['unit']):
            return
        route = run(['ip', '-4', 'route', 'show', 'table', str(group['table'])])[1]
        want = 'default via ' + self.c['gateway'] + ' dev ' + self.c['interface']
        self.action('route:' + str(group['table']), ['ip', 'route', 'replace', 'default', 'via',
                    self.c['gateway'], 'dev', self.c['interface'], 'table', str(group['table'])],
                    cooldown=60, failed=want not in route)
        rules = run(['ip', '-j', '-4', 'rule'])[1]
        parsed = json.loads(rules)
        match = lambda r: r.get('priority') == group['priority'] and str(r.get('table')) == str(group['table']) and int(str(r.get('fwmark', '0')), 0) == int(group['mark'], 0)
        ok = any(match(r) for r in parsed)
        self.action('rule:' + str(group['table']), ['ip', 'rule', 'add', 'priority', str(group['priority']),
                    'fwmark', group['mark'], 'table', str(group['table'])], cooldown=60, failed=not ok)
        self.result['checks'][group['unit'] + ':routing'] = want in route and ok

    def wg(self, iface, target, uplink):
        unit = 'wg-quick@' + iface + '.service'
        if not self.start_missing(unit, Path('/sys/class/net/' + iface).exists()):
            return
        config_code, stripped = run(['wg-quick', 'strip', iface])
        peers_code, runtime_peers = run(['wg', 'show', iface, 'peers'])
        if config_code == 0 and peers_code == 0:
            expected = {line.split('=', 1)[1].strip() for line in stripped.splitlines()
                        if line.strip().lower().startswith('publickey')}
            if expected != set(runtime_peers.split()):
                self.action('peer-config:' + iface, ['wg', 'syncconf', iface, '/dev/stdin'],
                            cooldown=120, stdin=stripped)
        code, peers = run(['wg', 'show', iface, 'persistent-keepalive'])
        if code == 0:
            for peer in peers.splitlines():
                key, value = peer.split()
                if value != '25':
                    self.action('keepalive:' + iface + ':' + key,
                                ['wg', 'set', iface, 'peer', key, 'persistent-keepalive', '25'])
        reachable = tcp(target)
        self.result['checks'][iface + ':tcp'] = reachable
        # Remote peer offline must not cause unrelated tunnels to restart.
        if iface != 'wg0' or reachable or not uplink:
            if reachable:
                self.s.setdefault('failures', {})['resync:' + iface] = 0
            return
        if config_code == 0:
            self.action('resync:' + iface, ['wg', 'syncconf', iface, '/dev/stdin'],
                        threshold=3, cooldown=300, stdin=stripped)

    def cycle(self):
        if (BASE / 'paused').exists():
            self.result['mode'] = 'paused'
            return self.result
        carrier = Path('/sys/class/net/' + self.c['interface'] + '/carrier')
        try:
            linked = carrier.read_text().strip() == '1'
        except OSError:
            linked = False
        self.result['checks']['carrier'] = linked
        active = run(['nmcli', '-g', 'GENERAL.STATE', 'device', 'show', self.c['interface']])[1].startswith('100')
        if linked and not active:
            self.action('ethernet', ['nmcli', '--wait', '15', 'connection', 'up', 'uuid', self.c['uuid']], cooldown=120)
        if not linked:
            self.result['mode'] = 'waiting-for-carrier'
            return self.result
        uplink = self.portal()
        route = run(['ip', 'route', 'get', self.c['hub_endpoint']])[1]
        correct_route = ('via ' + self.c['gateway']) in route and ('dev ' + self.c['interface']) in route
        self.action('hub-route', ['ip', 'route', 'replace', self.c['hub_endpoint'] + '/32',
                    'via', self.c['gateway'], 'dev', self.c['interface']], cooldown=60, failed=not correct_route)
        self.start_missing('mihomo.service', Path('/sys/class/net/Meta').exists())
        for iface, target in self.c['wg_targets'].items():
            self.wg(iface, target, uplink)
        self.result['checks']['fleet-titan:tcp'] = tcp('10.203.49.3')
        for group in self.c['groups']:
            self.start_missing(group['unit'])
            self.firewall(group)
            self.routes(group)
        forwarding = Path('/proc/sys/net/ipv4/ip_forward').read_text().strip() == '1'
        if any(self.enabled(g['unit']) for g in self.c['groups']):
            self.action('forwarding', ['sysctl', '-w', 'net.ipv4.ip_forward=1'], failed=not forwarding)
        self.result['checks']['ip_forward'] = forwarding
        def https(url):
            code, http = run(['curl', '--silent', '--output', '/dev/null', '--write-out', '%{http_code}',
                             '--proxy', 'http://127.0.0.1:7897', '--connect-timeout', '4', '--max-time', '8', url])
            return code == 0 and http in ('200', '204', '301', '302')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outgoing = list(pool.map(https, self.c['https_targets']))
        self.result['checks']['https'] = dict(zip(self.c['https_targets'], outgoing))
        self.action('mihomo-unresponsive', ['systemctl', 'restart', 'mihomo.service'], threshold=3,
                    cooldown=600, failed=uplink and not any(outgoing) and self.enabled('mihomo.service'))
        self.result['mode'] = 'checked'
        return self.result

def main():
    command = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if command == 'status':
        print((BASE / 'status.json').read_text() if (BASE / 'status.json').exists() else 'No check yet')
        return
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo')
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if command == 'pause':
        (BASE / 'paused').touch()
        print('Automatic repairs paused; current networking retained')
        return
    if command == 'resume':
        (BASE / 'paused').unlink(missing_ok=True)
        run(['systemctl', 'enable', '--now', 'gateway-health.timer'])
        print('Automatic repairs resumed')
        return
    if command not in ('check', 'repair'):
        raise SystemExit('usage: gateway-health {status|check|repair|pause|resume}')
    with (BASE / 'lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Check already running')
            return
        state = json.loads((BASE / 'state.json').read_text()) if (BASE / 'state.json').exists() else {}
        controller = Controller(json.loads(CONFIG.read_text()), state, command == 'repair')
        result = controller.cycle()
        if command == 'repair':
            write_json(BASE / 'state.json', state)
            # Status logging is auxiliary and cannot block a completed repair.
            try:
                write_json(BASE / 'status.json', result)
            except OSError:
                pass
        print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
