"""Read-only campus evidence; do not edit routing or relax client protection."""
from pathlib import Path
from contextlib import closing
import http.client
import json
import socket
import sys
import ssl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from server_network_assist import client_attachment, client_campus

attachment = client_attachment.snapshot()
link, route = client_attachment.preferred(attachment)
print(json.dumps({'attachment': attachment, 'preferred': link, 'route': route}, ensure_ascii=False))
if not link:
    raise SystemExit('No physical default')
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
    connection.settimeout(4)
    connection.setsockopt(socket.IPPROTO_IP, 31, socket.htonl(int(link['id'])))
    connection.bind((link['addresses'][0], 0))
    try:
        connection.connect(('10.20.32.13', 9182))
        connection.sendall(b'GET /api/session HTTP/1.1\r\nHost: 10.20.32.13:9182\r\nConnection: close\r\n\r\n')
        print(json.dumps({'source_status': connection.recv(512).split(b'\r\n', 1)[0].decode('ascii')}))
    except OSError as exc:
        print(json.dumps({'source_error_type': type(exc).__name__}))
try:
    with closing(client_campus.BoundHTTPS('auth1.ysu.edu.cn', link)) as connection:
        connection.request('GET', '/')
        response = connection.getresponse()
        location = response.getheader('Location', '')
        print(json.dumps({'portal_tls_verified': True, 'status': response.status,
                          'redirect_host': __import__('urllib.parse', fromlist=['urlsplit']).urlsplit(location).hostname}))
except (OSError, ValueError, http.client.HTTPException) as exc:
    print(json.dumps({'portal_tls_verified': False, 'error_type': type(exc).__name__}))
