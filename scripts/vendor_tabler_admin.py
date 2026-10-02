"""Vendor pinned, prebuilt Tabler assets. Never execute package code."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

VERSION='1.5.1'
URL=f'https://registry.npmjs.org/@tabler/core/-/core-{VERSION}.tgz'
root=Path(__file__).resolve().parents[1]
ui=root/'src/server_network_assist/subscription_admin_ui'
cache=root/'artifacts/tabler-core-1.5.1.tgz'
if cache.exists():payload=cache.read_bytes()
else:
    with urllib.request.urlopen(URL,timeout=60) as response:payload=response.read()
    cache.write_bytes(payload)
files={};licenses=[]
with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as archive:
    for member in archive:
        if not member.isfile():continue
        name=member.name
        if name in ['package/dist/css/tabler.min.css','package/dist/js/tabler.min.js']:
            files[Path(name).name]=archive.extractfile(member).read()
        if name in ['package/LICENSE','package/LICENSE.md','package/LICENSE.txt']:
            licenses.append(archive.extractfile(member).read())
if not licenses:
    with urllib.request.urlopen('https://raw.githubusercontent.com/tabler/tabler/%40tabler%2Fcore%401.5.1/LICENSE',timeout=30) as response:licenses.append(response.read())
assert set(files)=={'tabler.min.css','tabler.min.js'} and licenses
for name,body in files.items():(ui/name).write_bytes(body)
(ui/'TABLER-LICENSE.txt').write_bytes(licenses[0])
manifest={'version':VERSION,'source':URL,'package_sha256':hashlib.sha256(payload).hexdigest(),
          'files':{n:hashlib.sha256(b).hexdigest() for n,b in files.items()}}
(ui/'tabler-assets.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
print(json.dumps(manifest))
