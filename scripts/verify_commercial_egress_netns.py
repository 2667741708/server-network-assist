#!/usr/bin/env python3
"""Real WG/nft/NAT/policy-route acceptance inside disposable Linux namespaces.

Does not alter host routes, DNS, proxy, sysctl, services or existing interfaces.
Uses a local HTTP target to avoid depending on an airport or public Internet.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import uuid


def run(argv, *, input_text=None, check=True):
    return subprocess.run(argv, input=input_text, text=True, capture_output=True, check=check, timeout=15)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-tree',type=Path)
    args=parser.parse_args()
    if args.source_tree:
        sys.path.insert(0,str(args.source_tree))
    from server_network_assist.client_relay import RelayManager
    from server_network_assist.client_egress import route_commands, route_table
    if platform.system()!='Linux' or os.geteuid()!=0:
        raise RuntimeError('Run only as Linux root on an authorized test server')
    prefix='sna-eg-'+uuid.uuid4().hex[:8]
    client,source,wan=[prefix+'-'+n for n in ('c','s','w')]
    owned=[];servers=[];checks={}
    def ns(name,argv,**kwargs):return run(['ip','netns','exec',name,*argv],**kwargs)
    def pair(a,an,b,bn):
        run(['ip','link','add',an,'netns',a,'type','veth','peer','name',bn,'netns',b])
        ns(a,['ip','link','set',an,'up']);ns(b,['ip','link','set',bn,'up'])
    def curl():
        return ns(client,['curl','-q','--noproxy','*','--connect-timeout','2','--max-time','3','-sS','-o','/dev/null','-w','%{http_code}','http://93.184.216.34:8080/'],check=False)
    try:
        for name in (client,source,wan):
            run(['ip','netns','add',name]);owned.append(name);ns(name,['ip','link','set','lo','up'])
        pair(client,'underlay',source,'underlay')
        pair(source,'eth0',wan,'gateway')
        for name,dev,cidr in ((client,'underlay','10.252.0.2/30'),(source,'underlay','10.252.0.1/30'),
                              (source,'eth0','192.0.2.2/24'),(wan,'gateway','192.0.2.1/24'),
                              (wan,'lo','93.184.216.34/32')):
            ns(name,['ip','addr','add',cidr,'dev',dev])
        ns(source,['sysctl','-w','net.ipv4.ip_forward=1'])
        ns(source,['ip','route','add','default','via','192.0.2.1','dev','eth0'])
        keys=[]
        for _ in range(3):
            private=run(['wg','genkey']).stdout.strip()
            public=run(['wg','pubkey'],input_text=private+'\n').stdout.strip();keys.append((private,public))
        for name,dev,address,private,port in ((source,'sna-commercial','10.213.40.1/24',keys[0][0],51910),
                                               (client,'customer','10.213.40.7/32',keys[1][0],51911)):
            ns(name,['ip','link','add',dev,'type','wireguard'])
            ns(name,['wg','setconf',dev,'/dev/stdin'],input_text=f'[Interface]\nPrivateKey = {private}\nListenPort = {port}\n')
            ns(name,['ip','addr','add',address,'dev',dev]);ns(name,['ip','link','set',dev,'up'])
        ns(client,['wg','set','customer','peer',keys[0][1],'endpoint','10.252.0.1:51910',
                   'allowed-ips','0.0.0.0/0','persistent-keepalive','1'])
        ns(client,['ip','route','add','default','dev','customer'])
        # Synthetic TUN captures forwarding at the same priority as source Mihomo.
        ns(source,['ip','link','add','Meta','type','dummy']);ns(source,['ip','link','set','Meta','up'])
        ns(source,['ip','route','add','default','dev','Meta','table','2022'])
        ns(source,['ip','rule','add','priority','9002','iif','sna-commercial','lookup','2022'])
        baseline_rules=json.loads(ns(source,['ip','-4','-j','rule','show']).stdout)
        baseline_main=json.loads(ns(source,['ip','-4','-j','route','show','table','main']).stdout)
        server=subprocess.Popen(['ip','netns','exec',wan,sys.executable,'-m','http.server','8080','--bind','0.0.0.0'],
                                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        servers.append(server);time.sleep(.3)
        with tempfile.TemporaryDirectory(prefix='sna-egress-state-') as data:
            manager=RelayManager(Path(data),lambda argv,**kw:ns(source,argv,**kw))
            physical=dict(peer_id='ns-physical',public_key=keys[1][1],address='10.213.40.7/32',
                          interface='sna-commercial',customer_subnet='10.213.40.0/24',management_subnets=['10.20.0.0/16'],
                          egress_interface='eth0',egress_mode='physical',egress_gateway='192.0.2.1',
                          download_bps=20000000,upload_bps=5000000,quota_bytes=None,expires_at=int(time.time())+600,enabled=True)
            proxy=physical|dict(peer_id='ns-proxy',public_key=keys[2][1],address='10.213.40.8/32',
                                egress_interface='Meta',egress_mode='source_proxy',egress_gateway='')
            manager.apply(physical);manager.apply(proxy)
            for _ in range(4):
                response=curl()
                if response.returncode==0 and response.stdout=='200':break
                time.sleep(.3)
            checks['physical_bypasses_running_tun']=response.returncode==0 and response.stdout=='200'
            manager.measure('sna-commercial')
            checks['real_wg_meter']=manager.status()['peers']['ns-physical']['used_bytes']>0
            ns(source,['nft','delete','table','inet','sna_relay'])
            for command in route_commands(physical,remove=True):ns(source,command)
            manager.reconcile([physical,proxy]);manager.reconcile([physical,proxy])
            rules=json.loads(ns(source,['ip','-4','-j','rule','show']).stdout)
            checks['restart_restore_without_duplicate_rules']=sum(str(row.get('table'))==route_table(physical) for row in rules)==1 and curl().stdout=='200'
            ns(source,['ip','link','del','Meta'])
            checks['physical_with_source_tun_absent']=curl().stdout=='200'
            ns(source,['ip','link','set','eth0','down'])
            checks['physical_link_loss_fails_closed']=curl().returncode!=0
            ns(source,['ip','link','set','eth0','up'])
            # Linux removes this fixture-owned static default on administrative
            # down. Model DHCP/reconnect installing it again; relay must never
            # repair user main defaults itself.
            ns(source,['ip','route','replace','default','via','192.0.2.1','dev','eth0'])
            manager.reconcile([physical,proxy])
            checks['physical_link_recovery']=curl().stdout=='200'
            limited=physical|dict(quota_bytes=1)
            manager.apply(limited);manager.measure('sna-commercial');manager.reconcile()
            checks['quota_revokes_real_peer']=manager.status()['peers']['ns-physical']['status']=='revoked' and keys[1][1] not in ns(source,['wg','show','sna-commercial','peers']).stdout
            checks['quota_removes_source_rule']=not any(str(row.get('table'))==route_table(physical) for row in json.loads(ns(source,['ip','-4','-j','rule','show']).stdout))
            checks['revoked_customer_cannot_forward']=curl().returncode!=0
            # Remove test proxy lease, restore the synthetic Meta baseline only.
            manager.revoke('ns-proxy');ns(source,['ip','link','add','Meta','type','dummy']);ns(source,['ip','link','set','Meta','up'])
            # Link cycling can reorder equivalent kernel route rows.
            canonical=lambda rows:sorted(json.dumps(row,sort_keys=True) for row in rows)
            after_main=json.loads(ns(source,['ip','-4','-j','route','show','table','main']).stdout)
            checks['original_main_routes_preserved']=canonical(after_main)==canonical(baseline_main)
            if not checks['original_main_routes_preserved']:
                print(json.dumps({'main_before':baseline_main,'main_after':after_main},sort_keys=True),file=sys.stderr)
            checks['original_policy_rules_preserved']=json.loads(ns(source,['ip','-4','-j','rule','show']).stdout)==baseline_rules
            routes=ns(source,['ip','-4','-j','route','show','table',route_table(physical)],check=False)
            checks['owned_egress_table_empty']=not json.loads(routes.stdout or '[]')
    finally:
        for server in servers:
            server.terminate()
            try:server.wait(timeout=3)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=3)
        for name in reversed(owned):run(['ip','netns','del',name],check=False)
        checks['namespace_cleanup']=all(name not in run(['ip','netns','list']).stdout for name in owned)
    print(json.dumps({'ok':bool(checks) and all(checks.values()),'checks':checks},sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__=='__main__':raise SystemExit(main())
