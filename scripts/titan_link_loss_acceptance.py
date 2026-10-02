"""Titan-only adapter-loss test, with a separately scheduled restore task."""
from pathlib import Path
import json
import socket
import subprocess
import time
import urllib.request

assert socket.gethostname().lower() == 'desktop-td6b9gn'
root = Path(r'C:\ProgramData\ServerNetworkAssist\titan-exit-test')
data = Path(r'C:\ProgramData\ServerNetworkAssist\client\S-1-5-21-1446874470-693334550-1715965273-1001')
ps = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'


def state():
    info = json.loads((data / 'client-instance.json').read_text())
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/state", headers={'X-Client-Token': info['token']})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=8) as response:
        value = json.load(response)
    return {'active': bool(value.get('active')), 'recovering': value.get('recovering'), 'events': value.get('network_events')}


def run(name):
    subprocess.run([ps, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(root / name)], check=True, capture_output=True, timeout=20)


def window_visible():
    path = root / 'window-alert-result.json'
    path.unlink(missing_ok=True)
    run('titan_schedule_notification_snapshot.ps1')
    for _ in range(20):
        if path.exists():
            try:
                windows = json.loads(path.read_text(encoding='utf-8-sig'))
                rows = [row for row in windows['windows'] if '|借网客户端|' in row]
                if rows:
                    return any(row.endswith('|True') for row in rows)
            except (ValueError, OSError):
                pass
        time.sleep(.25)
    raise RuntimeError('No fresh interactive window snapshot')


result = {'before': state(), 'started': time.time()}
assert result['before']['active'], 'Connect a customer lease before this test'
result['before_window_visible'] = window_visible()
assert not result['before_window_visible'], 'Minimize before testing automatic visible alert'
before_sequence = max((e['sequence'] for e in result['before']['events'] or []), default=0)
try:
    run('titan_drop_test_adapter.ps1')
    time.sleep(22)
    result['disconnected'] = state()
    result['customer_owned_files'] = [p.name for p in data.glob('*.json') if p.name in (
        'customer-owned-tunnel.json', 'customer-leaving-network.json', 'customer-active-line.json',
        'customer-original-network.json', 'customer-clash-coexist.json', 'customer-clash-app-rules.json')]
    result['exit_while_disconnected'] = (not result['disconnected']['active'] and not result['disconnected']['recovering']
        and not result['customer_owned_files'] and any(e['code'] in ('attachment_lost', 'network_changed')
            and e['error'] and e['sequence'] > before_sequence for e in result['disconnected']['events'] or []))
    result['visible_window'] = window_visible()
finally:
    run('titan_restore_test_adapter.ps1')
    result['restored_at'] = time.time()
    (root / 'link-loss-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
time.sleep(8)
result['after'] = state()
assert result['after']['active'] is False, 'Must stay disconnected when campus returns'
assert result['exit_while_disconnected'], 'Owned cleanup and visible-event evidence required before restoring link'
assert result['visible_window'], 'A minimized window must be brought back for this critical alert'
(root / 'link-loss-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
