#!/usr/bin/env python3
"""Guarded, temporary D321 physical-egress experiment on authorized C201-4090.

No installation or customer database writes. Always restore the original relay
state/firewall and source service states, including on an independent timer.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

DATA=Path('/var/lib/server-network-assist-relay')
CONFIG=Path('/etc/mihomo/config.yaml')
UNITS=('mihomo.service','gateway-health.timer','server-network-assist-relay.timer')


def run(argv,*,check=True,input_text=None):
    return subprocess.run(argv,input=input_text,text=True,capture_output=True,check=check,timeout=15)


def active(unit):return run(['systemctl','is-active',unit],check=False).returncode==0


def save(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value),encoding='utf-8')
    temporary.chmod(0o600);temporary.replace(path)


def probe():
    result={}
    for name,address in (('D321','10.20.31.134'),('5080','10.20.32.12'),('D408','10.20.32.14')):
        try:
            with socket.create_connection((address,22),timeout=3) as connection:
                connection.settimeout(2);result[name]=connection.recv(80).decode('ascii',errors='replace').strip().startswith('SSH-')
        except OSError:result[name]=False
    return result


def sample(manager,before):
    peers=manager.status()['peers'];record=peers.get(before['peer_id'],{})
    return dict(at=int(time.time()),source_core_active=active('mihomo.service'),
                source_tun_present=run(['ip','link','show','Meta'],check=False).returncode==0,
                guardian_timer_active=active('gateway-health.timer'),
                relay_timer_active=active('server-network-assist-relay.timer'),
                config_unchanged=hashlib.sha256(CONFIG.read_bytes()).hexdigest()==before['config_hash'],
                physical_policy_active=record.get('status')=='active' and record.get('policy',{}).get('egress_mode')=='physical',
                campus_ssh=probe())


def restore(manager,directory,before):
    from server_network_assist.client_relay import validate_policy
    failures=[]
    # Restore source service first, so old proxy customers regain their exit.
    if before['units']['mihomo.service']:
        try:run(['systemctl','start','mihomo.service'])
        except Exception:failures.append('source core start')
    policy=before['physical_policy']
    try:manager._remove_egress(validate_policy(policy))
    except Exception:failures.append('owned physical routes')
    try:
        prefix='delete table inet sna_relay\n' if run(['nft','list','table','inet','sna_relay'],check=False).returncode==0 else ''
        run(['nft','-f','-'],input_text=prefix+(directory/'before.nft').read_text())
    except Exception:failures.append('relay firewall restore')
    try:
        original=json.loads((directory/'state-before.json').read_text())
        manager.store.save(original)
    except Exception:failures.append('relay state restore')
    # Do not resume the old agent until routing ownership has been cleaned up.
    if not failures:
        for unit in ('gateway-health.timer','server-network-assist-relay.timer'):
            if before['units'][unit]:
                try:run(['systemctl','start',unit])
                except Exception:failures.append(unit+' resume')
    result=sample(manager,before)|{'restored':not failures,'failures':failures}
    save(directory/'restore-result.json',result)
    if failures:raise RuntimeError('restore incomplete: '+','.join(failures))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action',choices=('prepare','apply','sample','restore'),required=True)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--source-tree',type=Path,required=True)
    args=parser.parse_args();sys.path.insert(0,str(args.source_tree))
    from server_network_assist.client_relay import RelayManager
    if os.geteuid()!=0 or socket.gethostname()!='a-MS-7E06':raise RuntimeError('Authorized C201-4090 root only')
    directory=args.directory.resolve()
    if directory.parent!=Path('/var/backups') or not directory.name.startswith('sna-egress-test-'):
        raise ValueError('Backup directory must be /var/backups/sna-egress-test-*')
    manager=RelayManager(DATA)
    if args.action=='prepare':
        if directory.exists():raise ValueError('Use a new backup directory')
        units={unit:active(unit) for unit in UNITS}
        if not all(units.values()):raise RuntimeError('Expected source core and both timers active before test')
        baseline=manager.store.load()
        records=[record for record in baseline['peers'].values() if record.get('status')=='active' and record['policy']['address']=='10.213.40.7/32']
        if len(records)!=1:raise RuntimeError('Expected exactly one active D321 lease')
        policy=records[0]['policy']
        if policy['interface']!='sna-commercial' or (policy.get('expires_at') or 0)<time.time()+180:
            raise RuntimeError('Need a current D321 lease with more than 180 seconds remaining')
        physical=policy|dict(egress_mode='physical',egress_interface='enp4s0',egress_gateway='10.20.32.1')
        manager._ensure_egress(physical,write=False)
        baseline_campus=probe()
        if not all(baseline_campus.values()):raise RuntimeError('Baseline campus SSH must all be reachable')
        directory.mkdir(mode=0o700)
        before=dict(units=units,peer_id=policy['peer_id'],physical_policy=physical,
                    config_hash=hashlib.sha256(CONFIG.read_bytes()).hexdigest(),campus_ssh=baseline_campus,
                    rescue='sna-egress-rescue-'+directory.name.removeprefix('sna-egress-test-'))
        save(directory/'before.json',before);save(directory/'state-before.json',baseline)
        (directory/'before.nft').write_text(run(['nft','list','table','inet','sna_relay']).stdout)
        (directory/'before.nft').chmod(0o600)
        run(['systemd-run','--unit',before['rescue'],'--on-active=120s','--property=Type=oneshot',
             sys.executable,str(Path(__file__).resolve()),'--action','restore','--directory',str(directory),
             '--source-tree',str(args.source_tree)])
        print(json.dumps({'ready':True,'campus_ssh':baseline_campus,'rescue':before['rescue']}));return
    before=json.loads((directory/'before.json').read_text())
    if args.action=='apply':
        try:
            run(['systemctl','stop','server-network-assist-relay.timer'])
            run(['systemctl','stop','server-network-assist-relay.service'])
            # Capture the final accounted state after the one-shot finishes.
            save(directory/'state-before.json',manager.store.load())
            manager.apply(before['physical_policy'])
            run(['systemctl','stop','gateway-health.timer'])
            run(['systemctl','stop','gateway-health.service'])
            run(['systemctl','stop','mihomo.service'])
            result=sample(manager,before);save(directory/'apply-result.json',result)
            if result['source_core_active'] or result['source_tun_present'] or not result['physical_policy_active']:
                raise RuntimeError('Controlled source stop was not established')
        except Exception:
            restore(manager,directory,before);raise
    elif args.action=='restore':
        result=restore(manager,directory,before)
        run(['systemctl','stop',before['rescue']+'.timer'],check=False)
    else:
        result=sample(manager,before);save(directory/'sample-result.json',result)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
