"""Anonymous entry probes: no enrollment, leases, credentials or network edits."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import ssl
import sys
import urllib.error
import urllib.request

sys.path.insert(0, 'C:/Users/86133/subscription-entry-probe-20260918')
from server_network_assist.client_attachment import snapshot
from server_network_assist.client_lan_transport import subscription_opener

attachment = snapshot()
context = ssl.create_default_context()
if hasattr(ssl, 'VERIFY_X509_STRICT'):
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
entries = [
    ('campus', 'http://10.20.32.13:9182', '/subscriptions.html'),
    ('management-wg', 'http://10.201.250.1:9180', '/subscription-admin/subscriptions.html'),
    ('public', 'https://whm12.art', '/subscription-admin/subscriptions.html'),
]

def probe(item):
    name, base, page = item
    rows = []
    for transport in ('ordinary-no-proxy', 'physical-client'):
        for path in (page, '/client/v1/subscription'):
            row = {'entry': name, 'transport': transport, 'url': base + path}
            try:
                opener = (subscription_opener(base, context, attachment=attachment)
                          if transport == 'physical-client' else urllib.request.build_opener(
                              urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)))
                with opener.open(base + path, timeout=7) as response:
                    body = response.read(256 * 1024)
                    row.update(status=response.status, content_type=response.headers.get('Content-Type'),
                               sha256=hashlib.sha256(body).hexdigest(),
                               campus_default=b'value="http://10.20.32.13:9182"' in body,
                               new_speed_controls=b'data-speed-preset="unlimited"' in body)
            except urllib.error.HTTPError as error:
                row.update(status=error.code, content_type=error.headers.get('Content-Type'))
                error.close()
            except (OSError, ValueError) as error:
                reason = error.reason if isinstance(error, urllib.error.URLError) else error
                row.update(error_type=type(reason).__name__, error=str(reason)[:400])
            rows.append(row)
    return rows

report = {'network_modified': False, 'enrolled': False,
          'attachment': [{'name': x.get('name'), 'addresses': x.get('addresses'),
                          'defaults': x.get('defaults'), 'dns': x.get('dns')}
                         for x in attachment.get('links', []) if x.get('connected')]}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    report['probes'] = [row for rows in pool.map(probe, entries) for row in rows]
records = []
for user in Path('C:/Users').iterdir():
    for folder in ('AppData/Local/ServerNetworkAssist', 'AppData/Local/PureNetworkClient',
                   'AppData/Local/PureEnjoyNetwork', '.server-network-assist'):
        directory = user / folder
        if not directory.is_dir():
            continue
        for p in directory.rglob('customer-online-service.json'):
            try:
                value = json.loads(p.read_text(encoding='utf-8'))
                records.append({'path': str(p), 'base_url': value.get('base_url'),
                                'customer_id': value.get('customer_id')})
            except (OSError, ValueError):
                pass
report['saved_service_entries'] = records
for p in Path('C:/ProgramData/ServerNetworkAssist/client').glob('*/customer-online-service.json'):
    try:
        value = json.loads(p.read_text(encoding='utf-8'))
        records.append({'path': str(p), 'base_url': value.get('base_url'),
                        'customer_id': value.get('customer_id')})
    except (OSError, ValueError):
        pass
print(json.dumps(report, ensure_ascii=True))
