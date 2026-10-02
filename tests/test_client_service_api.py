import base64
import hashlib
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server_network_assist.client_crypto import public_key_text, sign_payload
from server_network_assist.client_service_api import ClientServiceAPI
from server_network_assist.client_store import ClientStore


class ClientServiceAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ClientStore(Path(self.tmp.name) / 'commercial.sqlite3')
        plan = self.store.create_plan('20M', download_bps=20_000_000, upload_bps=5_000_000,
                                      quota_bytes=10_000, lease_seconds=600)
        self.customer = self.store.create_customer('移动宽带客户', plan['id'])
        self.enrollment = self.store.create_enrollment_token(self.customer['id'])
        self.grant = self.store.grant_line(self.customer['id'], '移动线路', 'hidden-relay',
            'relay.example.test:51820', relay_public_key=base64.b64encode(b'r' * 32).decode(),
            allocated_address='10.203.0.2/32', relay_interface='wg-customer', egress_interface='eth0')
        self.signing = Ed25519PrivateKey.generate()
        self.api = ClientServiceAPI(self.store, relay_token='relay-secret')

        @web.middleware
        async def errors(request, handler):
            try:
                return await handler(request)
            except web.HTTPException as exc:
                return web.json_response({'error': exc.text}, status=exc.status)
            except ValueError as exc:
                return web.json_response({'error': str(exc)}, status=400)

        app = web.Application(middlewares=[errors])
        app.router.add_get('/client/v1/{tail:.*}', self.api.handle_client)
        app.router.add_post('/client/v1/{tail:.*}', self.api.handle_client)
        app.router.add_get('/relay/v1/{tail:.*}', self.api.handle_relay)
        app.router.add_post('/relay/v1/{tail:.*}', self.api.handle_relay)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        response = await self.client.post('/client/v1/enroll', json={
            'enrollment_token': self.enrollment,
            'signing_public_key': public_key_text(self.signing.public_key()),
            'wireguard_public_key': base64.b64encode(b'w' * 32).decode(), 'label': '测试设备'})
        self.assertEqual(response.status, 200)
        self.device_id = (await response.json())['device_id']

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    def auth(self, method, path, body=b'', nonce='nonce-000000000001'):
        timestamp = int(time.time())
        signed = {'method': method, 'path': path, 'timestamp': timestamp, 'nonce': nonce,
                  'body_sha256': hashlib.sha256(body).hexdigest()}
        return {'X-Device-ID': self.device_id, 'X-Device-Timestamp': str(timestamp),
                'X-Device-Nonce': nonce, 'X-Device-Signature': sign_payload(self.signing, signed)}

    async def test_device_signed_routes_and_replay_rejection(self):
        path = '/client/v1/routes'
        headers = self.auth('GET', path)
        response = await self.client.get(path, headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['routes'][0]['name'], '移动线路')
        response = await self.client.get(path, headers=headers)
        self.assertEqual(response.status, 400)
        self.assertIn('重放', (await response.json())['error'])

    async def test_physical_route_reports_proxy_health_independently(self):
        from unittest.mock import patch
        self.store.set_grant_egress(self.grant['id'],egress_mode='physical',
            egress_interface='eth0',egress_gateway='10.20.32.1',dns='1.1.1.1')
        with patch('server_network_assist.commercial_proxy.source_is_local',return_value=True), \
             patch('server_network_assist.commercial_proxy.proxy_status',return_value={
                 'available':False,'running':False,'tun_ready':False,'interface':'Meta','reason':'未就绪'}):
            row=self.api.routes(self.store.device(self.device_id))[0]
        self.assertTrue(row['available'])
        self.assertEqual(row['egress_mode'],'physical')
        self.assertEqual(row['egress_interface'],'eth0')
        self.assertTrue(row['source_proxy']['checked'])
        self.assertFalse(row['source_proxy']['available'])
        with patch('server_network_assist.commercial_proxy.source_is_local',return_value=False):
            row=self.api.routes(self.store.device(self.device_id))[0]
        self.assertFalse(row['source_proxy']['checked'])
        self.assertIsNone(row['source_proxy']['available'])

    async def test_proxy_status_uses_grant_interface_when_registration_id_is_missing(self):
        from unittest.mock import patch
        import json
        grant=self.grant | {'egress_interface':'ProxyTun',
            'egress_policy':json.dumps({'egress_mode':'source_proxy','require_source_proxy':True})}
        with patch.object(self.store,'list_grants',return_value=[grant]), \
             patch('server_network_assist.commercial_proxy.grant_available',return_value=True), \
             patch('server_network_assist.commercial_proxy.source_is_local',return_value=True), \
             patch('server_network_assist.commercial_proxy.proxy_status',return_value={
                 'available':True,'interface':'ProxyTun'}) as probe:
            row=self.api.routes(self.store.device(self.device_id))[0]
        probe.assert_called_once_with('ProxyTun')
        self.assertEqual(row['source_proxy']['interface'],'ProxyTun')

    async def test_lease_relay_accounting_and_immediate_revoke(self):
        import json
        path = '/client/v1/lease'
        body = json.dumps({'grant_id': self.grant['id']}).encode()
        response = await self.client.post(path, data=body, headers={**self.auth('POST', path, body),
            'Content-Type': 'application/json'})
        self.assertEqual(response.status, 200)
        lease = (await response.json())['lease']
        response = await self.client.get('/relay/v1/reconcile', headers={'Authorization': 'Bearer relay-secret'})
        self.assertEqual(response.status, 200)
        policy = (await response.json())['policies'][0]
        self.assertEqual(policy['peer_id'], lease['id'])
        self.assertNotIn('egress_mode', policy)
        self.assertNotIn('egress_gateway', policy)
        response = await self.client.post('/relay/v1/usage', json={'lease_id': lease['id'],
            'report_id': 'sample-1', 'rx_total': 100, 'tx_total': 50},
            headers={'Authorization': 'Bearer relay-secret'})
        self.assertEqual((await response.json())['usage']['used_bytes'], 150)
        self.store.revoke('lease', lease['id'])
        response = await self.client.get('/relay/v1/reconcile?since=0', headers={'Authorization': 'Bearer relay-secret'})
        value = await response.json()
        self.assertFalse(value['policies'])
        self.assertEqual(value['revoked'][0]['id'], lease['id'])

    async def test_signed_client_usage_report_updates_dashboard_accounting(self):
        import json
        path = '/client/v1/lease'
        body = json.dumps({'grant_id': self.grant['id']}).encode()
        response = await self.client.post(path, data=body, headers={**self.auth('POST', path, body),
            'Content-Type': 'application/json'})
        self.assertEqual(response.status, 200)
        lease = (await response.json())['lease']
        path = '/client/v1/usage/report'
        body = json.dumps({'lease_id': lease['id'], 'report_id': 'client-sample-1',
                           'rx_total': 120, 'tx_total': 30}).encode()
        response = await self.client.post(path, data=body, headers={**self.auth('POST', path, body,
            nonce='nonce-000000000004'), 'Content-Type': 'application/json'})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['usage']['used_bytes'], 150)

    async def test_client_usage_report_cannot_write_another_device_lease(self):
        import json
        path = '/client/v1/lease'
        body = json.dumps({'grant_id': self.grant['id']}).encode()
        response = await self.client.post(path, data=body, headers={**self.auth('POST', path, body),
            'Content-Type': 'application/json'})
        lease = (await response.json())['lease']
        other = Ed25519PrivateKey.generate()
        other_customer = self.store.create_customer('另一客户', self.customer['plan_id'])
        other_enrollment = self.store.create_enrollment_token(other_customer['id'])
        response = await self.client.post('/client/v1/enroll', json={
            'enrollment_token': other_enrollment,
            'signing_public_key': public_key_text(other.public_key()),
            'wireguard_public_key': base64.b64encode(b'x' * 32).decode(), 'label': '另一设备'})
        other_id = (await response.json())['device_id']
        self.device_id = other_id
        self.signing = other
        path = '/client/v1/usage/report'
        body = json.dumps({'lease_id': lease['id'], 'report_id': 'wrong-device',
                           'rx_total': 1, 'tx_total': 1}).encode()
        response = await self.client.post(path, data=body, headers={**self.auth('POST', path, body,
            nonce='nonce-000000000005'), 'Content-Type': 'application/json'})
        self.assertEqual(response.status, 400)
        self.assertIn('不属于当前设备', (await response.json())['error'])

    async def test_physical_policy_reaches_relay_but_internal_gateway_is_hidden_from_client(self):
        self.store.set_grant_egress(self.grant['id'], egress_mode='physical',
            egress_interface='eth0', egress_gateway='10.20.32.1', dns='1.1.1.1')
        import json
        path='/client/v1/lease';body=json.dumps({'grant_id':self.grant['id']}).encode()
        response=await self.client.post(path,data=body,headers={**self.auth('POST',path,body),'Content-Type':'application/json'})
        self.assertEqual(response.status,200);lease=(await response.json())['lease']
        self.assertEqual(lease['egress_mode'],'physical');self.assertEqual(lease['dns'],'1.1.1.1')
        self.assertNotIn('egress_gateway',lease);self.assertNotIn('egress_interface',lease)
        response=await self.client.get('/relay/v1/reconcile',headers={'Authorization':'Bearer relay-secret'})
        self.assertEqual(response.status,200);policy=(await response.json())['policies'][0]
        self.assertEqual(policy['egress_mode'],'physical');self.assertEqual(policy['egress_gateway'],'10.20.32.1')

    async def test_proxy_health_loss_omits_forwarding_and_rejects_renewal(self):
        import json
        from unittest.mock import patch
        with patch('server_network_assist.commercial_proxy.source_is_local',return_value=True),patch('server_network_assist.commercial_proxy.require_proxy'),patch('server_network_assist.commercial_proxy.proxy_status',return_value={'available':True}):
            self.store.set_grant_egress(self.grant['id'],egress_mode='source_proxy',egress_interface='Meta')
            path='/client/v1/lease';body=json.dumps({'grant_id':self.grant['id']}).encode()
            response=await self.client.post(path,data=body,headers={**self.auth('POST',path,body),'Content-Type':'application/json'})
            self.assertEqual(response.status,200);lease=(await response.json())['lease']
        with patch('server_network_assist.commercial_proxy.source_is_local',return_value=True),patch('server_network_assist.commercial_proxy.proxy_status',return_value={'available':False}):
            response=await self.client.get('/relay/v1/reconcile',headers={'Authorization':'Bearer relay-secret'})
            self.assertEqual(response.status,200);self.assertEqual((await response.json())['policies'],[])
            path='/client/v1/routes'
            response=await self.client.get(path,headers=self.auth('GET',path,nonce='nonce-000000000002'))
            self.assertFalse((await response.json())['routes'][0]['available'])
            path='/client/v1/lease/renew';body=json.dumps({'lease_id':lease['id']}).encode()
            response=await self.client.post(path,data=body,headers={**self.auth('POST',path,body,nonce='nonce-000000000003'),'Content-Type':'application/json'})
            self.assertEqual(response.status,400);self.assertIn('未就绪',(await response.json())['error'])


if __name__ == '__main__':
    unittest.main()
