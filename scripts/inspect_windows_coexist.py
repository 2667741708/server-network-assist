"""Safe, read-only inspection of the current Windows Clash controller/runtime."""
import json
import os
from pathlib import Path
from server_network_assist import clash_control

path = clash_control.candidates()[0]
base, secret = clash_control.controller(path)
result = {'config': str(path), 'controller': bool(base), 'proxy': clash_control.system_proxy()}
if base:
    config = clash_control.api(base, secret, 'GET', '/configs')
    result['clash'] = {k: config.get(k) for k in ['mode', 'mixed-port', 'tun', 'dns', 'interface-name', 'routing-mark']}
    result['version'] = clash_control.api(base, secret, 'GET', '/version')
root = Path(os.environ['PROGRAMDATA']) / 'ServerNetworkAssist'
result['client_installed'] = (root / 'desktop/0.7.0/python.exe').exists()
print(json.dumps(result, ensure_ascii=False))
