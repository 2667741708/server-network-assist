"""Egress contract, isolation, cleanup and backwards compatibility regressions."""
import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from server_network_assist.client_egress import validate_egress, route_table, route_commands
from server_network_assist.client_relay import RelayManager, firewall_script, validate_policy
from server_network_assist.client_relay_agent import sync
from server_network_assist.client_store import ClientStore, ClientStoreError
from server_network_assist.client_service_api import ClientServiceAPI
from server_network_assist.client_service_admin import build_parser


def policy(**changes):
    result = dict(peer_id='physical-1', public_key='A'*43+'=', address='10.213.40.7/32',
                  interface='sna-commercial', customer_subnet='10.213.40.0/24',
                  management_subnets=['10.20.0.0/16'], egress_interface='eth0',
                  egress_mode='physical', egress_gateway='10.20.32.1',
                  download_bps=None, upload_bps=None, quota_bytes=None,
                  expires_at=2000000000, enabled=True)
    return result | changes


class Kernel:
    def __init__(self):
        self.calls, self.rules, self.routes = [], [], []

    def __call__(self, command, *, input_text=None, check=True):
        self.calls.append((command, input_text))
        output = ''
        if command[:4] == ['ip', '-j', '-d', 'link']:
            output = json.dumps([{'ifname':'eth0','linkinfo':{'info_kind':'veth'}}])
        elif command[:5] == ['ip', '-4', '-j', 'addr', 'show']:
            output = json.dumps([{'addr_info':[{'family':'inet','local':'10.20.32.13','prefixlen':22}]}])
        elif command[:5] == ['ip', '-4', '-j', 'rule', 'show']:
            output = json.dumps(self.rules)
        elif command[:5] == ['ip', '-4', '-j', 'route', 'show']:
            output = json.dumps(self.routes)
        elif command[:4] == ['ip', '-4', 'rule', 'add']:
            self.rules.append({'priority':100,'src':command[command.index('from')+1],
                               'iif':command[command.index('iif')+1], 'table':command[-1]})
        elif command[:4] == ['ip', '-4', 'rule', 'del']:
            self.rules = [row for row in self.rules if str(row['table']) != command[-1]]
        return subprocess.CompletedProcess(command, 0, output, '')


