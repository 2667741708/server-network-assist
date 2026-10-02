"""One-off, explicitly authorized logout; not part of automatic client behavior."""
import json
import os
from pathlib import Path
import sys
from contextlib import closing
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urljoin, urlsplit

if os.environ.get('COMPUTERNAME', '').upper() != 'WHM-SAVE':
    raise SystemExit('WHM-SAVE only')
root = Path(r'C:\Users\hmw20\.ssh\gui-d321-control-20260917')
permission = json.loads((root / 'PERMISSION.json').read_text(encoding='utf-8-sig'))
if permission.get('campus_logout_allowed') is not True:
    raise SystemExit('Explicit campus logout authorization is required')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from server_network_assist import client_attachment, client_campus

attachment = client_attachment.snapshot()
link = client_campus.campus_link(attachment)
before = client_campus.authentication(attachment)
if before == 'offline':
    print(json.dumps({'before': before, 'after': before, 'logout_requested': False}))
    raise SystemExit(0)
if before != 'online':
    raise SystemExit('Campus authentication is unknown; do not guess or affect another session')

cookies = SimpleCookie()
url = client_campus.PORTAL
session = None
for _ in range(6):
    parts = urlsplit(url)
    if parts.hostname != 'auth1.ysu.edu.cn' or parts.scheme not in ('https', 'http') or parts.port not in (None, 443):
        raise SystemExit('Unexpected campus redirect; no logout performed')
    session = parse_qs(parts.query).get('sessionId', [None])[0]
    if session:
        break
    headers = {'User-Agent': 'Mozilla/5.0'}
    if cookies:
        headers['Cookie'] = '; '.join(f'{key}={item.value}' for key, item in cookies.items())
    with closing(client_campus.BoundHTTPS(parts.hostname, link)) as conn:
        conn.request('GET', parts.path + ('?' + parts.query if parts.query else ''), headers=headers)
        response = conn.getresponse()
        for key, value in response.getheaders():
            if key.lower() == 'set-cookie':
                cookies.load(value)
        location = response.getheader('Location')
        response.read(65536)
        if response.status not in (301, 302, 303, 307, 308) or not location:
            raise SystemExit('Campus portal did not identify the current physical session')
        url = urljoin(url, location)
if not session:
    raise SystemExit('Current physical campus session unavailable; no logout performed')

# Existing netlogin.py uses this same endpoint and JSON request shape.
headers = {'Content-Type': 'application/json', 'Origin': 'https://auth1.ysu.edu.cn',
           'Referer': 'https://auth1.ysu.edu.cn/portal/', 'User-Agent': 'Mozilla/5.0'}
if cookies:
    headers['Cookie'] = '; '.join(f'{key}={item.value}' for key, item in cookies.items())
with closing(client_campus.BoundHTTPS('auth1.ysu.edu.cn', link)) as conn:
    conn.request('POST', '/eportal/network/offline', json.dumps({'sessionId': session}), headers)
    response = conn.getresponse()
    value = json.loads(response.read(65536))
after = client_campus.authentication(client_attachment.snapshot())
print(json.dumps({'before': before, 'after': after, 'logout_requested': True,
                  'http_status': response.status, 'portal_code': value.get('code')}))
if after != 'offline':
    raise SystemExit('Logout not verified; do not proceed with client entry')
