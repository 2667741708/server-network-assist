"""Package allowlisted public source corresponding to the customer binary."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import zipfile

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--artifact',type=Path,required=True)
args=parser.parse_args()
artifact=args.artifact.resolve()
source_manifest=json.loads((root/'artifacts/client-exe-source-manifest.json').read_text(encoding='utf-8'))
for name,digest in source_manifest['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest,name
files=[p for p in (root/'src/server_network_assist').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix.lower() in {'.py','.ps1','.html','.js','.css','.json','.ico','.txt','.md'}]
files += [root/name for name in ('pyproject.toml','README.md','LICENSE','THIRD_PARTY_NOTICES.md',
    'scripts/build_client_exe.py','scripts/client_exe_entry.py','scripts/client_windows_version.txt',
    'scripts/build_customer_installer.py','scripts/customer_installer_entry.py',
    'scripts/customer_install_windows.ps1','scripts/customer_dependencies_windows.ps1',
    'scripts/package_customer_distribution.py','scripts/package_customer_source_snapshot.py',
    'scripts/verify_wifi_client_exe.py','scripts/verify_offline_customer_bundle.py',
    'scripts/check_customer_installer_syntax.ps1','tests/test_client_dependencies.py',
    'tests/test_client_install.py','docs/WINDOWS_CUSTOMER_QUICKSTART.md')]
entries={p.relative_to(root).as_posix():p.read_bytes() for p in files}
# This is a public source allowlist, not a checkout/data-directory archive.
for name,payload in entries.items():
    if Path(name).suffix.lower() in {'.py','.ps1','.json','.md','.txt','.html','.js'}:
        assert not re.search(rb'(?<![A-Za-z0-9])(?:enr_|ghp_|gho_)[A-Za-z0-9_-]{20,}',payload),name
versions=sorted({(d.metadata['Name'],d.version) for d in importlib.metadata.distributions()})
entries['build-runtime-requirements.txt']=''.join(f'{name}=={version}\n' for name,version in versions).encode('utf-8')
entries['client-exe-source-manifest.json']=json.dumps(source_manifest,indent=2).encode('utf-8')
entries['SNAPSHOT-README.md']=('This asset contains the working-source snapshot matching the offline customer binary, not only the release tag base commit.\n'
    'Build on Windows x64 with Python 3.13.9: create a venv and install build-runtime-requirements.txt.\n'
    'Run scripts/build_client_exe.py and scripts/build_customer_installer.py with --output artifacts/client-offline-20260917.\n'
    'The official offline vendor installers and WireGuard upstream source are in the customer distribution ZIP.\n'
    'No subscriptions, device keys, administrator credentials or user-data directories are included.\n').encode('utf-8')
destination=artifact/'pure-network-client-source-20260917.zip'
with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
    for name,payload in sorted(entries.items()):
        archive.writestr('server-network-assist/'+name,payload)
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
print(json.dumps({'source_zip':str(destination),'files':len(entries),'bytes':destination.stat().st_size,
    'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'contains_user_data':False}))
