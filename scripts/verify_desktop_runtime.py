"""Read-only deployed desktop smoke test. Never prints the local auth token."""
import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request


def verify(data, expected=None, campus_account=None):
    state = json.loads((data/'desktop-instance.json').read_text(encoding='utf-8'))
    origin = f"http://127.0.0.1:{int(state['port'])}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get(path, authenticated=True):
        request = urllib.request.Request(origin+path,
            headers={'X-Desktop-Token': state['token']} if authenticated else {})
        try:
            with opener.open(request, timeout=35) as response:
                content = response.read()
                return response.status, json.loads(content) if path.startswith('/api/') else content
        except urllib.error.HTTPError as error:
            return error.code, {}
    code, health = get('/api/health')
    assert code == 200 and health.get('pid') == state['pid'], 'Health identity mismatch'
    if expected:
        assert health.get('version') == expected, 'Running version differs from release'
    code, status = get('/api/status')
    assert code == 200, 'Status request failed'
    checks = {}
    for path in ['/api/fleet/hosts', '/api/fleet/credentials', '/api/fleet/network',
                 '/api/proxy', '/api/diagnostics']:
        checks[path] = get(path)[0]
    denied = get('/api/status', False)[0]
    assert denied == 403, 'Unauthenticated status was not rejected'
    if expected:
        assert all(code == 200 for code in checks.values()), checks
    campus = None
    if campus_account:
        code, configured = get('/api/campus')
        assert code == 200 and configured.get('configured'), 'Campus script not configured'
        code, campus = get('/api/campus/status')
        assert code == 200 and campus.get('online'), 'Campus account not online'
        assert campus.get('account') == campus_account, 'Campus account mismatch'
        assert campus.get('internet_online') is True, 'Campus internet probe did not pass'
        assert not any(t.get('active') for t in status.get('tunnels', [])), 'A local sharing tunnel remains active'
        assert denied == get('/api/campus/status', False)[0], 'Campus status requires token'
    return dict(health=health, port=state['port'], checks=checks, unauthenticated_status=denied,
        campus=campus,
        direct=status.get('direct'), system=status.get('system'),
        proxy_enabled=status.get('proxy', {}).get('enabled'), background=status.get('background'),
        tunnels=[dict(name=t['name'], active=t['active'], start_mode=t.get('start_mode')) for t in status.get('tunnels', [])])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--expected-version')
    parser.add_argument('--expected-campus-account')
    args = parser.parse_args()
    print(json.dumps(verify(args.data, args.expected_version, args.expected_campus_account), ensure_ascii=True))
