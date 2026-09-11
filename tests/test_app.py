import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import asyncssh
from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet

from server_network_assist.app import STATE_KEY, create_app, initialize, ssh_route


class SSHServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == 'tester' and password == 'fixture-password'

    def connection_requested(self, dest_host, dest_port, orig_host, orig_port):
        return dest_host == '127.0.0.1'


async def shell(process):
    if process.command == 'hostname; whoami':
        process.stdout.write('fixture-host\ntester\n')
        process.exit(0)
        return
    process.stdout.write('fixture-shell ready\r\n$ ')
    try:
        async for data in process.stdin:
            if data.strip() == 'exit':
                process.exit(0)
                return
            process.stdout.write('received: ' + data)
    except (asyncssh.BreakReceived, asyncssh.TerminalSizeChanged):
        pass


class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data = root / 'data'
        initialize(self.data)
        self.secret = json.loads((self.data / 'initial-login.json').read_text())
        key = root / 'vault.key'
        key.write_bytes(Fernet.generate_key())
        with patch.dict(os.environ, {'PANEL_ORIGIN':''}):
            self.app = create_app(self.data, key)
        self.state = self.app[STATE_KEY]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.origin = str(self.client.make_url('/')).rstrip('/')
        self.hostkey = asyncssh.generate_private_key('ssh-ed25519')
        self.ssh = await asyncssh.create_server(SSHServer, '127.0.0.1', 0, server_host_keys=[self.hostkey], process_factory=shell)
        self.credential = self.state.save_credential({'name':'fixture', 'kind':'password', 'secret':'fixture-password'})
        self.host = self.state.save_host({'name':'Test target','address':'127.0.0.1','port':self.ssh.get_port(),
            'username':'tester','credential_id':self.credential,'host_key':self.hostkey.export_public_key().decode()})
        await self.login()

    async def asyncTearDown(self):
        await self.client.close()
        self.ssh.close()
        await self.ssh.wait_closed()
        self.tmp.cleanup()

    async def login(self, remember=0):
        response = await self.client.post('/api/login', json={**self.secret,'remember':remember,'device':'Test device'}, headers={'Origin':self.origin})
        self.assertEqual(response.status, 200)
        self.csrf = (await response.json())['csrf']
        return response

    async def post(self, path, data=None):
        return await self.client.post('/api/'+path, json=data or {}, headers={'X-CSRF-Token':self.csrf,'Origin':self.origin})

    async def test_angular_frontend_is_packaged(self):
        response = await self.client.get('/')
        self.assertEqual(response.status, 200)
        self.assertIn('app-root', await response.text())

    async def test_path_prefixed_frontend_rewrites_base_href(self):
        self.state.base_path = '/network-assist'
        response = await self.client.get('/')
        self.assertEqual(response.status, 200)
        self.assertIn('<base href="/network-assist/">', await response.text())

    async def test_persistent_login_device_revoke(self):
        r = await self.login(30)
        self.assertEqual(r.cookies['panel_session']['max-age'], '2592000')
        security = await (await self.client.get('/api/security')).json()
        self.assertEqual(len(security['devices']), 2)
        current = next(d for d in security['devices'] if d['current'])
        self.assertEqual((await self.post('device/revoke', {'id':current['token_hash']})).status, 200)
        self.assertEqual((await self.client.get('/api/hosts')).status, 401)

    async def test_origin_csrf_and_secret_redaction(self):
        self.assertEqual((await self.client.post('/api/host/save', json={})).status, 403)
        self.assertEqual((await self.client.post('/api/login', json=self.secret, headers={'Origin':'https://evil.test'})).status, 403)
        body = await (await self.client.get('/api/credentials')).text()
        self.assertNotIn('fixture-password', body)
        self.assertNotIn('fixture-password', self.state.db.read_bytes().decode(errors='ignore'))
        self.assertNotIn('secret', body)

    async def test_additional_http_origin_uses_non_secure_cookie(self):
        self.state.origin = 'https://network.example.com'
        self.state.secure = True
        self.state.allowed_origins = ('https://network.example.com', 'http://10.201.250.1:9180')
        response = await self.client.post('/api/login', json=self.secret,
            headers={'Origin':'http://10.201.250.1:9180'})
        self.assertEqual(response.status, 200)
        self.assertFalse(response.cookies['panel_session']['secure'])
        session = await self.client.get('/api/session', headers={'Origin':'http://10.201.250.1:9180'})
        body = await session.json()
        self.assertFalse(body['secure'])
        self.assertFalse(body['passkeys'])
        public = await self.client.get('/api/session', headers={
            'X-Forwarded-Proto':'https', 'X-Forwarded-Host':'network.example.com'})
        public_body = await public.json()
        self.assertTrue(public_body['secure'])
        self.assertTrue(public_body['passkeys'])

    async def test_real_ssh_direct_and_jump(self):
        r = await self.post('host/test', {'id': self.host['id']})
        self.assertEqual(r.status, 200, await r.text())
        self.assertIn('fixture-host', (await r.json())['output'])
        target = self.state.save_host({**self.host, 'id':'target', 'name':'Through jump', 'jump_id':self.host['id']})
        r = await self.post('host/test', {'id':target['id']})
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual((await r.json())['route'], ['Test target','Through jump'])
        self.assertEqual((await self.post('host/delete', {'id':self.host['id']})).status, 400)
        self.assertEqual((await self.post('credential/delete', {'id':self.credential})).status, 400)
        with self.assertRaises(ValueError):
            self.state.save_host({**self.host, 'jump_id':target['id']})

    async def test_wrong_hostkey_and_wrong_password(self):
        key = asyncssh.generate_private_key('ssh-ed25519')
        self.state.save_host({**self.host, 'host_key':key.export_public_key().decode()})
        r = await self.post('host/test', {'id':self.host['id']})
        self.assertEqual(r.status, 400)
        self.assertIn('指纹', (await r.json())['error'])
        wrong = self.state.save_credential({'name':'wrong','kind':'password','secret':'wrong'})
        self.state.save_host({**self.host,'credential_id':wrong})
        r = await self.post('host/test', {'id':self.host['id']})
        self.assertEqual(r.status, 400)
        self.assertIn('认证失败', (await r.json())['error'])

    async def test_websocket_real_shell_and_revocation(self):
        r = await self.post('terminal/ticket', {'id':self.host['id']})
        ticket = (await r.json())['ticket']
        ws = await self.client.ws_connect('/ws', headers={'Origin':self.origin})
        await ws.send_json({'ticket':ticket})
        msg = await ws.receive_json(timeout=5)
        self.assertEqual(msg['type'], 'ready', msg)
        output = await ws.receive_bytes(timeout=5)
        self.assertIn(b'fixture-shell', output)
        await ws.send_json({'type':'input','data':'hello\n'})
        result = await ws.receive_bytes(timeout=5)
        self.assertIn(b'hello', result)
        await self.post('logout')
        msg = await ws.receive(timeout=5)
        self.assertIn(msg.type.name, ('CLOSE','CLOSED'))
        await ws.close()

    async def test_websocket_no_origin_and_ticket_replay(self):
        with self.assertRaises(Exception):
            await self.client.ws_connect('/ws')
        r = await self.post('terminal/ticket', {'id':self.host['id']})
        ticket = (await r.json())['ticket']
        self.state.tickets[ticket]['expires'] = 0
        ws = await self.client.ws_connect('/ws', headers={'Origin':self.origin})
        await ws.send_json({'ticket':ticket})
        self.assertEqual((await ws.receive(timeout=5)).type.name, 'CLOSE')
        await ws.close()

    async def test_reauth_and_password_change(self):
        self.state.recent.clear()
        self.assertEqual((await self.post('credential/save',{'name':'new','kind':'password','secret':'abc'})).status,403)
        self.assertEqual((await self.post('reauth', {'password':self.secret['password']})).status,200)
        self.assertEqual((await self.post('password', {'new_password':'new-long-password'})).status,200)
        self.assertEqual((await self.client.get('/api/hosts')).status,401)
        self.secret['password']='new-long-password'
        self.secret['key']=''
        await self.login()


    async def test_changed_host_invalidates_live_terminal(self):
        r = await self.post('terminal/ticket', {'id':self.host['id']})
        ws = await self.client.ws_connect('/ws', headers={'Origin':self.origin})
        await ws.send_json({'ticket':(await r.json())['ticket']})
        self.assertEqual((await ws.receive_json(timeout=5))['type'], 'ready')
        await ws.receive_bytes(timeout=5)
        self.state.save_host({**self.host, 'terminal_enabled':False})
        self.assertEqual((await ws.receive(timeout=5)).type.name, 'CLOSE')
        await ws.close()

    async def test_ticket_bound_to_session_and_single_use(self):
        r = await self.post('terminal/ticket', {'id':self.host['id']})
        ticket = (await r.json())['ticket']
        await self.login()
        ws = await self.client.ws_connect('/ws', headers={'Origin':self.origin})
        await ws.send_json({'ticket':ticket})
        self.assertEqual((await ws.receive(timeout=5)).type.name, 'CLOSE')
        self.assertNotIn(ticket, self.state.tickets)
        await ws.close()

    async def test_missing_pin_and_management_limits(self):
        self.state.save_host({**self.host, 'host_key':''})
        r = await self.post('host/test', {'id':self.host['id']})
        self.assertEqual(r.status,400)
        self.assertIn('指纹', (await r.json())['error'])
        self.assertEqual((await self.post('host/save',{**self.host,'port':70000})).status,400)
        self.assertEqual((await self.post('host/save',{**self.host,'username':'user;whoami'})).status,400)
        self.assertEqual((await self.post('password',{'new_password':'short'})).status,400)
        self.assertEqual((await self.post('passkey/options')).status,400)

    async def test_network_profile_api_and_host_dependency(self):
        target = self.state.save_host({**self.host, 'id':'network-client', 'name':'Network client'})
        r = await self.post('network/profile/save', {'name':'Fixture assist',
            'gateway_id':self.host['id'], 'client_ids':[target['id']], 'port':52011,
            'preserve_routes':['10.0.0.1/32']})
        self.assertEqual(r.status, 200, await r.text())
        profile = (await r.json())['profile']
        listing = await (await self.client.get('/api/network')).json()
        self.assertEqual(listing['profiles'][0]['id'], profile['id'])
        self.assertEqual((await self.post('host/delete', {'id':target['id']})).status, 400)
        self.assertEqual((await self.post('network/profile/delete', {'id':profile['id']})).status, 200)

    async def test_network_probe_is_read_only_and_authenticated(self):
        sample = {'id':self.host['id'],'name':'Test target','address':'127.0.0.1',
            'ssh':True,'dns':True,'internet':True,'helper':False,'hostname':'fixture',
            'os':'Linux','default_route':'default via 127.0.0.1','http_code':'204',
            'assist':[],'error':''}
        with patch('server_network_assist.app.network_probe_host', new=AsyncMock(return_value=sample)):
            response = await self.post('network/probe', {'ids':[self.host['id']]})
        self.assertEqual(response.status, 200, await response.text())
        self.assertTrue((await response.json())['results'][0]['internet'])


if __name__ == '__main__':
    unittest.main()
