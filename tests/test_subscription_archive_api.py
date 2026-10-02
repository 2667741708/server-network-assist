import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet
from server_network_assist.app import STATE_KEY, create_app, initialize


class SubscriptionArchiveAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data = root / 'data'
        initialize(self.data)
        self.secret = json.loads((self.data / 'initial-login.json').read_text())
        key = root / 'vault.key'
        key.write_bytes(Fernet.generate_key())
        with patch.dict(os.environ, {'PANEL_ORIGIN':'','PANEL_ALLOWED_ORIGINS':''}):
            app = create_app(self.data,key)
        self.state = app[STATE_KEY]
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = str(self.client.make_url('/')).rstrip('/')
        self.state.client_service.save_source({'id':'source','name':'源网','endpoint':'10.20.32.13:51910',
            'address_pool':'10.77.0.0/24','relay_public_key':base64.b64encode(b'x'*32).decode(),
            'relay_interface':'test','egress_interface':'eth0','egress_gateway':'10.20.32.1'})
        self.payload = {'action':'subscription-generate','name':'客户','quota_gb':10,
            'source_ids':['source'],'base_url':'https://service.example'}
        self.csrf = ''

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def login(self):
        response = await self.client.post('/api/login',json=self.secret,headers={'Origin':self.origin})
        self.assertEqual(response.status,200)
        self.csrf = (await response.json())['csrf']

    async def action(self,payload):
        return await self.client.post('/api/client-service/action',json=payload,
            headers={'Origin':self.origin,'X-CSRF-Token':self.csrf})

    async def test_generate_list_view_reissue_and_relogin(self):
        await self.login()
        response = await self.action(self.payload)
        self.assertEqual(response.status,200)
        result = await response.json()
        response = await self.client.get('/api/client-service')
        listing = await response.json()
        self.assertEqual(listing['subscription_addresses'][0]['available'],True)
        self.assertNotIn(result['url'],json.dumps(listing))
        await self.login()
        response = await self.action({'action':'subscription-view','id':result['customer_id']})
        self.assertEqual(response.status,200)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual((await response.json())['url'],result['url'])
        response = await self.action({'action':'subscription-reissue','id':result['customer_id'],'base_url':'https://service.example'})
        self.assertEqual(response.status,200)
        reissued = await response.json()
        self.assertNotEqual(reissued['url'],result['url'])
        self.assertEqual(reissued['customer_id'],result['customer_id'])
        self.assertEqual(len(self.state.client_service.list_customers()),1)

        response = await self.action({'action':'subscription-view','id':result['customer_id']})
        self.assertEqual((await response.json())['url'],reissued['url'])

    async def test_generate_unlimited_and_custom_terms_with_speed_controls(self):
        import time
        await self.login()
        for days in (180,365,None):
            expiry=None if days is None else int(time.time())+days*86400
            response=await self.action(self.payload | {'expires_at':expiry,'download_bps':None,'upload_bps':12345000})
            self.assertEqual(response.status,200)
            created=await response.json()
            self.assertEqual(created['expires_at'],expiry)
            plan=self.state.client_service.get_plan(self.state.client_service.customer(created['customer_id'])['plan_id'])
            self.assertIsNone(plan['download_bps'])
            self.assertEqual(plan['upload_bps'],12345000)
            self.assertEqual(plan['period_seconds'],30*86400)
        count=len(self.state.client_service.list_customers())
        response=await self.action(self.payload | {'expires_at':int(time.time())-1})
        self.assertEqual(response.status,400)
        self.assertEqual(len(self.state.client_service.list_customers()),count)

    async def test_admin_csrf_origin_and_recent_verification_required(self):
        for action in ('subscription-view','subscription-reissue'):
            response = await self.action({'action':action,'id':'unknown','base_url':'https://service.example'})
            self.assertEqual(response.status,401)
        await self.login()
        response = await self.client.post('/api/client-service/action',json=self.payload,headers={'Origin':self.origin})
        self.assertEqual(response.status,403)
        response = await self.client.post('/api/client-service/action',json=self.payload,
            headers={'Origin':'https://invalid.example','X-CSRF-Token':self.csrf})
        self.assertEqual(response.status,403)
        self.state.recent.clear()
        for action in ('subscription-view','subscription-reissue'):
            response = await self.action({'action':action,'id':'unknown','base_url':'https://service.example'})
            self.assertEqual(response.status,403)

    async def test_legacy_view_error_does_not_create_customer(self):
        legacy = self.state.client_service.generate_monthly_subscription('旧客户',10,['source'])
        await self.login()
        response = await self.action({'action':'subscription-view','id':legacy['customer_id']})
        self.assertEqual(response.status,400)
        self.assertIn('无法还原',(await response.json())['error'])
        self.assertEqual(len(self.state.client_service.list_customers()),1)

    async def test_dashboard_and_service_update_are_admin_only_and_keep_address(self):
        response=await self.client.get('/api/client-service/dashboard')
        self.assertEqual(response.status,401)
        await self.login()
        response=await self.action(self.payload);created=await response.json()
        response=await self.client.get('/api/client-service/dashboard')
        self.assertEqual(response.status,200)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        snapshot=await response.json();customer=snapshot['customers'][0]
        self.assertNotIn(created['url'],json.dumps(snapshot))
        body={'action':'subscription-update','id':customer['id'],'service':{
            'expected_version':customer['version'],'expected_revision':customer['revision'],'quota_bytes':None}}
        response=await self.client.post('/api/client-service/action',json=body,headers={'Origin':self.origin})
        self.assertEqual(response.status,403)
        self.state.recent.clear()
        response=await self.action(body);self.assertEqual(response.status,403)
        await self.login()
        response=await self.action(body);self.assertEqual(response.status,200)
        self.assertFalse((await response.json())['subscription_address_changed'])
        response=await self.action({'action':'subscription-view','id':customer['id']})
        self.assertEqual((await response.json())['url'],created['url'])

    async def test_customer_metadata_archive_and_legacy_enable_guard(self):
        await self.login()
        created=await (await self.action(self.payload)).json()
        c=(await (await self.client.get('/api/client-service/dashboard')).json())['customers'][0]
        body={'action':'subscription-update','id':c['id'],'service':{
            'expected_version':c['version'],'expected_revision':c['revision'],
            'tags':['测试客户'],'notes':'仅管理员可见的备注','lifecycle':'archived'}}
        response=await self.client.post('/api/client-service/action',json=body,headers={'Origin':self.origin})
        self.assertEqual(response.status,403)
        response=await self.action(body);self.assertEqual(response.status,200)
        c=(await (await self.client.get('/api/client-service/dashboard')).json())['customers'][0]
        self.assertEqual(c['tags'],['测试客户']);self.assertFalse(c['enabled'])
        response=await self.action({'action':'customer-enable','id':c['id'],'enabled':True})
        self.assertEqual(response.status,400);self.assertIn('先将客户',(await response.json())['error'])
        body['service']={'expected_version':c['version'],'expected_revision':c['revision'],'lifecycle':'active'}
        response=await self.action(body);self.assertEqual(response.status,200)
        self.assertFalse(self.state.client_service.customer(c['id'])['enabled'])
        response=await self.action({'action':'customer-enable','id':c['id'],'enabled':True})
        self.assertEqual(response.status,200)
        response=await self.action({'action':'subscription-view','id':c['id']})
        self.assertEqual((await response.json())['url'],created['url'])

    async def test_authenticated_grant_egress_migration_and_csrf(self):
        await self.login()
        response=await self.action(self.payload);customer=(await response.json())['customer_id']
        grant=self.state.client_service.list_grants(customer)[0]
        body={'action':'grant-egress','id':grant['id'],'egress_mode':'physical',
              'egress_interface':'eth0','egress_gateway':'10.20.32.1','dns':'1.1.1.1'}
        response=await self.client.post('/api/client-service/action',json=body,headers={'Origin':self.origin})
        self.assertEqual(response.status,403)
        response=await self.action(body);self.assertEqual(response.status,200)
        updated=(await response.json())['grant']
        self.assertEqual(json.loads(updated['egress_policy'])['egress_mode'],'physical')
        self.assertEqual(updated['dns'],'1.1.1.1')

    async def test_proxy_start_requires_admin_csrf_and_fresh_auth(self):
        body={'action':'source-proxy-start'}
        response=await self.action(body);self.assertEqual(response.status,401)
        await self.login()
        response=await self.client.post('/api/client-service/action',json=body,headers={'Origin':self.origin})
        self.assertEqual(response.status,403)
        with patch('server_network_assist.commercial_proxy.start_proxy',return_value={'available':True}) as start:
            response=await self.action(body);self.assertEqual(response.status,200);start.assert_called_once()
        self.state.recent.clear()
        response=await self.action(body);self.assertEqual(response.status,403)