class EgressTests(unittest.TestCase):
    def test_dns_rejects_private_fake_ipv6_and_command_payload(self):
        for dns in ('198.18.0.1','10.20.32.1','::1','1.1.1.1;id',''):
            if not dns:
                self.assertEqual(validate_egress('physical','10.20.32.1',dns)['dns'], '223.5.5.5,1.1.1.1')
            else:
                with self.assertRaises(ValueError):
                    validate_egress('physical','10.20.32.1',dns)

    def test_bad_mode_gateway_and_recursive_interface_rejected(self):
        for values in ({'egress_mode':'execute'}, {'egress_gateway':''},
                       {'egress_gateway':'::1'}, {'egress_interface':'sna-commercial'}):
            with self.assertRaises(ValueError):
                validate_policy(policy(**values))

    def test_physical_rule_is_scoped_and_main_table_never_changed(self):
        commands = route_commands(policy())
        self.assertIn('unreachable', commands[0])
        self.assertEqual(commands[-1][0:4], ['ip','-4','rule','add'])
        self.assertIn('sna-commercial', commands[-1])
        self.assertIn('10.213.40.7/32', commands[-1])
        self.assertFalse(any('main' in row or 'flush' in row for row in commands))
        self.assertEqual(route_commands(policy(egress_mode='source_proxy',egress_gateway='')), [])

    def test_mixed_firewall_includes_both_exits_and_blocks_ipv6_fake_ip(self):
        a=policy(); b=policy(peer_id='proxy-2',address='10.213.40.8/32',egress_mode='source_proxy',egress_gateway='',egress_interface='Meta')
        value=firewall_script(a,['10.213.40.7','10.213.40.8'],policies=[a,b])
        self.assertIn('ip saddr 10.213.40.7 oifname != "eth0" drop',value)
        self.assertIn('ip saddr 10.213.40.8 oifname != "Meta" drop',value)
        self.assertIn('198.18.0.0/15 drop',value)
        self.assertIn('meta nfproto ipv6 drop',value)
        self.assertIn('ip saddr @customers oifname "eth0" masquerade',value)

    def test_apply_reconcile_idempotent_and_revoke_owned_policy_route(self):
        with tempfile.TemporaryDirectory() as root, patch('server_network_assist.client_relay.require_linux_root'):
            kernel=Kernel(); manager=RelayManager(Path(root),kernel,lambda:1900000000)
            manager.apply(policy())
            manager.reconcile([policy()])
            self.assertEqual(len(kernel.rules),1)
            before=len(kernel.calls)
            self.assertEqual(manager.revoke('physical-1')['status'],'revoked')
            self.assertEqual(kernel.rules,[])
            self.assertTrue(any(row[0][:4]==['ip','-4','rule','del'] for row in kernel.calls[before:]))
            self.assertFalse(any('flush' in row[0] for row in kernel.calls))

    def test_foreign_table_rejected_before_any_mutation_or_cleanup(self):
        with tempfile.TemporaryDirectory() as root, patch('server_network_assist.client_relay.require_linux_root'):
            kernel=Kernel();kernel.rules=[{'priority':99,'src':'all','table':route_table(policy())}]
            manager=RelayManager(Path(root),kernel)
            with self.assertRaisesRegex(RuntimeError,'another rule'):
                manager.apply(policy())
            self.assertFalse(any(any(word in row[0] for word in ('add','del','replace','remove','-f')) for row in kernel.calls))
            self.assertEqual(manager.status()['peers'],{})

    def test_missing_firewall_restored_before_peer_element_on_software_restart(self):
        with tempfile.TemporaryDirectory() as root, patch('server_network_assist.client_relay.require_linux_root'):
            kernel=Kernel();manager=RelayManager(Path(root),kernel,lambda:1900000000)
            manager.apply(policy());present=False
            def missing(command,**kwargs):
                nonlocal present
                if command[:4]==['nft','list','table','inet']:
                    return subprocess.CompletedProcess(command,0 if present else 1,'','No such file or directory' if not present else '')
                if command==['nft','-f','-']:present=True
                if command[:3]==['nft','add','element'] and not present:
                    raise RuntimeError('Shared firewall missing')
                return kernel(command,**kwargs)
            manager.runner=missing;manager.reconcile([policy()]);self.assertTrue(present)

    def test_physical_to_proxy_cleans_old_rule_without_toggling_source_clash(self):
        with tempfile.TemporaryDirectory() as root, patch('server_network_assist.client_relay.require_linux_root'):
            kernel=Kernel();manager=RelayManager(Path(root),kernel,lambda:1900000000)
            manager.apply(policy())
            manager.apply(policy(egress_mode='source_proxy',egress_gateway='',egress_interface='Meta'))
            self.assertEqual(kernel.rules,[])
            self.assertFalse(any(row[0][0]=='systemctl' for row in kernel.calls))

    def test_retry_cleans_nested_historical_egress_and_preserves_measured_usage(self):
        with tempfile.TemporaryDirectory() as root, patch('server_network_assist.client_relay.require_linux_root'):
            kernel=Kernel();manager=RelayManager(Path(root),kernel,lambda:1900000000);manager.apply(policy())
            state=manager.store.load();state['peers']['physical-1'].update(used_bytes=300,accounted_received=100,accounted_sent=200,last_received=100,last_sent=200);manager.store.save(state)
            proxy=policy(egress_mode='source_proxy',egress_gateway='',egress_interface='Meta')
            with patch.object(manager,'_remove_egress',side_effect=RuntimeError('transient cleanup failure')):
                with self.assertRaises(RuntimeError):manager.apply(proxy)
            recovery=manager.status()['peers']['physical-1'];self.assertEqual(recovery['used_bytes'],300)
            self.assertEqual(recovery['accounted_received'],100);self.assertIsNone(recovery['last_received'])
            self.assertEqual(len(kernel.rules),1)
            active=manager.apply(proxy);self.assertEqual(kernel.rules,[])
            self.assertEqual(active['used_bytes'],300);self.assertEqual(active['accounted_sent'],200)

    def test_logging_failure_does_not_block_revoke(self):
        with tempfile.TemporaryDirectory() as root, patch('server_network_assist.client_relay.require_linux_root'):
            manager=RelayManager(Path(root),Kernel(),lambda:1900000000);manager.apply(policy())
            manager.store.journal_path.mkdir(exist_ok=True) if not manager.store.journal_path.exists() else manager.store.journal_path.unlink()
            manager.store.journal_path.mkdir(exist_ok=True)
            self.assertEqual(manager.revoke('physical-1')['status'],'revoked')

    def test_control_plane_failure_still_enforces_expiry(self):
        class Offline:
            def request(self,*args): raise RuntimeError('offline')
        with tempfile.TemporaryDirectory() as root, patch('server_network_assist.client_relay.require_linux_root'):
            manager=RelayManager(Path(root),Kernel(),lambda:1900000000);manager.apply(policy(expires_at=1899999999))
            with self.assertRaisesRegex(RuntimeError,'offline'):
                sync(manager,Offline(),Path(root)/'cursor')
            self.assertEqual(manager.status()['peers']['physical-1']['reason'],'expired')


