"""Emit reviewed deployment assets without credentials."""
import json
import shutil
from pathlib import Path
root = Path(__file__).resolve().parents[1]
source = root/'src/server_network_assist/subscription_admin_ui'
names = ['subscriptions.html','subscriptions.js','dashboard.js','smooth-navigation.js','smooth-navigation.css']
for name in names:
    shutil.copy2(source/name, root/'src/server_network_assist/ui'/name)
print(json.dumps({'assets': {'ui/'+name: (source/name).read_text(encoding='utf-8') for name in names},
    'deploy': (root/'scripts/deploy_smooth_navigation.py').read_text(encoding='utf-8')}, ensure_ascii=True))
