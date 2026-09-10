#!/usr/bin/env python3
"""Deploy reviewed static files to cloud, preserve existing Caddy routes, rollback on failure.

Upload the generated docs/projects tree separately; run this file with sudo on cloud.
No dependency installation, DNS changes, credential export, or application restarts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.request

BASE = Path('/var/www/whm-projects')
CADDYFILE = Path('/etc/caddy/Caddyfile')
BEGIN = '# BEGIN WHM PROJECT HUB'
END = '# END WHM PROJECT HUB'
BLOCK = '''
    # BEGIN WHM PROJECT HUB
    redir /projects /projects/ 308
    handle_path /projects/* {
        root * /var/www/whm-projects/current
        header {
            Content-Security-Policy "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            Permissions-Policy "camera=(), microphone=(), geolocation=()"
            X-Frame-Options "DENY"
            Cache-Control "no-cache"
        }
        file_server
    }
    # END WHM PROJECT HUB
'''

def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=40)

def check_public(expected_count):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    checks = ['https://whm12.art/projects/', 'https://whm12.art/projects/server-network-assist/',
              'https://whm12.art/projects/hub.css', 'https://whm12.art/projects/manifest.json',
              'https://whm12.art/network-resilience/']
    for url in checks:
        with opener.open(url, timeout=20) as response:
            data = response.read()
            if response.status != 200:
                raise RuntimeError('Public check failed: ' + url)
            if url.endswith('/manifest.json') and json.loads(data)['projects'] != expected_count:
                raise RuntimeError('Published manifest does not match the release')

def deploy(source, expected_sha, dry_run):
    if os.geteuid() != 0 or socket.gethostname() != 'VM-0-12-ubuntu':
        raise RuntimeError('Run with sudo only on the verified cloud host')
    source = Path(source).resolve(strict=True)
    if not str(source).startswith('/home/ubuntu/') or not (source/'index.html').is_file():
        raise ValueError('Source must be the uploaded static tree under /home/ubuntu')
    manifest = json.loads((source/'manifest.json').read_text())
    if manifest['projects'] != len(manifest['paths']) or manifest['projects'] < 1:
        raise ValueError('Invalid build manifest')
    for relative in manifest['paths']:
        target = (source/relative/'index.html').resolve()
        if source not in target.parents or not target.is_file():
            raise ValueError('Missing project page: ' + relative)
    for path in source.rglob('*'):
        if path.is_symlink() or (path.is_file() and path.name != 'LICENSE' and path.suffix not in ('.html','.css','.js','.svg','.png','.xml','.json')):
            raise ValueError('Unexpected public artifact: ' + str(path))
    original = CADDYFILE.read_bytes()
    if hashlib.sha256(original).hexdigest() != expected_sha:
        raise RuntimeError('Caddy configuration changed since inspection; inspect again before deployment')
    text = original.decode()
    if not text.startswith('whm12.art {'):
        raise ValueError('Unexpected domain configuration')
    if BEGIN in text:
        start = text.index(BEGIN); finish = text.index(END,start)+len(END)
        updated = text[:start] + BLOCK.strip() + text[finish:]
    else:
        if '/projects' in text or '\n\treverse_proxy ' not in text:
            raise ValueError('Projects route already used or expected upstream missing')
        updated = text.replace('\n\treverse_proxy ', BLOCK+'\n\treverse_proxy ',1)
    stamp = time.strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
    candidate = Path('/tmp/Caddyfile.projects-'+stamp)
    candidate.write_text(updated)
    try:
        run(['/usr/bin/caddy','validate','--config',str(candidate),'--adapter','caddyfile'])
        if dry_run:
            print(json.dumps({'validated':True,'projects':manifest['projects'],'config_sha256':expected_sha}))
            return
        backup = Path('/var/backups/whm-projects')/stamp
        backup.mkdir(parents=True,mode=0o700)
        shutil.copy2(CADDYFILE, backup/'Caddyfile')
        releases = BASE/'releases'; releases.mkdir(parents=True,exist_ok=True)
        current = BASE/'current'
        if current.exists() and not current.is_symlink():
            raise RuntimeError('Current destination is not an owned release symlink')
        previous = os.readlink(current) if current.is_symlink() else None
        (backup/'previous.json').write_text(json.dumps({'previous':previous}))
        release = releases/stamp
        shutil.copytree(source,release)
        for path in [BASE,releases,release,*release.rglob('*')]:
            os.chown(path,0,33)
            path.chmod(0o755 if path.is_dir() else 0o644)
        candidate_link = BASE/('current-'+stamp)
        candidate_link.symlink_to(release)
        try:
            os.replace(candidate_link,current)
            shutil.copyfile(candidate,CADDYFILE)
            run(['systemctl','reload','caddy.service'])
            check_public(manifest['projects'])
        except Exception:
            CADDYFILE.write_bytes(original)
            if previous:
                candidate_link.unlink(missing_ok=True)
                candidate_link.symlink_to(previous)
                os.replace(candidate_link,current)
            else:
                current.unlink(missing_ok=True)
            run(['systemctl','reload','caddy.service'])
            raise
        print(json.dumps({'deployed':True,'url':'https://whm12.art/projects/','projects':manifest['projects'],
                          'release':str(release),'backup':str(backup)}))
    finally:
        candidate.unlink(missing_ok=True)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--expected-config-sha256',required=True)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    deploy(args.source,args.expected_config_sha256,args.dry_run)
