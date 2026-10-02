"""Launch the explicitly requested D321 CLI in its interactive user session."""
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess


def optional_json(path, value):
    try:
        path.write_text(json.dumps(value), encoding='utf-8')
    except OSError:
        pass


def main():
    if socket.gethostname().upper() != 'DESKTOP-TD6B9GN':
        raise RuntimeError('D321 only')
    root = Path('C:/Users/86133/sna-d321-wired-gui-20260917')
    artifacts = root / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    task = (root / 'TASK.md').read_text(encoding='utf-8-sig')
    env = dict(os.environ)
    env.update(HTTP_PROXY='http://127.0.0.1:7897', HTTPS_PROXY='http://127.0.0.1:7897',
               NO_PROXY='localhost,127.0.0.1,10.20.32.13,10.203.49.1')
    argv = ['C:/Users/86133/.codex/tools/node-v24.16.0-win-x64/codex.exe',
            'exec', '-', '--sandbox', 'danger-full-access', '--skip-git-repo-check',
            '--cd', str(root), '--json', '--output-last-message', str(artifacts / 'codex-final.md')]
    handles = []
    for name in ['codex-events.jsonl', 'codex-stderr.log']:
        try:
            handles.append((artifacts / name).open('wb'))
        except OSError:
            handles.append(open(os.devnull, 'wb'))
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=handles[0], stderr=handles[1],
                                cwd=root, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        optional_json(artifacts / 'codex-process.json', {'launcher': os.getpid(), 'codex': proc.pid,
                      'started': datetime.now().astimezone().isoformat()})
        proc.communicate(task.encode('utf-8'))
        optional_json(artifacts / 'codex-exit.json', {'exit_code': proc.returncode,
                      'ended': datetime.now().astimezone().isoformat()})
    finally:
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
