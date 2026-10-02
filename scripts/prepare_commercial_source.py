#!/usr/bin/env python3
"""Prepare public source registration; does not activate privileged networking."""
import base64
import json
from pathlib import Path
import subprocess

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from server_network_assist.client_store import ClientStore

data = Path('/home/a/.local/share/server-network-assist-commercial/data')
key_path = data.parent / 'sna-commercial.key'
if key_path.exists():
    key = X25519PrivateKey.from_private_bytes(base64.b64decode(key_path.read_text().strip(), validate=True))
else:
    key = X25519PrivateKey.generate()
    key_path.write_text(base64.b64encode(key.private_bytes_raw()).decode(), encoding='ascii')
    key_path.chmod(0o600)
route = json.loads(subprocess.run(['ip','-j','route','get','1.1.1.1'], capture_output=True, text=True, check=True).stdout)[0]
source = ClientStore(data / 'commercial-service.sqlite3').save_source({
    'id':'c201-4090-commercial','name':'C201-4090 商业源网','endpoint':'10.20.32.13:51910',
    'relay_public_key':base64.b64encode(key.public_key().public_bytes_raw()).decode(),
    'address_pool':'10.213.40.0/24','relay_interface':'sna-commercial','egress_interface':route['dev']})
print(json.dumps({'prepared':True,'network_activated':False,'source':source}, ensure_ascii=False))
