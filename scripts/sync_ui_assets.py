"""Copy the reviewed, vendored UI stylesheet into the desktop Python package."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'docs/vendor/framework7'
destination = ROOT / 'src/server_network_assist/desktop_ui'
provenance = json.loads((source / 'provenance.json').read_text())
for original, output in [('framework7-bundle.min.css', 'framework7-bundle.min.css'),
                         ('LICENSE', 'FRAMEWORK7-LICENSE.txt')]:
    assert hashlib.sha256((source / original).read_bytes()).hexdigest() == provenance['files'][original]
    shutil.copyfile(source / original, destination / output)
shutil.copyfile(source / 'provenance.json', destination / 'framework7-provenance.json')
shutil.copyfile(source / 'framework7-default-theme.css', destination / 'framework7-default-theme.css')
print('Desktop UI assets match the reviewed Framework7 package.')
