"""Freeze a small build input tree while another task repairs the same repository."""
import hashlib
import json
from pathlib import Path
import shutil
import argparse
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--name', default='brand-build-snapshot-20260917')
args = parser.parse_args()
if Path(args.name).name != args.name:
    raise SystemExit('Snapshot name must be a single directory name')
target = ROOT / 'artifacts' / args.name
if target.exists():
    raise SystemExit('Snapshot exists; do not overwrite it')
files = list((ROOT / 'src/server_network_assist').rglob('*'))
files = [p for p in files if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
files += [ROOT / 'scripts' / name for name in
          ['build_client_exe.py', 'client_exe_entry.py', 'client_windows_version.txt', 'verify_wifi_client_exe.py']]
hashes = {}
for source in files:
    content = source.read_bytes()
    destination = target / source.relative_to(ROOT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    hashes[str(source.relative_to(ROOT))] = hashlib.sha256(content).hexdigest()
(target / 'snapshot.json').write_text(json.dumps({'time': datetime.now().astimezone().isoformat(), 'files': hashes}, indent=2), encoding='utf-8')
print(str(target))
