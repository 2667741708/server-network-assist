"""Build the native customer client; never run network operations on the build PC."""
from pathlib import Path
import subprocess
import sys
import hashlib
import json
import argparse
import importlib.util

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=root / 'artifacts/client-exe')
output = parser.parse_args().output.resolve()
required = ('PyInstaller', 'webview', 'clr', 'pystray', 'PIL', 'yaml')
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit('Build runtime is missing: ' + ', '.join(missing) + '. Use artifacts/desktop-build-env/Scripts/python.exe; no EXE was built.')
manifest = root / 'artifacts/client-exe-source-manifest.json'
manifest.parent.mkdir(parents=True, exist_ok=True)
files = list((root / 'src/server_network_assist').glob('*.py'))
files += list((root / 'src/server_network_assist').glob('*.ps1'))
files += [p for p in (root / 'src/server_network_assist/client_ui').rglob('*') if p.is_file()]
files += [root / 'src/server_network_assist/subscription_admin_ui' / name
          for name in ('smooth-navigation.js', 'smooth-navigation.css')]
files += [root / 'scripts/client_exe_entry.py', Path(__file__),
          root / 'src/server_network_assist/desktop_ui/icon.ico',
          root / 'scripts/client_windows_version.txt']
manifest.write_text(json.dumps({'files': {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(files)}}, indent=2), encoding='utf-8')
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
    '--windowed',
    '--uac-admin', '--name', 'ServerNetworkAssistClient',
    '--icon', str(root / 'src/server_network_assist/desktop_ui/icon.ico'),
    '--version-file', str(root / 'scripts/client_windows_version.txt'),
    '--paths', str(root / 'src'), '--distpath', str(output),
    '--workpath', str(root / 'artifacts/client-exe-build'), '--specpath', str(root / 'artifacts'),
    '--add-data', str(root / 'src/server_network_assist/client_ui') + ';server_network_assist/client_ui',
    '--add-data', str(root / 'src/server_network_assist/subscription_admin_ui/smooth-navigation.js') + ';server_network_assist/subscription_admin_ui',
    '--add-data', str(root / 'src/server_network_assist/subscription_admin_ui/smooth-navigation.css') + ';server_network_assist/subscription_admin_ui',
    '--add-data', str(root / 'src/server_network_assist/client_attachment_windows.ps1') + ';server_network_assist',
    '--add-data', str(root / 'src/server_network_assist/desktop_network.ps1') + ';server_network_assist',
    '--add-data', str(root / 'src/server_network_assist/desktop_ui/icon.ico') + ';server_network_assist/desktop_ui',
    '--hidden-import', 'pystray', '--hidden-import', 'PIL.Image',
    '--add-data', str(manifest) + ';server_network_assist',
    '--collect-all', 'webview', '--hidden-import', 'yaml', '--hidden-import', 'clr',
    str(root / 'scripts/client_exe_entry.py')], cwd=root, check=True)
