"""Prepare one deployable static tree plus the separately reviewed cloud installer."""
from pathlib import Path
import base64
import hashlib
from html.parser import HTMLParser
import json
import shutil

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/project-deployment'
OUT.mkdir(parents=True,exist_ok=True)
site=OUT/'site'
if site.exists():
    if site.resolve() != ROOT.resolve()/'artifacts/project-deployment/site' or site.is_symlink():
        raise ValueError('Unexpected staging directory')
    shutil.rmtree(site)
source = ROOT/'site/blog/dist'
if not (source/'admin/index.html').is_file():
    raise RuntimeError('Build site/blog with npm run build before preparing deployment')
shutil.copytree(source,site)

class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.collect = False
        self.body = ''
        self.hashes = set()
    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.collect = 'src' not in dict(attrs)
            self.body = ''
    def handle_data(self, data):
        if self.collect:
            self.body += data
    def handle_endtag(self, tag):
        if tag == 'script' and self.collect:
            self.hashes.add("'sha256-" + base64.b64encode(hashlib.sha256(self.body.encode()).digest()).decode() + "'")
            self.collect = False

scripts = Scripts()
for html in site.rglob('*.html'):
    scripts.feed(html.read_text(encoding='utf-8'))
catalog = json.loads((ROOT/'site/projects/catalog.json').read_text(encoding='utf-8'))
manifest = dict(format=2, projects=len(catalog['projects']),
                paths=[p['name'] for p in catalog['projects']],
                script_hashes=sorted(scripts.hashes),
                files={p.relative_to(site).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(site.rglob('*')) if p.is_file() and p != site/'manifest.json'})
(site/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
shutil.copyfile(ROOT/'scripts/deploy_project_hub.py',OUT/'deploy_project_hub.py')
print('Prepared static project deployment at '+str(OUT))
