"""Run the explicitly authorized Titan Codex task without shell stderr handling."""
import json
import os
from pathlib import Path
import socket
import subprocess
from datetime import datetime

if socket.gethostname().upper() != 'DESKTOP-TD6B9GN':
    raise RuntimeError('Titan only')
root = Path(r'C:\Users\86133\sna-wifi-goal-20260917')
artifacts = root / 'artifacts'
artifacts.mkdir(exist_ok=True)
env = dict(os.environ)
env.update(HTTP_PROXY='http://127.0.0.1:7897', HTTPS_PROXY='http://127.0.0.1:7897',
           NO_PROXY='localhost,127.0.0.1,10.20.0.0/16,10.203.0.0/16,10.201.0.0/16')
argv = [r'C:\Users\86133\.codex\tools\node-v24.16.0-win-x64\codex.exe',
        'exec', '-', '--sandbox', 'danger-full-access', '--skip-git-repo-check',
        '--cd', str(root), '--json', '--output-last-message', str(artifacts / 'codex-final.md')]
with (artifacts / 'codex-events.jsonl').open('wb') as out, (artifacts / 'codex-stderr.log').open('wb') as err:
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err,
                            env=env, cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)
    (artifacts / 'codex-process.json').write_text(json.dumps({'launcher': os.getpid(), 'codex': proc.pid,
        'started': datetime.now().astimezone().isoformat(), 'proxy': '127.0.0.1:7897'}), encoding='utf-8')
    proc.communicate((root / 'TASK.md').read_text(encoding='utf-8-sig').encode('utf-8'))
    (artifacts / 'codex-exit.json').write_text(json.dumps({'exit_code': proc.returncode,
        'ended': datetime.now().astimezone().isoformat()}), encoding='utf-8')
