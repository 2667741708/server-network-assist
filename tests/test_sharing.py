"""Sharing contracts, rollback journals and a real local TCP relay (no host network mutations)."""
import asyncio
import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_network_assist import FakeState
from server_network_assist.network_assist import NetworkStore
from server_network_assist import network_assist_helper as helper
from server_network_assist import app


class SharingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = NetworkStore(FakeState(Path(self.tmp.name) / 'db'))

    def test_proxy_default_validation_and_active_immutability(self):
        p = self.store.save({'name':'test','gateway_id':'gateway','client_ids':['client']})
        self.assertEqual(p['proxy_mode'], 'direct')
        for update in ({'proxy_mode':'auto'}, {'proxy_host':'127.0.0.1;id'}, {'proxy_host':'0.0.0.0'}, {'proxy_port':65536}):
            with self.assertRaises(ValueError): self.store.save({**p, **update})
        p = self.store.save({**p, 'proxy_mode':'share'})
        gateway, clients = self.store.runtime_payloads(p, {'gateway':'A'*43+'=', 'client':'B'*43+'='})
        self.assertEqual(gateway['proxy_host'], '127.0.0.1')
        self.assertEqual(clients['client']['gateway_ip'], '10.213.1.1')
        self.assertEqual(clients['client']['relay_port'], 17897)
        self.store.set_state(p['id'], 'enabled')
        for update in ({'proxy_mode':'direct'}, {'proxy_port':7890}, {'preserve_routes':['192.0.2.0/24']}):
            with self.assertRaises(ValueError): self.store.save({**p, **update})

    def test_gateway_proxy_hooks_stay_in_interface_section(self):
        p = {'role':'gateway','profile_id':'test','interface':'na1234567890','address':'10.213.1.1/24',
             'subnet':'10.213.1.0/24','port':51919,'proxy_mode':'share',
             'peers':[{'allowed_ip':'10.213.1.2/32','public_key':'A'*43+'='}]}
        with patch.object(helper, 'private_key', return_value='key'), patch.object(helper, 'default_uplink', return_value='eth0'):
            interface, peer = helper.wg_config(p).split('[Peer]', 1)
        self.assertIn('-i %i -s 10.213.1.0/24 -p tcp --dport 17897', interface)
        self.assertNotIn('PostUp', peer)

    def test_pending_cleanup_cannot_discard_recovery_profile(self):
        p = self.store.save({'name':'test','gateway_id':'gateway','client_ids':['client']})
        p.update(state='error', cleanup_pending=True)
        self.store.put(p)
        with self.assertRaisesRegex(ValueError, '回退'): self.store.save({**p, 'proxy_mode':'share'})
        with self.assertRaisesRegex(ValueError, '回退'): self.store.delete(p['id'])

    def test_linux_proxy_cleanup_keeps_changed_file_for_retry(self):
        path = Path(self.tmp.name) / 'proxy.sh'
        path.write_text('changed by operator')
        state = {'profile_id':'test','proxy_files':{str(path):'original generated text'}}
        with patch.object(helper, 'save_state'):
            with self.assertRaisesRegex(ValueError, 'edited'): helper.client_proxy(state, False)
        self.assertIn(str(path), state['proxy_files'])
        self.assertEqual(path.read_text(), 'changed by operator')

    def test_linux_direct_mode_does_not_touch_proxy(self):
        with patch.object(helper, 'atomic') as write:
            helper.client_proxy({'proxy_mode':'direct'}, True)
        write.assert_not_called()

    def test_proxy_restore_error_does_not_skip_network_cleanup(self):
        state = {'profile_id':'test','interface':'na1234567890','role':'client',
                 'added_routes':['192.0.2.1/32'],'proxy_files':{'file':'data'}}
        commands = []
        def run(args, **kwargs):
            commands.append(args)
            return SimpleNamespace(stdout='[{"dst":"192.0.2.1/32"}]', returncode=0)
        with patch.object(helper,'load_state',return_value=state), patch.object(helper,'save_state'), patch.object(helper,'timer'), patch.object(helper,'run',side_effect=run), patch.object(helper,'client_proxy',side_effect=RuntimeError('offline user')):
            with self.assertRaisesRegex(RuntimeError, 'offline user'): helper.disable('test')
        self.assertIn(['ip','route','del','192.0.2.1/32'], commands)
        self.assertEqual(state['proxy_files'], {'file':'data'})
        self.assertFalse(state['desired'])

    def test_route_mutation_is_journaled_before_failure(self):
        state = {'profile_id':'test','preserve_routes':['192.0.2.1/32']}
        def run(args, **kwargs):
            if args[:3] == ['ip','route','replace']:
                self.assertEqual(state['added_routes'], ['192.0.2.1/32'])
                raise RuntimeError('route error')
            return SimpleNamespace(stdout='[]' if 'show' in args else '[{"dev":"eth0","gateway":"192.0.2.254"}]', returncode=0)
        with patch.object(helper,'run',side_effect=run), patch.object(helper,'save_state') as save:
            with self.assertRaisesRegex(RuntimeError,'route error'): helper.preserve_routes(state)
        save.assert_called_once()


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_bidirectional_relay_and_peer_rejection(self):
        calls = []
        async def echo(reader, writer):
            calls.append(True)
            try:
                data = await reader.read(1024)
                writer.write(b'echo:' + data); await writer.drain()
            finally: writer.close(); await writer.wait_closed()
        upstream = await asyncio.start_server(echo, '127.0.0.1', 0)
        state = {'proxy_host':'127.0.0.1','proxy_port':upstream.sockets[0].getsockname()[1],
                 'peers':[{'allowed_ip':'127.0.0.1/32'}]}
        relay = await asyncio.start_server(lambda r,w: helper.relay_connection(r,w,state), '127.0.0.1', 0)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', relay.sockets[0].getsockname()[1])
            writer.write(b'tunnel data'); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(1024), 3), b'echo:tunnel data')
            writer.close(); await writer.wait_closed()
            state['peers'] = [{'allowed_ip':'192.0.2.2/32'}]
            reader, writer = await asyncio.open_connection('127.0.0.1', relay.sockets[0].getsockname()[1])
            self.assertEqual(await asyncio.wait_for(reader.read(1024), 3), b'')
            writer.close(); await writer.wait_closed()
            self.assertEqual(len(calls), 1)
        finally:
            relay.close(); upstream.close()
            await relay.wait_closed(); await upstream.wait_closed()

    async def test_failed_enable_reply_still_triggers_client_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeState(Path(directory) / 'db')
            fake.network = NetworkStore(fake)
            fake.host = lambda ident: {'name':ident}
            fake.audit = lambda *args: None
            profile = fake.network.save({'name':'test','gateway_id':'gateway','client_ids':['client'],'proxy_mode':'share'})
            async def remote(state, host, action, *args, **kwargs):
                if action == 'prepare': return {'public_key':'A'*43+'='}
                if action == 'enable' and host == 'client': raise RuntimeError('lost enable reply')
                return {'ok':True}
            probe = {'ssh':True,'internet':False,'gateway_supported':True,'os':'Windows','helper':True,'name':'demo'}
            with patch.object(app,'network_probe_host',AsyncMock(return_value=probe)), patch.object(app,'_network_remote',AsyncMock(side_effect=remote)) as dispatch:
                with self.assertRaisesRegex(RuntimeError, 'lost enable reply'): await app.network_enable_profile(fake, profile['id'])
            calls = [(c.args[1],c.args[2]) for c in dispatch.await_args_list]
            self.assertIn(('client','disable'),calls)
            self.assertIn(('gateway','disable'),calls)
            self.assertEqual(fake.network.get(profile['id'])['state'],'error')
