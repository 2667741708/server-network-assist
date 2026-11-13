"""Publish the current main branch's verified CI blog artifact, never an older run.

Root-owned script invoked by a systemd timer. Credentials stay in the existing
server-side gh account. Does not execute programs downloaded in the artifact.
"""
import base64
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import time

REPO = '2667741708/server-network-assist'
STATE = Path('/var/lib/blog-publisher/status.json')
STAGING = Path('/home/ubuntu/blog-publisher-runs')
DEPLOY = Path('/usr/local/libexec/whm-blog-deploy.py')
CADDY = Path('/etc/caddy/Caddyfile')


def run(args, timeout=40):
    environment = os.environ.copy()
    if args[0] == 'gh':
        environment['GH_TOKEN'] = Path('/etc/blog-email-auth/github.token').read_text().strip()
    result = subprocess.run(args, capture_output=True, timeout=timeout, env=environment)
    if result.returncode:
        detail = result.stderr.decode(errors='replace')[-2400:]
        raise RuntimeError(Path(args[0]).name + ' failed: ' + detail)
    return result.stdout


def api(path):
    return json.loads(run(['gh', 'api', 'repos/' + REPO + '/' + path]))


def status(stage, head, **extra):
    STATE.parent.mkdir(exist_ok=True, mode=0o700)
    data = {'stage': stage, 'head': head, 'checked_at': datetime.now(timezone.utc).isoformat(), **extra}
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(data))
    temporary.chmod(0o600)
    temporary.replace(STATE)


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
            digest = base64.b64encode(hashlib.sha256(self.body.encode()).digest()).decode()
            self.hashes.add("'sha256-" + digest + "'")
            self.collect = False


def main():
    head = api('git/ref/heads/main')['object']['sha']
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    if previous.get('published_head') == head:
        return
    runs = api('actions/workflows/ci.yml/runs?branch=main&event=push&per_page=5')['workflow_runs']
    runs = [item for item in runs if item['head_sha'] == head]
    if not runs or runs[0]['status'] != 'completed':
        status('building', head, published_head=previous.get('published_head'))
        return
    job = runs[0]
    artifacts = api(f"actions/runs/{job['id']}/artifacts")['artifacts']
    artifacts = [item for item in artifacts if item['name'] == 'blog-production' and not item['expired']]
    if len(artifacts) != 1:
        status('build_failed', head, run_id=job['id'], published_head=previous.get('published_head'))
        return
    # The blog job uploads this artifact only after the CMS/build checks pass.
    jobs = api(f"actions/runs/{job['id']}/jobs?per_page=100")['jobs']
    if not any(item['name'] == 'blog' and item['conclusion'] == 'success' for item in jobs):
        status('build_failed', head, run_id=job['id'], published_head=previous.get('published_head'))
        return
    STAGING.mkdir(exist_ok=True, mode=0o700)
    root = STAGING / (str(job['id']) + '-' + str(time.time_ns()))
    site = root / 'site'
    site.mkdir(parents=True, mode=0o700)
    run(['gh', 'run', 'download', str(job['id']), '--repo', REPO,
         '--name', 'blog-production', '--dir', str(site)], timeout=240)
    # The generic CI build has no production OAuth environment. Supply only
    # public endpoint settings here; all actual credentials remain server-side.
    cms_path = site / 'admin/config.json'
    cms = json.loads(cms_path.read_text())
    cms['oauthConfigured'] = True
    cms['config']['backend'].update(base_url='https://whm12.art/projects/oauth', auth_endpoint='auth')
    cms_path.write_text(json.dumps(cms, ensure_ascii=False, indent=2))
    scripts = Scripts()
    for html in site.rglob('*.html'):
        if html.is_symlink():
            raise ValueError('Symlinks are not allowed in the artifact')
        scripts.feed(html.read_text())
    projects = []
    for name in ('catalog', 'documentation'):
        content = api(f'contents/site/projects/{name}.json?ref={head}')['content']
        projects.extend(json.loads(base64.b64decode(content))['projects'])
    if len({item['name'] for item in projects}) != len(projects):
        raise ValueError('Duplicate project routes')
    manifest = {
        'format': 2, 'projects': len(projects), 'paths': [item['name'] for item in projects],
        'script_hashes': sorted(scripts.hashes),
        'files': {item.relative_to(site).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
                  for item in sorted(site.rglob('*')) if item.is_file() and item.name != 'manifest.json'},
    }
    (site / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    if api('git/ref/heads/main')['object']['sha'] != head:
        status('building', head, published_head=previous.get('published_head'))
        return
    caddy_sha = hashlib.sha256(CADDY.read_bytes()).hexdigest()
    # Deployment validates the inventory/checksums/routes, swaps the symlink,
    # checks the public site, and rolls back on failure.
    run(['/usr/bin/python3', str(DEPLOY), '--source', str(site),
         '--expected-config-sha256', caddy_sha], timeout=480)
    status('published', head, published_head=head, run_id=job['id'])
    print('Published blog build ' + head[:12])


if __name__ == '__main__':
    main()
