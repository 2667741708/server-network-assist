"""Anonymous transport probes only: no enrollment, lease, Wi-Fi or route changes."""
import json
from pathlib import Path
import urllib.error
import urllib.request
from server_network_assist.client_attachment import snapshot
from server_network_assist.client_lan_transport import subscription_opener, subscription_links, connect

attachment = snapshot()
report = {'enrolled': False, 'lease_requested': False, 'network_changed': False}
try:
    opener = subscription_opener('https://whm12.art', attachment=attachment)
    try:
        with opener.open('https://whm12.art/client/v1/subscription', timeout=8) as response:
            report['public_api_status'] = response.status
    except urllib.error.HTTPError as error:
        report['public_api_status'] = error.code
        report['public_api_response'] = json.loads(error.read(2048))
    report['public_api_reachable'] = report['public_api_status'] == 401
    with opener.open('https://whm12.art/subscription-admin/subscriptions.html', timeout=8) as response:
        report['manager_page_available'] = '生成订阅地址' in response.read(20000).decode()
except (OSError, ValueError, urllib.error.URLError) as error:
    report['public_api_reachable'] = False
    report['error'] = type(error).__name__ + ': ' + str(error)[:300]
report['paths'] = [{'name': row.get('name'), 'wifi': row.get('wifi'), 'addresses': row.get('addresses')}
                   for row in subscription_links(attachment, '10.20.32.13')]
report['campus_source_tcp_reachable'] = False
for row in subscription_links(attachment, '10.20.32.13')[:4]:
    try:
        with connect('10.20.32.13', 9182, row):
            report['campus_source_tcp_reachable'] = True
            break
    except (OSError, ValueError):
        pass
try:
    Path('artifacts/public-subscription-readonly-20260917.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
except OSError:
    print('Optional diagnostic report could not be written.')
print(json.dumps(report, ensure_ascii=True))
