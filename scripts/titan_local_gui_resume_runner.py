"""Resume the authorized d321 Codex actor; preserve the first-stage evidence."""
import json
import os
from pathlib import Path
import socket
import subprocess
from datetime import datetime

if socket.gethostname().upper() != 'DESKTOP-TD6B9GN':
    raise RuntimeError('Titan only')
root = Path(r'C:\Users\86133\sna-local-gui-goal-20260917')
artifacts = root / 'artifacts'
permission = json.loads((artifacts / 'PERMISSION.json').read_text(encoding='utf-8-sig'))
if permission.get('campus_logout_allowed') is not True:
    raise RuntimeError('Explicit user authorization required')
previous = json.loads((artifacts / 'codex-exit.json').read_text(encoding='utf-8-sig'))
if previous.get('exit_code') != 0:
    raise RuntimeError('Inspect previous actor failure before resume')
(artifacts / 'codex-exit-phase1.json').write_text(json.dumps(previous), encoding='utf-8')
(artifacts / 'codex-exit.json').unlink()
env = dict(os.environ)
for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
    env.pop(key, None)
env['NO_PROXY'] = 'localhost,127.0.0.1,10.20.0.0/16,10.126.0.0/17'
argv = [r'C:\Users\86133\.codex\tools\node-v24.16.0-win-x64\codex.exe',
        'exec', '--sandbox', 'danger-full-access', '--cd', str(root),
        'resume', '01a0ab56-bdf5-7f31-9a82-303917929804', '-',
        '--skip-git-repo-check', '--json', '--output-last-message', str(artifacts / 'codex-final-phase2.md')]
with (artifacts / 'codex-events.jsonl').open('ab') as out, (artifacts / 'codex-stderr.log').open('ab') as err:
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err,
                            env=env, cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)
    (artifacts / 'codex-process.json').write_text(json.dumps({'launcher': os.getpid(), 'codex': proc.pid,
        'started': datetime.now().astimezone().isoformat(), 'phase': 2,
        'proxy': 'source network direct HTTPS'}), encoding='utf-8')
    proc.communicate((root / 'TASK-PHASE2.md').read_text(encoding='utf-8-sig').encode('utf-8'))
    (artifacts / 'codex-exit.json').write_text(json.dumps({'exit_code': proc.returncode,
        'ended': datetime.now().astimezone().isoformat(), 'phase': 2}), encoding='utf-8')
