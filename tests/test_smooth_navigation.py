import asyncio
import json
import os
import secrets
import tempfile
import threading
import time
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet
from server_network_assist.app import create_app, initialize
from server_network_assist.client import handler_for
from server_network_assist.navigation_reads import ConcurrentReads


class ConcurrentReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlap_shared_but_completed_result_not_cached(self):
        reads = ConcurrentReads()
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return calls

        self.assertEqual(await asyncio.gather(reads.get('live', load), reads.get('live', load)), [1, 1])
        self.assertEqual(await reads.get('live', load), 2)

    async def test_cancelled_observer_does_not_cancel_foreground(self):
        reads = ConcurrentReads()
        gate = asyncio.Event()

        async def load():
            await gate.wait()
            return 'live'

        observer = asyncio.create_task(reads.get('live', load))
        foreground = asyncio.create_task(reads.get('live', load))
        await asyncio.sleep(0)
        observer.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await observer
        gate.set()
        self.assertEqual(await foreground, 'live')

    async def test_mutation_detaches_previous_probe(self):
        reads = ConcurrentReads()
        gate = asyncio.Event()

        async def old():
            await gate.wait()
            return 'old'

        pending = asyncio.create_task(reads.get('live', old))
        await asyncio.sleep(0)
        reads.invalidate()
        self.assertEqual(await reads.get('live', lambda: asyncio.sleep(0, result='new')), 'new')
        gate.set()
        self.assertEqual(await pending, 'old')


class NavigationAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        data = root / 'data'
        initialize(data)
        self.secret = json.loads((data / 'initial-login.json').read_text())
        key = root / 'vault.key'
        key.write_bytes(Fernet.generate_key())
        with patch.dict(os.environ, {'PANEL_ORIGIN':'', 'PANEL_ALLOWED_ORIGINS':''}):
            app = create_app(data, key)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = str(self.client.make_url('/')).rstrip('/')

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def login(self):
        response = await self.client.post('/api/login', json=self.secret, headers={'Origin':self.origin})
        self.assertEqual(response.status, 200)
        return (await response.json())['csrf']

    async def test_authentication_before_probes_and_concurrent_live_data(self):
        calls = 0

        def proxy():
            nonlocal calls
            calls += 1
            time.sleep(0.08)
            return {'available':False,'running':False,'reason':'fixture'}

        with patch('server_network_assist.commercial_proxy.proxy_status', side_effect=proxy), patch('server_network_assist.commercial_proxy.physical_defaults', return_value={}):
            response = await self.client.get('/api/client-service')
            self.assertEqual(response.status, 401)
            self.assertEqual(calls, 0)
            csrf = await self.login()
            responses = await asyncio.gather(self.client.get('/api/client-service'), self.client.get('/api/client-service/dashboard'))
            self.assertEqual(calls, 1)
            for response in responses:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertIn('navigation-data;dur=', response.headers['Server-Timing'])
                await response.json()
            response = await self.client.get('/api/client-service')
            self.assertEqual(response.status, 200)
            await response.json()
            self.assertEqual(calls, 2, 'Live readiness is not retained after a request')
            response = await self.client.post('/api/logout', json={}, headers={'Origin':self.origin,'X-CSRF-Token':csrf})
            self.assertEqual(response.status, 200)
            self.assertEqual((await self.client.get('/api/client-service/dashboard')).status, 401)

    async def test_static_validator_and_deep_link_shell(self):
        # The authored module is available even before packaging new Angular assets.
        from server_network_assist.app import ROOT
        with patch('server_network_assist.app.ROOT', ROOT):
            response = await self.client.get('/subscriptions.js')
            self.assertEqual(response.status, 200)
            etag = response.headers['ETag']
            await response.read()
            self.assertEqual(response.headers['Cache-Control'], 'public, max-age=0, must-revalidate')
            response = await self.client.get('/subscriptions.js', headers={'If-None-Match':etag})
            self.assertEqual(response.status, 304)
            self.assertEqual(await response.read(), b'')
            response = await self.client.get('/?view=commercial')
            self.assertEqual(response.status, 200)
            self.assertIn(b'<app-root>', await response.read())
            self.assertEqual(response.headers['Cache-Control'], 'no-store')


class CustomerStaticTests(unittest.TestCase):
    def test_query_deep_link_static_304_and_api_token_guard(self):
        class Panel:
            token = secrets.token_urlsafe(24)
            def state(self):
                return {'fixture':True}

        panel = Panel()
        server = ThreadingHTTPServer(('127.0.0.1',0), handler_for(panel))
        panel.origin = f'http://127.0.0.1:{server.server_port}'
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(panel.origin+'/?view=subscription-history') as response:
                self.assertIn(b'smooth-navigation.js', response.read())
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with urlopen(panel.origin+'/smooth-navigation.js') as response:
                etag = response.headers['ETag']
                self.assertIn(b'createReadCache', response.read())
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(panel.origin+'/smooth-navigation.js', headers={'If-None-Match':etag}))
            self.assertEqual(caught.exception.code, 304)
            self.assertEqual(caught.exception.read(), b'')
            with self.assertRaises(HTTPError) as caught:
                urlopen(panel.origin+'/api/state')
            self.assertEqual(caught.exception.code, 403)
            with urlopen(Request(panel.origin+'/api/state', headers={'X-Client-Token':panel.token})) as response:
                self.assertEqual(json.load(response), {'fixture':True})
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
