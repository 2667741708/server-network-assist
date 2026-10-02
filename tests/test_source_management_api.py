import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet
from server_network_assist.app import STATE_KEY, create_app, initialize


class SourceManagementAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);data=root/'data'
        initialize(data);secret=json.loads((data/'initial-login.json').read_text())
        key=root/'vault.key';key.write_bytes(Fernet.generate_key())
        with patch.dict(os.environ,{'PANEL_ORIGIN':'','PANEL_ALLOWED_ORIGINS':''}):self.app=create_app(data,key)
        self.client=TestClient(TestServer(self.app));await self.client.start_server()
        self.origin=str(self.client.make_url('/')).rstrip('/')
        response=await self.client.post('/api/login',json=secret,headers={'Origin':self.origin})
        self.csrf=(await response.json())['csrf'];self.store=self.app[STATE_KEY].client_service
        for i in (1,2):
            self.store.save_source({'id':f's{i}','name':f'源网{i}','endpoint':f'10.20.32.{i}:51910',
                'relay_public_key':base64.b64encode(bytes([i])*32).decode(),'address_pool':f'10.214.{i}.0/24',
                'relay_interface':f'wg-source{i}','egress_interface':'eth0','egress_gateway':f'10.20.32.{i+10}'})
    async def asyncTearDown(self):
        await self.client.close();self.tmp.cleanup()
    async def post(self,payload,csrf=None):
        return await self.client.post('/api/client-service/action',json=payload,
            headers={'Origin':self.origin,'X-CSRF-Token':self.csrf if csrf is None else csrf})
    async def test_capability_and_source_mutations_require_auth_csrf(self):
        listing=await (await self.client.get('/api/client-service')).json()
        self.assertTrue(listing['source_management_supported']);source=listing['sources'][0]
        payload={'action':'source-enable','id':source['id'],'enabled':False,'expected_revision':source['revision']}
        response=await self.post(payload,'bad-csrf');self.assertEqual(response.status,403)
        self.assertTrue(self.store.list_sources()[0]['enabled'])
        response=await self.post(payload);self.assertEqual(response.status,200)
        self.assertFalse(self.store.list_sources()[0]['enabled'])
        response=await self.post(payload);self.assertEqual(response.status,400)
        await self.client.post('/api/logout',json={},headers={'Origin':self.origin,'X-CSRF-Token':self.csrf})
        response=await self.post(payload);self.assertEqual(response.status,401)
    async def test_migrate_api_keeps_enrollment_and_rejects_replay(self):
        response=await self.post({'action':'subscription-generate','name':'customer','source_ids':['s1'],
            'quota_gb':10,'base_url':'https://entry.example'})
        self.assertEqual(response.status,200);issued=await response.json()
        old=self.store.list_grants(issued['customer_id'])[0]
        source=next(s for s in self.store.list_sources() if s['id']=='s2')
        payload={'action':'grant-source','id':old['id'],'source_id':'s2','egress_mode':'physical',
            'expected_grant':old,'expected_source_revision':source['revision']}
        response=await self.post(payload);self.assertEqual(response.status,200)
        self.assertTrue((await response.json())['reconnect_required'])
        response=await self.post(payload);self.assertEqual(response.status,400)
        response=await self.post({'action':'subscription-view','id':issued['customer_id']})
        self.assertEqual((await response.json())['url'],issued['url'])
