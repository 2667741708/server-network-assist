"""Only inspect physical adapters and scan WLAN; do not run a client or change networking."""
import json
from pathlib import Path
from server_network_assist.client_attachment import snapshot, preferred
from server_network_assist.client_campus import campus_link
from server_network_assist.client_wifi import scan

attachment = snapshot()
link, _ = preferred(attachment)
try:
    campus_link(attachment)
    campus = 'configured-campus-address'
except ValueError as exc:
    campus = str(exc)
wifi = scan()
result = {'physical_type': ('wifi' if link.get('wifi') else 'ethernet') if link else 'none',
    'physical_addresses': link.get('addresses', []) if link else [],
    'ipv6_default_present': bool(link and link.get('defaults6')),
    'campus_address_check': campus, 'wireless_count': len(wifi['networks']),
    'connected_wireless_count': sum(row['connected'] for row in wifi['networks']),
    'wireless_message': wifi['message'], 'network_changed': False,
    'note': 'Address policy only; this check does not claim campus service reachability.'}
try:
    Path('artifacts/windows-wifi-readonly-20260917.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
except OSError:
    print('Optional wireless report could not be written.')
print(json.dumps(result))
