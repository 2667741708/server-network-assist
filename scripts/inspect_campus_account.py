"""Read campus authentication status only; never call Logout or modify networking."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from server_network_assist.client_attachment import snapshot
from server_network_assist.client_campus import authentication

print(json.dumps({'authentication': authentication(snapshot()), 'logout_sent': False,
                  'network_changed': False}, ensure_ascii=True))
