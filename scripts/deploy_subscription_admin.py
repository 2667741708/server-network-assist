"""Narrow static-page deployment. No backend, relay, client or route restart."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

MARKER = '<!-- SNA SUBSCRIPTION ENTRY -->'
BANNER = MARKER + '<a href="{url}" style="position:fixed;right:16px;bottom:16px;z-index:9999;max-width:calc(100vw - 32px);white-space:normal;overflow-wrap:anywhere;background:#1769aa;color:white;padding:12px 18px;border-radius:8px;text-decoration:none">源网订阅管理 · 生成订阅地址</a>'
BLOCK = '''\t# BEGIN SOURCE SUBSCRIPTION ADMIN
\tredir /subscription-admin /subscription-admin/subscriptions.html 302
\tredir /subscription-admin/ /subscription-admin/subscriptions.html 302
\thandle_path /subscription-admin/* {
\t\t@invalid_subscription_origin {
\t\t\tmethod POST
\t\t\tnot header Origin https://whm12.art
\t\t}
\t\t@subscription_paths path /subscriptions.html /subscriptions.js /subscriptions.css /dashboard.js /dashboard.css /tabler.min.css /tabler.min.js /TABLER-LICENSE.txt /tabler-assets.json /api/session /api/login /api/logout /api/reauth /api/client-service /api/client-service/dashboard /api/client-service/action
\t\troute {
\t\t\trespond @invalid_subscription_origin "Invalid origin" 403
\t\t\treverse_proxy @subscription_paths 10.201.250.10:9182 {
\t\t\t\theader_up Origin http://10.20.32.13:9182
\t\t\t\theader_up Cookie "(^|;[ ]*)panel_session=[^;]*" ""
\t\t\t\theader_up Cookie "(^|;[ ]*)sna_subscription_session=" "${1}panel_session="
\t\t\t\theader_down Set-Cookie "^panel_session=" "sna_subscription_session="
\t\t\t\theader_down Set-Cookie "Path=/" "Path=/subscription-admin/; Secure"
\t\t\t\ttransport http {
\t\t\t\t\tdial_timeout 4s
\t\t\t\t\tresponse_header_timeout 20s
\t\t\t\t}
\t\t\t}
\t\t\trespond "Not found" 404
\t\t}
\t}
\t# END SOURCE SUBSCRIPTION ADMIN

'''


def atomic(path, data):
    temporary = path.with_name(path.name + '.sna-new')
    temporary.write_bytes(data)
    if path.exists():
        temporary.chmod(path.stat().st_mode & 0o777)
    else:
        temporary.chmod(0o644)
    os.replace(temporary, path)


def source(stage):
    root = Path('/home/a/.local/lib/python3.13/site-packages/server_network_assist/ui')
    names = ['subscriptions.html', 'subscriptions.js', 'subscriptions.css', 'index.html']
    backup = stage / ('backup-' + str(time.time_ns()))
    backup.mkdir()
    originals = {name: (root / name).read_bytes() if (root / name).exists() else None for name in names}
    for name, body in originals.items():
        if body is not None:
            (backup / name).write_bytes(body)
    try:
        for name in names[:3]:
            atomic(root / name, (stage / name).read_bytes())
        index = originals['index.html'].decode()
        if MARKER not in index:
            index = index.replace('<body>', '<body>' + BANNER.format(url='subscriptions.html'), 1)
        assert MARKER in index
        atomic(root / 'index.html', index.encode())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for name in names[:3]:
            with opener.open('http://127.0.0.1:9182/' + name, timeout=5) as response:
                assert response.read() == (stage / name).read_bytes(), name
    except BaseException:
        for name, body in originals.items():
            if body is None:
                (root / name).unlink(missing_ok=True)
            else:
                atomic(root / name, body)
        raise
    print(json.dumps({'source_static_verified': True, 'backup': str(backup), 'service_restarted': False}))


def prepare(stage):
    original = Path('/etc/caddy/Caddyfile').read_bytes()
    text = original.decode()
    if '# BEGIN SOURCE SUBSCRIPTION ADMIN\n' in text:
        text, count = re.subn(r'\t# BEGIN SOURCE SUBSCRIPTION ADMIN\n.*?\t# END SOURCE SUBSCRIPTION ADMIN\n\n', '', text, flags=re.S)
        assert count == 1
    marker = '\t# BEGIN SERVER NETWORK ASSIST\n'
    assert text.count(marker) == 1
    wg_marker = '\tbind 10.201.250.1\n'
    assert text.count(wg_marker) == 1
    if '# BEGIN SOURCE SUBSCRIPTION ADMIN WG\n' in text:
        text, count = re.subn(r'\t# BEGIN SOURCE SUBSCRIPTION ADMIN WG\n.*?\t# END SOURCE SUBSCRIPTION ADMIN WG\n\n', '', text, flags=re.S)
        assert count == 1
    wg_block = BLOCK.replace('SOURCE SUBSCRIPTION ADMIN', 'SOURCE SUBSCRIPTION ADMIN WG').replace('not header Origin https://whm12.art', 'not header Origin http://10.201.250.1:9180').replace('Path=/subscription-admin/; Secure', 'Path=/subscription-admin/')
    text = text.replace(wg_marker, wg_marker + wg_block, 1)
    stage.mkdir(parents=True, exist_ok=True)
    (stage / 'original-caddy').write_bytes(original)
    (stage / 'candidate-caddy').write_text(text.replace(marker, BLOCK + marker, 1))
    print(json.dumps({'candidate': str(stage / 'candidate-caddy'), 'baseline_sha256': hashlib.sha256(original).hexdigest()}))


def cloud(stage):
    config = Path('/etc/caddy/Caddyfile')
    original = (stage / 'original-caddy').read_bytes()
    assert config.read_bytes() == original, 'Caddy changed since preparation'
    candidate = stage / 'candidate-caddy'
    subprocess.run(['caddy', 'validate', '--config', str(candidate), '--adapter', 'caddyfile'], check=True)
    index = Path('/opt/server-network-assist/venv/lib/python3.12/site-packages/server_network_assist/ui/index.html')
    old_index = index.read_bytes()
    (stage / 'original-cloud-index').write_bytes(old_index)
    backup = Path('/etc/caddy/Caddyfile.sna-subscriptions-' + str(time.time_ns()))
    backup.write_bytes(original)
    try:
        atomic(config, candidate.read_bytes())
        subprocess.run(['systemctl', 'reload', 'caddy'], check=True)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('https://whm12.art/subscription-admin/api/session', timeout=15) as response:
            assert json.load(response)['authenticated'] is False
        with opener.open('https://whm12.art/subscription-admin/subscriptions.html', timeout=15) as response:
            assert '生成订阅地址' in response.read().decode()
        with opener.open('http://10.201.250.1:9180/subscription-admin/api/session', timeout=10) as response:
            assert json.load(response)['authenticated'] is False
        request = urllib.request.Request('https://whm12.art/subscription-admin/api/login',
            data=b'{}', headers={'Origin': 'https://invalid.example', 'Content-Type': 'application/json'})
        try:
            opener.open(request, timeout=10)
            raise AssertionError('Untrusted Origin was not rejected')
        except urllib.error.HTTPError as error:
            assert error.code == 403
        text = old_index.decode()
        if MARKER not in text:
            text = text.replace('<body>', '<body>' + BANNER.format(url='/subscription-admin/subscriptions.html'), 1)
        assert MARKER in text
        atomic(index, text.encode())
    except BaseException:
        atomic(config, original)
        atomic(index, old_index)
        subprocess.run(['systemctl', 'reload', 'caddy'], check=True)
        raise
    print(json.dumps({'cloud_admin_verified': True, 'caddy_backup': str(backup), 'graceful_reload': True, 'backend_restarted': False}))


parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['source', 'prepare', 'cloud'])
parser.add_argument('--stage', type=Path, required=True)
args = parser.parse_args()
{'source': source, 'prepare': prepare, 'cloud': cloud}[args.action](args.stage)
