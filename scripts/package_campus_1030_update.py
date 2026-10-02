"""Create the verified 10.30 update ZIP using previously bundled offline installers."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'artifacts/client-offline-20260917'
NEW=ROOT/'artifacts/client-campus-1030-20260918-v2'
for name in ('Install.exe','dependencies/wireguard-amd64.msi',
             'dependencies/MicrosoftEdgeWebView2RuntimeInstallerX64.exe','wireguard-windows-v1.1-source.zip'):
    target=NEW/name
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(OLD/name,target)
digest=hashlib.sha256((NEW/'ServerNetworkAssistClient.exe').read_bytes()).hexdigest()
subprocess.run([sys.executable,str(ROOT/'scripts/package_customer_distribution.py'),
    '--artifact',str(NEW),'--expected-hash',digest,'--revision','20260918-campus-1030',
    '--zip-name','pure-network-client-windows-x64-offline-20260918.zip','--installer','--offline-dependencies'],check=True)
(NEW/'release-notes.md').write_text('''# Pure Network Client: 10.30 campus compatibility

- Windows x64 update: supports the operator-authorized 10.30.0.0/16 campus range.
- Requires a physical-interface source-service probe and existing verified campus authentication before a lease; keeps hotspot and IPv6-default safeguards.
- Displays the exact blocking adapter, IP and gateway. Subscription checks retain the original access-block reason.
- 65 focused regression tests passed. Frozen-source and offline-package verification are reported separately. Real connectivity on the reported customer PC remains unverified.
- To upgrade: exit the old client, then replace/run ServerNetworkAssistClient.exe; saved subscriptions remain. New installations use the full offline ZIP and Install.exe.
- No customer subscriptions, keys or administrator credentials are included. Source snapshot matches the working source; the release tag base does not represent all uncommitted project work.
''',encoding='utf-8')
print(digest)
