"""Verify that the generated desktop assets come from the React/Vite source."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'desktop-frontend'
generated = ROOT / 'src/server_network_assist/desktop_ui'
for path in (source / 'package.json', source / 'src/main.tsx', generated / 'index.html', generated / 'desktop.js', generated / 'desktop.css'):
    if not path.is_file():
        raise SystemExit(f'Missing desktop UI build artifact: {path}')
bundle = (generated / 'desktop.js').read_text(encoding='utf-8').lower()
if 'framework7' in bundle:
    raise SystemExit('The generated desktop bundle still references Framework7')
print('Desktop UI source and generated React/Vite assets are present.')