class GrantEgressTests(unittest.TestCase):
    def test_generated_subscription_copies_selected_source_egress_and_public_dns(self):
        with tempfile.TemporaryDirectory() as root:
            store=ClientStore(Path(root)/'db')
            source=store.save_source(dict(name='physical',endpoint='10.20.32.13:51910',
                relay_public_key='A'*43+'=',address_pool='10.213.40.0/24',relay_interface='sna-commercial',
                egress_interface='eth0',egress_mode='physical',egress_gateway='10.20.32.1',dns='1.1.1.1'))
            result=store.generate_monthly_subscription('client',10,[source['id']])
            grant=store.list_grants(result['customer_id'])[0]
            self.assertEqual(json.loads(grant['egress_policy'])['egress_mode'],'physical')
            self.assertEqual(grant['dns'],'1.1.1.1')

    def test_migration_revokes_old_lease_new_dns_and_client_dto_hides_gateway(self):
        with tempfile.TemporaryDirectory() as root:
            store=ClientStore(Path(root)/'db');plan=store.create_plan('plan');customer=store.create_customer('test',plan['id'])
            token=store.create_enrollment_token(customer['id']);device=store.enroll_device(token,'sign','test',wireguard_public_key='A'*43+'=')
            grant=store.grant_line(customer['id'],'old','tunnel','10.20.32.13:51910',relay_interface='sna-commercial',
                                   egress_interface='Meta',allocated_address='10.213.40.7/32',dns='198.18.0.1')
            old=store.issue_lease(device['id'],grant['id'])
            store.set_grant_egress(grant['id'],egress_mode='physical',egress_interface='eth0',egress_gateway='10.20.32.1',dns='223.5.5.5,1.1.1.1')
            with self.assertRaises(ClientStoreError):store.validate_lease(old['token'])
            lease=store.issue_lease(device['id'],grant['id']);dto=ClientServiceAPI.lease_dto(lease)
            self.assertEqual(dto['egress_mode'],'physical');self.assertEqual(dto['dns'],'223.5.5.5,1.1.1.1')
            self.assertNotIn('egress_gateway',dto);self.assertNotIn('egress_policy',dto)
            self.assertNotIn('::/0',dto['allowed_ips'])
            self.assertEqual(json.loads(store.active_leases()[0]['egress_policy'])['egress_gateway'],'10.20.32.1')

    def test_legacy_schema_upgrade_retains_existing_grants(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'db';store=ClientStore(path);plan=store.create_plan('old');customer=store.create_customer('old',plan['id']);grant=store.grant_line(customer['id'],'old','t','relay:1')
            with closing(sqlite3.connect(path)) as db:
                db.execute('ALTER TABLE line_grants DROP COLUMN egress_policy');db.commit()
            upgraded=ClientStore(path)
            self.assertEqual(upgraded.line_grant(grant['id'])['egress_policy'],'{}')
            upgraded.grant_line(customer['id'],'new','t','relay:2')
            self.assertEqual(len(upgraded.list_grants()),2)

    def test_cli_migration_and_physical_ipv6_rejection(self):
        args=build_parser().parse_args(['grant','set-egress','grant1','--egress-mode','physical','--egress-interface','eth0','--egress-gateway','10.20.32.1','--dns','1.1.1.1'])
        self.assertEqual(args.egress_mode,'physical')
        with tempfile.TemporaryDirectory() as root:
            store=ClientStore(Path(root)/'db');plan=store.create_plan('old');customer=store.create_customer('old',plan['id'])
            with self.assertRaisesRegex(ClientStoreError,'IPv4'):
                store.grant_line(customer['id'],'bad','t','relay:1',egress_mode='physical',egress_interface='eth0',egress_gateway='10.20.32.1')


if __name__=='__main__':unittest.main()
