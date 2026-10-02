"""Generate a dedicated key locally; never copy or print its private contents."""
import argparse
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--key', required=True)
parser.add_argument('--comment', required=True)
args = parser.parse_args()
key = Path(args.key)
key.parent.mkdir(parents=True, exist_ok=True)
if not key.exists():
    subprocess.run([r'C:\Windows\System32\OpenSSH\ssh-keygen.exe', '-t', 'ed25519',
                    '-f', str(key), '-N', '', '-C', args.comment], check=True)
if not key.with_suffix(key.suffix + '.pub').is_file():
    raise RuntimeError('Public key is missing; existing private key is left untouched')
subprocess.run(['icacls', str(key), '/inheritance:r', '/grant:r',
                '*S-1-5-18:F', '*S-1-5-32-544:F'], check=True, capture_output=True)
subprocess.run(['icacls', str(key), '/grant',
                subprocess.check_output(['whoami'], text=True).strip() + ':F'], check=True, capture_output=True)
print('Dedicated key ready; private material remains on this machine.')
