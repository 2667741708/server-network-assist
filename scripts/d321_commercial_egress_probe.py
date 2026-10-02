"""Read-only D321 proxy and current customer lease probe; prints no bearer keys."""
import json
from pathlib import Path
import socket
import winreg

if socket.gethostname().upper()!='DESKTOP-TD6B9GN':raise RuntimeError('D321 only')
data=Path('C:/ProgramData/ServerNetworkAssist/client/S-1-5-21-1446874470-693334550-1715965273-1001/gui-window-test-20260917')
lease=json.loads((data/'customer-online-lease.json').read_text(encoding='utf-8'))
active=json.loads((data/'customer-active-line.json').read_text(encoding='utf-8'))
with winreg.OpenKey(winreg.HKEY_USERS,r'S-1-5-21-1446874470-693334550-1715965273-1001\Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
    enabled=winreg.QueryValueEx(key,'ProxyEnable')[0]
try:
    with socket.create_connection(('127.0.0.1',7897),timeout=2):listening=True
except OSError:listening=False
print(json.dumps({'proxy_enable':enabled,'local_proxy_listening':listening,'active_tunnel':active.get('tunnel'),
                  'source_endpoint':lease.get('endpoint'),'dns':lease.get('dns'),'lease_expires_at':lease.get('expires_at')}))
