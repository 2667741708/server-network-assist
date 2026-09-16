import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from server_network_assist.client_crypto import b64url_decode, verify_payload
from server_network_assist.client_online import OnlineServiceClient, parse_enrollment_url


class Response:
    def __init__(self, value, url):
        self.value = value
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return json.dumps(self.value).encode()


class Service:
    def __init__(self):
        self.requests = []
        self.signing_public = None

    def open(self, request, timeout):
        body = request.data or b''
        path = request.full_url.removeprefix('https://service.example.test')
        value = json.loads(body or b'{}')
        self.requests.append((path, value, dict(request.headers)))
        if path == '/client/v1/enroll':
            self.assert_enrollment(value)
            return Response({'device_id': 'dev-1', 'customer_id': 'customer-1'}, request.full_url)
        headers = {key.lower(): item for key, item in request.headers.items()}
        signed = {'method': request.method, 'path': path,
                  'timestamp': int(headers['x-device-timestamp']), 'nonce': headers['x-device-nonce'],
                  'body_sha256': hashlib.sha256(body).hexdigest()}
        if not verify_payload(self.signing_public, signed, headers['x-device-signature']):
            raise AssertionError('signature did not verify')
        if path == '/client/v1/routes':
            return Response({'routes': [{'id': 'grant-1', 'name': '线路'}]}, request.full_url)
        if path == '/client/v1/usage':
            return Response({'used_bytes': 50, 'remaining_bytes': 950}, request.full_url)
        if path == '/client/v1/lease':
            return Response({'lease': {'id': 'lease-1', 'grant_id': value['grant_id'], 'expires_at': 2000}}, request.full_url)
        if path == '/client/v1/lease/renew':
            return Response({'lease': {'id': value['lease_id'], 'expires_at': 3000}}, request.full_url)
        if path == '/client/v1/lease/release':
            return Response({'ok': True}, request.full_url)
        raise AssertionError(path)

    def assert_enrollment(self, value):
        self.signing_public = value['signing_public_key']
        Ed25519PublicKey.from_public_bytes(b64url_decode(self.signing_public))
        self.assert_wireguard(value['wireguard_public_key'])

    @staticmethod
    def assert_wireguard(value):
        if len(base64.b64decode(value)) != 32:
            raise AssertionError('invalid WireGuard public key')


class OnlineClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = Service()
        self.client = OnlineServiceClient(Path(self.tmp.name), opener=self.service, clock=lambda: 1000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_https_enrollment_url_keeps_token_in_fragment(self):
        base, token = parse_enrollment_url('https://service.example.test/#enroll=one-time-secret-123')
        self.assertEqual(base, 'https://service.example.test')
        self.assertEqual(token, 'one-time-secret-123')
        private_base, private_token = parse_enrollment_url(
            'http://10.20.32.13:9182/#enroll=campus-one-time-token')
        self.assertEqual(private_base, 'http://10.20.32.13:9182')
        self.assertEqual(private_token, 'campus-one-time-token')
        for value in ('http://service.example.test/#enroll=one-time-secret-123',
                      'https://service.example.test/?token=x#enroll=one-time-secret-123'):
            with self.assertRaises(ValueError):
                parse_enrollment_url(value)

    def test_enroll_sign_routes_and_full_lease_lifecycle(self):
        result = self.client.enroll('https://service.example.test/#enroll=one-time-secret-123', 'd321')
        self.assertEqual(result['device_id'], 'dev-1')
        record = json.loads(self.client.path.read_text(encoding='utf-8'))
        self.assertNotIn('one-time-secret', json.dumps(record))
        self.assertEqual(self.client.routes()[0]['id'], 'grant-1')
        self.assertEqual(self.client.usage()['remaining_bytes'], 950)
        lease = self.client.lease('grant-1')
        self.assertEqual(lease['id'], 'lease-1')
        self.assertEqual(self.client.renew()['expires_at'], 3000)
        self.client.release()
        self.assertIsNone(self.client.active_lease())
        nonces = [headers['X-device-nonce'] for path, _, headers in self.service.requests if path != '/client/v1/enroll']
        self.assertEqual(len(nonces), len(set(nonces)))

    def test_failed_enrollment_does_not_persist_private_keys(self):
        class Failed:
            def open(self, request, timeout):
                raise OSError('offline')
        client = OnlineServiceClient(Path(self.tmp.name), opener=Failed())
        with self.assertRaisesRegex(ValueError, '无法连接'):
            client.enroll('https://service.example.test/#enroll=one-time-secret-123')
        self.assertFalse(client.path.exists())


if __name__ == '__main__':
    unittest.main()
