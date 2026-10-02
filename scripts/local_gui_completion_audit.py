"""Read only the user-authorized native GUI state; never print credentials."""
import json
import os
import ctypes
from pathlib import Path
import sys
from datetime import datetime

if os.environ.get('COMPUTERNAME', '').upper() != 'WHM-SAVE':
    raise SystemExit('WHM-SAVE only')
if not ctypes.windll.shell32.IsUserAnAdmin():
    raise SystemExit('Read audit requires authorized administrator SSH context; no elevation attempted')
repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / 'src'))
from server_network_assist import client_attachment, client_campus

control = Path(r'C:\Users\hmw20\.ssh\gui-d321-control-20260917')
settings = json.loads((control / 'gui-config.json').read_text(encoding='utf-8-sig'))
data = Path(settings['data'])
online = data / 'customer-online-service.json'
configured = online.exists()
record = json.loads(online.read_text(encoding='utf-8-sig')) if configured else {}
attachment = client_attachment.snapshot()
link, route = client_attachment.preferred(attachment)
report = {
    'time': datetime.now().astimezone().isoformat(),
    'configured': configured,
    'customer_id': record.get('customer_id'),
    'active': (data / 'customer-active-line.json').exists(),
    'owned': (data / 'customer-owned-tunnel.json').exists(),
    'recovering': (data / 'customer-leaving-network.json').exists(),
    'personal_campus_authentication': client_campus.authentication(attachment),
    'physical_link': {key: link.get(key) for key in ('id', 'name', 'addresses', 'ssid')} if link else None,
}
target = repo / 'artifacts' / 'local-gui-current-audit-20260917.json'
try:
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
except OSError as exc:
    print(f'Optional audit file unavailable: {type(exc).__name__}', file=sys.stderr)
print(json.dumps(report, ensure_ascii=False))
