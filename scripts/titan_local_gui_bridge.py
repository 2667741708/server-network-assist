"""SSH native GUI controls; never register or connect through client APIs."""
import argparse
import base64
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['stage', 'launch', 'inspect', 'click', 'enroll', 'scroll', 'verify'])
parser.add_argument('--x', type=int, default=0)
parser.add_argument('--y', type=int, default=0)
parser.add_argument('--delta', type=int, default=-600)
args = parser.parse_args()
root = Path(r'C:\Users\86133\sna-local-gui-goal-20260917')
commands = [r'C:\Windows\System32\OpenSSH\ssh.exe', '-F', r'C:\Users\86133\.ssh\sna-peer.config',
            '-o', 'BatchMode=yes', 'whm-save', 'powershell.exe', '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'Bypass', '-File']
payload = None
if args.action == 'stage':
    commands = [r'C:\Windows\System32\OpenSSH\scp.exe', '-F', r'C:\Users\86133\.ssh\sna-peer.config',
                '-o', 'BatchMode=yes', str(root / 'enrollment.txt'),
                'whm-save:C:/Users/hmw20/.ssh/gui-d321-control-20260917/enrollment.txt']
else:
    commands += [r'C:\Users\hmw20\.ssh\gui-d321-control-20260917\invoke.ps1', '-Action', args.action,
                 '-X', str(args.x), '-Y', str(args.y), '-Delta', str(args.delta)]
result = subprocess.run(commands, input=payload, capture_output=True, timeout=30,
                        creationflags=subprocess.CREATE_NO_WINDOW)
if result.returncode:
    print(result.stderr.decode('utf-8', errors='replace')[:1000])
    raise SystemExit(result.returncode)
text = result.stdout.decode('utf-8-sig', errors='replace').strip()
if args.action == 'inspect':
    value = json.loads(text)
    picture = value.pop('png', None)
    if picture:
        target = root / 'artifacts' / 'whm-native-window.png'
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(base64.b64decode(picture, validate=True))
        value['image_path'] = str(target)
    print(json.dumps(value, ensure_ascii=False))
else:
    print(text)
