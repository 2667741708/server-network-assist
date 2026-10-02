"""Redacted, read-only checks for the user-requested Titan Codex task."""
import json
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from server_network_assist import clash_control

path = Path(r'C:\Users\86133\AppData\Roaming\io.github.clash-verge-rev.clash-verge-rev\clash-verge.yaml')
base, secret = clash_control.controller(path)
config = clash_control.api(base, secret, 'GET', '/configs')
proxies = clash_control.api(base, secret, 'GET', '/proxies').get('proxies', {})
print(json.dumps({'clash': {'mode': config.get('mode'), 'mixed_port': config.get('mixed-port'),
    'tun': (config.get('tun') or {}).get('enable'),
    'groups': [{'name': name, 'selected': value.get('now'), 'type': value.get('type')}
               for name, value in proxies.items() if isinstance(value, dict) and value.get('all')]}}, ensure_ascii=False))
settings = tomllib.loads(Path(r'C:\Users\86133\.codex\config.toml').read_text(encoding='utf-8-sig'))
provider = settings.get('model_provider', 'openai')
provider_settings = settings.get('model_providers', {}).get(provider, {})
print(json.dumps({'codex': {'model_provider': provider,
    'endpoint_host': urlsplit(provider_settings.get('base_url', 'https://chatgpt.com/backend-api/codex')).hostname}}, ensure_ascii=False))
