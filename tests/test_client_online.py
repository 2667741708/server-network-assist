import base64
import hashlib
import json
import socket
import ssl
import urllib.error
import io
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
        if path == '/client/v1/subscription':
            return Response({'routes': [{'id': 'grant-1', 'name': '线路'}], 'usage':{}}, request.full_url)
        if path == '/client/v1/usage':
            return Response({'used_bytes': 50, 'remaining_bytes': 950}, request.full_url)
        if path == '/client/v1/lease':
            return Response({'lease': {'id': 'lease-1', 'grant_id': value['grant_id'],
                                       'token': 'stable-lease-token', 'expires_at': 2000}}, request.full_url)
        if path == '/client/v1/lease/renew':
            return Response({'lease': {'id': value['lease_id'],
                                       'token': value.get('current_token'), 'expires_at': 3000}}, request.full_url)
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
        renewed = self.client.renew()
        self.assertEqual(renewed['expires_at'], 3000)
        self.assertEqual(renewed['token'], 'stable-lease-token')
        renew_request = next(value for path, value, _ in self.service.requests
                             if path == '/client/v1/lease/renew')
        self.assertEqual(renew_request['current_token'], 'stable-lease-token')
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

    def test_connection_failures_are_actionable_and_never_store_credentials(self):
        cases = (
            (urllib.error.URLError(TimeoutError('secret-do-not-echo')), '连接超时', '内网订阅入口'),
            (ConnectionRefusedError('secret-do-not-echo'), '连接被拒绝', '监听端口'),
            (urllib.error.URLError(socket.gaierror('secret-do-not-echo')), '域名解析失败', 'DNS'),
            (urllib.error.URLError(ssl.SSLCertVerificationError('secret-do-not-echo')), '证书验证失败', '不会关闭证书验证'),
            (urllib.error.URLError(ssl.SSLError('secret-do-not-echo')), 'TLS 握手失败', 'HTTP/HTTPS'),
        )
        for failure, expected, guidance in cases:
            with self.subTest(expected=expected):
                class Failed:
                    def open(self, request, timeout):
                        raise failure
                client = OnlineServiceClient(Path(self.tmp.name), opener=Failed())
                with self.assertRaises(ValueError) as raised:
                    client.enroll('http://10.20.32.13:9182/#enroll=campus-one-time-secret')
                message = str(raised.exception)
                self.assertIn(expected, message)
                self.assertIn(guidance, message)
                self.assertNotIn('secret', message)
                self.assertNotIn('10.20.32.13', message)
                self.assertFalse(client.path.exists())

    def test_repeating_same_enrollment_refreshes_signed_subscription_without_new_keys(self):
        url='https://service.example.test/#enroll=one-time-secret-123'
        self.client.enroll(url)
        before=self.client.path.read_bytes()
        result=self.client.enroll(url)
        self.assertTrue(result['reused'])
        self.assertEqual(self.client.path.read_bytes(),before)
        self.assertEqual([p for p,_,_ in self.service.requests].count('/client/v1/enroll'),1)
        self.assertEqual(self.service.requests[-1][0],'/client/v1/subscription')

    def test_existing_legacy_registration_and_different_tokens_are_not_overwritten(self):
        url='https://service.example.test/#enroll=one-time-secret-123'
        self.client.enroll(url)
        record=json.loads(self.client.path.read_text())
        record.pop('enrollment_token_digest')
        self.client._write_private(self.client.path,record)
        before=self.client.path.read_bytes()
        for value in [url,'https://service.example.test/#enroll=different-one-time-secret']:
            with self.assertRaisesRegex(ValueError,'已完成订阅注册'):
                self.client.enroll(value)
        self.assertEqual(self.client.path.read_bytes(),before)
        self.assertEqual(len(self.service.requests),1)

    def test_json_error_is_decoded_into_readable_chinese(self):
        class Failed:
            def open(self,request,timeout):
                body=json.dumps({'error':'开户令牌无效、已使用或已过期'}).encode()
                raise urllib.error.HTTPError(request.full_url,400,'bad',{},io.BytesIO(body))
        client=OnlineServiceClient(Path(self.tmp.name),opener=Failed())
        with self.assertRaisesRegex(ValueError,'开户令牌无效、已使用或已过期'):
            client.enroll('https://service.example.test/#enroll=one-time-secret-123')
        self.assertFalse(client.path.exists())


if __name__ == '__main__':
    unittest.main()
