"""4090-only guarded repair for DNS-poisoned transparent TLS destinations.

No client/operator routes, DNS, subscription keys or proxy nodes are modified.
Back up the exact private config and arm independent systemd rollback first.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import yaml

ROOT=Path('/var/backups/sna-source-sniff-20260918')
CONFIG=Path('/etc/mihomo/config.yaml')
UNIT='sna-source-sniff-20260918-rollback'

def run(argv):
    result=subprocess.run(argv,capture_output=True,text=True,timeout=30)
    if result.returncode:
        raise RuntimeError('Action failed: '+argv[0]+' (private output suppressed)')
    return result.stdout

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('apply','restore','commit','baseline'))
    action=parser.parse_args().action
    if os.geteuid()!=0 or socket.gethostname()!='a-MS-7E06':
        raise RuntimeError('Only the authorized 4090 source, as root')
    saved=ROOT/'original.yaml';verified=ROOT/'verified'
    if action=='restore':
        if verified.exists(): return
        shutil.copy2(saved,CONFIG)
        info=json.loads((ROOT/'metadata.json').read_text())
        os.chown(CONFIG,info['uid'],info['gid'])
        CONFIG.chmod(info['mode'])
        run(['systemctl','restart','mihomo.service'])
        print(json.dumps({'restored':True}))
        return
    if action=='commit':
        run(['systemctl','is-active','mihomo.service'])
        if not saved.exists():raise RuntimeError('No pending repair')
        verified.write_text('Source TLS and campus verification completed\n')
        run(['systemctl','stop',UNIT+'.timer'])
        print(json.dumps({'committed':True}))
        return
    if action=='baseline':
        report={}
        for label,argv in [('rules',['ip','-4','rule','show']),('main',['ip','-4','route','show','table','main']),
                           ('relay_guard',['nft','list','table','inet','sna_relay'])]:
            report[label]=run(argv)
        report['campus_tcp']={}
        for host in ('10.20.31.134','10.20.32.12','10.20.32.14'):
            try:
                with socket.create_connection((host,22),timeout=3):pass
                report['campus_tcp'][host]=True
            except OSError:report['campus_tcp'][host]=False
        print(json.dumps(report),flush=True)
        return
    config=yaml.safe_load(CONFIG.read_text())
    if 'sniffer' in config:raise RuntimeError('Existing sniffer requires explicit merge review')
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True);ROOT.chmod(0o700)
    if saved.exists():raise RuntimeError('Existing repair backup: inspect before reapplying')
    info=CONFIG.stat()
    (ROOT/'metadata.json').write_text(json.dumps({'uid':info.st_uid,'gid':info.st_gid,'mode':info.st_mode&0o777}))
    shutil.copy2(CONFIG,saved);saved.chmod(0o600)
    addition='''\nsniffer:
  enable: true
  force-dns-mapping: true
  parse-pure-ip: true
  override-destination: true
  sniff:
    TLS:
      ports: [443, 8443]
    HTTP:
      ports: [80, 8080]
  skip-dst-address:
    - 10.0.0.0/8
    - 172.16.0.0/12
    - 192.168.0.0/16
'''
    candidate=ROOT/'candidate.yaml'
    candidate.write_bytes(CONFIG.read_bytes()+addition.encode('ascii'))
    candidate.chmod(0o600)
    run(['/usr/local/bin/mihomo','-t','-d','/var/lib/mihomo','-f',str(candidate)])
    run(['systemd-run','--unit='+UNIT,'--on-active=4m','/usr/bin/python3',str(Path(__file__).resolve()),'restore'])
    try:
        shutil.copyfile(candidate,CONFIG)
        run(['systemctl','restart','mihomo.service'])
        run(['systemctl','is-active','mihomo.service'])
    except Exception:
        shutil.copyfile(saved,CONFIG)
        run(['systemctl','restart','mihomo.service'])
        raise
    print(json.dumps({'applied':True,'rollback_seconds':240}),flush=True)

if __name__=='__main__':main()
