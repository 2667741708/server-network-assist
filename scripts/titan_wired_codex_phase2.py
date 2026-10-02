"""Resume the requested GUI task with a temporary management-only model channel."""
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess


def main():
    if socket.gethostname().upper() != 'DESKTOP-TD6B9GN':
        raise RuntimeError('D321 only')
    root = Path('C:/Users/86133/sna-d321-wired-gui-20260917')
    artifacts = root / 'artifacts'
    env = dict(os.environ)
    env.update(HTTP_PROXY='http://10.203.49.1:19097', HTTPS_PROXY='http://10.203.49.1:19097',
               NO_PROXY='localhost,127.0.0.1,10.20.32.13,10.203.49.1')
    argv = ['C:/Users/86133/.codex/tools/node-v24.16.0-win-x64/codex.exe',
            'exec', '--sandbox', 'danger-full-access', '--cd', str(root),
            'resume', '01a0ad9e-92e4-7432-9cea-23d82d727258', '-', '--skip-git-repo-check',
            '--json', '--output-last-message', str(artifacts / 'codex-final-phase2.md')]
    handles = []
    for name in ['codex-events-phase2.jsonl', 'codex-stderr-phase2.log']:
        try:
            handles.append((artifacts / name).open('wb'))
        except OSError:
            handles.append(open(os.devnull, 'wb'))
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=handles[0], stderr=handles[1],
                                cwd=root, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            (artifacts / 'codex-process-phase2.json').write_text(json.dumps({'launcher': os.getpid(),
                'codex': proc.pid, 'started': datetime.now().astimezone().isoformat()}), encoding='utf-8')
        except OSError:
            pass
        proc.communicate((root / 'TASK-PHASE2.md').read_bytes())
        try:
            (artifacts / 'codex-exit-phase2.json').write_text(json.dumps({'exit_code': proc.returncode}), encoding='utf-8')
        except OSError:
            pass
    finally:
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
