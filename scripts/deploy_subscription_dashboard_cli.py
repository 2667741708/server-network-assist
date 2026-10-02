"""Update only two root CLI modules; never restart the commercial relay."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
assert os.geteuid()==0
root=Path('/opt/server-network-assist-relay/venv/lib/python3.13/site-packages/server_network_assist')
names=['subscription_dashboard.py','client_service_admin.py'];backup=a.stage/('cli-backup-'+str(time.time_ns()));backup.mkdir(mode=0o700)
original={n:(root/n).read_bytes() if (root/n).exists() else None for n in names}
def atomic(path,body):
    tmp=path.with_name(path.name+'.dashboard-new');tmp.write_bytes(body);tmp.chmod(0o644);os.replace(tmp,path)
for n,b in original.items():
    if b is not None:(backup/n).write_bytes(b)
try:
    for n in names:
        body=(a.stage/n).read_bytes();compile(body,n,'exec');atomic(root/n,body)
    result=subprocess.run(['/opt/server-network-assist-relay/venv/bin/python','-m','server_network_assist.client_service_admin','subscription','show','--help'],check=True,capture_output=True,text=True)
    assert '--customer' in result.stdout
except BaseException:
    for n,b in original.items():
        if b is None:(root/n).unlink(missing_ok=True)
        else:atomic(root/n,b)
    raise
print(json.dumps({'root_cli_updated':True,'relay_restarted':False,'backup':str(backup)}))
