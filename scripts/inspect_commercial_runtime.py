"""Read-only service runtime inspection with secret command arguments omitted."""
import argparse
import json
from pathlib import Path
import re
import socket
import subprocess

parser=argparse.ArgumentParser();parser.add_argument('--scope',choices=('source','cloud'),required=True);args=parser.parse_args()
source=args.scope=='source'
expected='a-MS-7E06' if source else 'VM-0-12-ubuntu'
if socket.gethostname()!=expected:raise RuntimeError('Unexpected host')
unit='sna-commercial' if source else 'server-network-assist.service'
command=['systemctl']+(['--user'] if source else [])+['show',unit,'-p','ExecStart','-p','MainPID']
result=subprocess.run(command,text=True,capture_output=True,check=True).stdout
match=re.search(r'path=([^ ;]+)',result)
if not match:raise RuntimeError('Cannot select interpreter from unit')
entry=Path(match.group(1));python=str(entry)
if entry.name not in ('python','python3','python3.12','python3.13'):
    first=entry.read_text().splitlines()[0]
    if not first.startswith('#!/'):raise RuntimeError('Unknown service entry')
    python=first[2:]
package=subprocess.run([python,'-c','import server_network_assist; print(server_network_assist.__file__)'],text=True,capture_output=True,check=True).stdout.strip()
print(json.dumps({'host':expected,'python':python,'package':str(Path(package).parent),'unit':unit,
                  'main_pid':int(re.search(r'MainPID=(\d+)',result).group(1))}))
