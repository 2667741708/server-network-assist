"""Vendor only CSS and LICENSE from a pinned npm package; never execute package code."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.request import urlopen

VERSION = '9.1.3'
DEST = Path(__file__).resolve().parents[1] / 'docs/vendor/framework7'

def main():
    with urlopen(f'https://registry.npmjs.org/framework7/{VERSION}', timeout=30) as response:
        metadata = json.load(response)
    assert metadata['version'] == VERSION and metadata['license'] == 'MIT'
    url = metadata['dist']['tarball']
    assert url == f'https://registry.npmjs.org/framework7/-/framework7-{VERSION}.tgz'
    with urlopen(url, timeout=60) as response:
        data = response.read(20_000_000)
    integrity = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
    assert integrity == metadata['dist']['integrity'], 'Package integrity mismatch'
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as package:
        chosen = {}
        for name in ('framework7-bundle.min.css', 'framework7-bundle.min.js', 'LICENSE'):
            member = package.getmember('package/' + name)
            assert member.isfile()
            chosen[name] = package.extractfile(member).read()
    DEST.mkdir(parents=True, exist_ok=True)
    for name, content in chosen.items():
        (DEST / name).write_bytes(content)
    (DEST / 'provenance.json').write_text(json.dumps({
        'name': 'framework7', 'version': VERSION, 'license': 'MIT',
        'source': url, 'integrity': integrity,
        'files': {name: hashlib.sha256(content).hexdigest() for name, content in chosen.items()},
    }, indent=2) + '\n', encoding='utf-8')
    print(f'Vendored Framework7 {VERSION} CSS and MIT license; package scripts were not executed.')

if __name__ == '__main__':
    main()
