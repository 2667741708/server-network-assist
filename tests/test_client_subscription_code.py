import base64
import hashlib
import json
from unittest.mock import Mock

import pytest

from server_network_assist.client_online import OnlineServiceClient, parse_enrollment_url
from server_network_assist.client_subscription_code import encode_subscription_code
from test_client_saved_subscriptions import record


def raw_code(payload):
    return 'PURE1-' + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')


@pytest.mark.parametrize('url', [
    'https://service.example.test/#enroll=sample-enrollment-token',
    'http://10.20.32.13:9182/#enroll=sample-enrollment-token',
])
def test_url_and_code_resolve_to_identical_endpoint_and_token(url):
    code = encode_subscription_code(url)
    assert code.startswith('PURE1-') and '#enroll=' not in code
    assert parse_enrollment_url(code) == parse_enrollment_url(url)
    assert parse_enrollment_url(' \n' + code + '\n ') == parse_enrollment_url(url)
    assert encode_subscription_code(code) == code


@pytest.mark.parametrize('value', [
    '', 'PURE1-', 'PURE1-@@', 'PURE1-' + 'a' * 8200,
    raw_code({'v': 2, 'url': 'https://example.test'}),
    raw_code({'v': True, 'url': 'https://example.test'}),
    raw_code({'v': 1, 'url': 12}), raw_code([]),
    raw_code({'v': 1, 'url': 'PURE1-nested'}),
    raw_code({'v': 1, 'url': 'https://example.test', 'extra': 1}),
    raw_code({'v': 1, 'url': 'http://public.example.test/#enroll=sample-enrollment-token'}),
    raw_code({'v': 1, 'url': 'https://user:password@example.test/#enroll=sample-enrollment-token'}),
    raw_code({'v': 1, 'url': 'https://example.test/#enroll=short'}),
    raw_code({'v': 1, 'url': 'https://example.test/client/v1/#enroll=sample-enrollment-token'}),
])
def test_invalid_codes_cannot_replace_identity_or_request_enrollment(tmp_path, value):
    online = OnlineServiceClient(tmp_path)
    online._write_private(online.path, record())
    online._write_private(online.lease_path, {'id': 'existing-lease'})
    identity, lease = online.path.read_bytes(), online.lease_path.read_bytes()
    online._enroll_record = Mock()
    with pytest.raises(ValueError):
        online.add_subscription(value)
    online._enroll_record.assert_not_called()
    assert online.path.read_bytes() == identity and online.lease_path.read_bytes() == lease


def test_url_then_same_code_reuses_device_without_redeeming_token_twice(tmp_path):
    online = OnlineServiceClient(tmp_path)
    url = 'http://10.20.32.13:9182/#enroll=sample-enrollment-token'
    identity = record()
    identity['base_url'] = 'http://10.20.32.13:9182'
    identity['enrollment_token_digest'] = hashlib.sha256(b'sample-enrollment-token').hexdigest()
    online._enroll_record = Mock(return_value=identity)
    first = online.add_subscription(url)
    online._write_private(online.lease_path, {'id': 'existing-lease'})
    before = online.path.read_bytes()
    second = online.add_subscription(encode_subscription_code(url))
    assert second['reused'] and second['subscription_id'] == first['subscription_id']
    online._enroll_record.assert_called_once_with('http://10.20.32.13:9182', 'sample-enrollment-token', None)
    assert online.path.read_bytes() == before and online.active_lease()['id'] == 'existing-lease'


def test_code_first_enrollment_uses_existing_online_path(tmp_path):
    online = OnlineServiceClient(tmp_path)
    url = 'https://service.example.test/#enroll=sample-enrollment-token'
    online._enroll_record = Mock(return_value=record())
    online.add_subscription(encode_subscription_code(url), '订阅码套餐')
    online._enroll_record.assert_called_once_with('https://service.example.test', 'sample-enrollment-token', '订阅码套餐')
    assert online.saved.list()[0]['selected'] is True
