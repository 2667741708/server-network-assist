import base64
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server_network_assist.client import ClientPanel, handler_for
from server_network_assist.client_admin import keygen, sign
from server_network_assist.client_subscription import SubscriptionStore, local_device_id, validate_payload


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def payload(now=None):
    now = int(time.time()) if now is None else now
    return {
        'schema_version': 1, 'version': 1, 'subscription_id': 'subscription-1',
        'device_id': local_device_id(), 'nonce': 'nonce-000000000001',
        'issued_at': now - 10, 'expires_at': now + 600,
        'customer': {'display_name': '移动宽带用户'},
        'lines': [{'id': 'line-1', 'name': '个人移动宽带出口', 'tunnel': 'customer-line-1',
                   'endpoint': 'relay.example.test:51820', 'available': True,
                   'download_bps': 2_500_000, 'upload_bps': 625_000,
                   'quota_bytes': 1000, 'used_bytes': 100}],
    }


class Response:
    def __init__(self, body, url='https://subscription.example.test/value'):
        self.body, self.url = body, url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.body


class Opener:
    def __init__(self, response):
        self.response = response

    def open(self, request, timeout):
        return self.response


class ClientSubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SubscriptionStore(Path(self.tmp.name))
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.url = 'https://subscription.example.test/value?token=secret#key=' + b64(public)

    def tearDown(self):
        self.tmp.cleanup()

    def envelope(self, value):
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        return json.dumps({'payload': value, 'signature': b64(self.private.sign(canonical))}).encode()

    def test_signed_subscription_is_pinned_cached_and_redacted_from_summary(self):
        self.store.save_url(self.url)
        result = self.store.update(Opener(Response(self.envelope(payload()))))
        self.assertEqual(result['lines'][0]['name'], '个人移动宽带出口')
        summary = self.store.summary()
        self.assertEqual(summary['provider'], 'subscription.example.test')
        self.assertNotIn('secret', json.dumps(summary))

    def test_modified_subscription_and_cross_host_redirect_are_rejected(self):
        self.store.save_url(self.url)
        value = payload()
        body = self.envelope(value)
        changed = json.loads(body)
        changed['payload']['lines'][0]['name'] = '被篡改'
        with self.assertRaisesRegex(ValueError, '签名'):
            self.store.update(Opener(Response(json.dumps(changed).encode())))
        with self.assertRaisesRegex(ValueError, '跨域'):
            self.store.update(Opener(Response(body, 'https://other.example.test/value')))

    def test_cached_envelope_is_verified_again_before_use(self):
        self.store.save_url(self.url)
        self.store.update(Opener(Response(self.envelope(payload()))))
        cache = Path(self.tmp.name) / 'customer-subscription-cache.json'
        value = json.loads(cache.read_text(encoding='utf-8'))
        value['payload']['lines'][0]['tunnel'] = 'admin-secret'
        cache.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '签名'):
            self.store.cached()

    def test_subscription_is_bound_to_local_device(self):
        self.store.save_url(self.url)
        value = payload()
        value['device_id'] = 'device-not-this-machine'
        with self.assertRaisesRegex(ValueError, '当前设备'):
            self.store.update(Opener(Response(self.envelope(value))))

    def test_expired_unbound_or_unversioned_payload_is_rejected(self):
        now = 1_800_000_000
        for key, value in [('expires_at', now), ('device_id', ''), ('schema_version', 2), ('nonce', 'x')]:
            item = payload(now)
            item[key] = value
            with self.assertRaises(ValueError):
                validate_payload(item, now=now)

    def test_admin_keygen_and_sign_round_trip(self):
        private_path = Path(self.tmp.name) / 'private' / 'key'
        public = keygen(private_path)
        payload_path = Path(self.tmp.name) / 'payload.json'
        output = Path(self.tmp.name) / 'subscription.json'
        payload_path.write_text(json.dumps(payload()), encoding='utf-8')
        sign(private_path, payload_path, output)
        self.store.save_url('https://subscription.example.test/value#key=' + public)
        result = self.store.update(Opener(Response(output.read_bytes())))
        self.assertEqual(result['device_id'], local_device_id())


class ClientPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.panel = ClientPanel(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    @patch('server_network_assist.client.change_tunnel')
    def test_signed_static_subscription_cannot_control_management_tunnel(self, change):
        value = payload(); value['lines'][0]['tunnel'] = 'fleet-titan'
        with patch.object(self.panel, 'payload', return_value=value):
            with self.assertRaisesRegex(ValueError, '旧静态'):
                self.panel.connect('line-1')
        change.assert_not_called()

    @patch('server_network_assist.client.change_tunnel')
    def test_static_disconnect_cannot_control_management_tunnel(self, change):
        with self.assertRaisesRegex(ValueError, '所有权'):
            self.panel.disconnect('line-1')
        change.assert_not_called()

    @patch('server_network_assist.client.change_tunnel')
    def test_static_expiry_preserves_unowned_management_tunnel_and_retry_state(self, change):
        self.panel.save_active('line-1', 'fleet-titan')
        with patch.object(self.panel.subscription, 'cached', side_effect=ValueError('订阅已经过期')):
            with self.assertRaisesRegex(RuntimeError, '所有权'):
                self.panel.reconcile()
        change.assert_not_called()
        self.assertIsNotNone(self.panel.active())
        self.assertTrue(self.panel.leaving_path.exists())

    @patch('server_network_assist.client.change_tunnel')
    def test_static_remove_cannot_remove_unowned_management_tunnel(self, change):
        self.panel.save_active('line-1', 'fleet-titan')
        with patch.object(self.panel.subscription, 'remove') as remove:
            with self.assertRaisesRegex(RuntimeError, '所有权'):
                self.panel.remove_subscription()
        remove.assert_not_called(); change.assert_not_called()

    def test_static_update_does_not_clear_online_active_connection(self):
        self.panel.save_active('grant', 'sna0123456789ab', 'online')
        with patch.object(self.panel.subscription, 'cached', return_value=payload()), patch.object(self.panel.subscription, 'update', return_value=payload()):
            self.panel.update_subscription()
        self.assertEqual(self.panel.active()['kind'], 'online')

    @patch('server_network_assist.client.change_tunnel')
    def test_replacing_subscription_does_not_modify_unowned_management_tunnel(self, change):
        value = payload(); value['lines'][0]['tunnel'] = 'fleet-titan'
        self.panel.save_active('line-1', 'fleet-titan')
        with patch.object(self.panel.subscription, 'cached', return_value=value), patch.object(self.panel.subscription, 'save_url') as save:
            with self.assertRaisesRegex(RuntimeError, '所有权'):
                self.panel.replace_subscription('https://new.example.test/sub#key=value')
        change.assert_not_called(); save.assert_not_called()


class ClientHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.panel = ClientPanel(Path(self.tmp.name))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.panel))
        self.panel.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, token=True):
        headers = {'Origin': self.panel.origin}
        if token:
            headers['X-Client-Token'] = self.panel.token
        return self.opener.open(Request(self.panel.origin + path, headers=headers), timeout=5)

    def test_assets_are_offline_and_api_is_authenticated(self):
        with self.request('/', token=False) as response:
            body = response.read().decode()
        self.assertIn('纯享入网', body)
        self.assertNotIn(self.panel.token, body)
        for asset in ('/client.js', '/client.css', '/framework7-bundle.min.js'):
            with self.request(asset, token=False) as response:
                self.assertGreater(len(response.read()), 50)
        with self.assertRaises(HTTPError) as result:
            self.request('/api/state', token=False)
        self.assertEqual(result.exception.code, 403)

    def test_admin_routes_do_not_exist_in_customer_panel(self):
        for path in ('/api/fleet/hosts', '/api/proxy', '/api/campus', '/api/terminal'):
            with self.assertRaises(HTTPError) as result:
                self.request(path)
            self.assertEqual(result.exception.code, 404)

    def test_readonly_access_api_requires_client_authentication(self):
        from unittest.mock import Mock
        self.panel.access_report = Mock(return_value={'ready':False})
        with self.request('/api/network/access') as response:
            self.assertEqual(json.load(response), {'ready':False})
        with self.assertRaises(HTTPError) as result:
            self.request('/api/network/access', token=False)
        self.assertEqual(result.exception.code, 403)
        self.panel.access_report.assert_called_once()

    def test_show_ui_is_authenticated_and_does_not_take_network_lock(self):
        from unittest.mock import Mock
        self.panel.ui_activate = Mock(return_value=True)
        self.panel.lock = Mock()
        self.panel.leave_network = Mock()
        headers = {'X-Client-Token': self.panel.token, 'Origin': self.panel.origin}
        with self.opener.open(Request(self.panel.origin + '/api/ui/show', data=b'{}', headers=headers), timeout=3) as response:
            self.assertEqual(json.load(response), {'shown': True})
        self.panel.ui_activate.assert_called_once()
        self.panel.lock.assert_not_called()
        self.panel.leave_network.assert_not_called()
        for bad in ({'Origin': self.panel.origin}, {'X-Client-Token': self.panel.token},
                    {'X-Client-Token': self.panel.token, 'Origin': 'https://untrusted.example'}):
            with self.assertRaises(HTTPError) as result:
                self.opener.open(Request(self.panel.origin + '/api/ui/show', data=b'{}', headers=bad), timeout=3)
            self.assertEqual(result.exception.code, 403)
        self.panel.ui_activate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
