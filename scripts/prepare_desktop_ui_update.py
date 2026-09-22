"""Stage only the reviewed React desktop view and asset routes, never network settings."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/desktop-ui-update'
OUT.mkdir(parents=True, exist_ok=True)
files = ['desktop.py'] + ['desktop_ui/' + name for name in ('index.html', 'desktop.js', 'desktop.css')]
manifest = []
for name in files:
    source = ROOT / 'src/server_network_assist' / name
    destination = OUT / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    manifest.append({'path':name, 'sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
(OUT / 'manifest.json').write_text(json.dumps({'files':manifest},indent=2)+'\n',encoding='utf-8')
shutil.copyfile(ROOT/'scripts/update_desktop_ui.ps1', OUT/'update_desktop_ui.ps1')
print(f'Staged {len(files)} files at {OUT}')
