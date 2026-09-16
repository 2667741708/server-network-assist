import io
import json
from pathlib import Path
import uuid

from server_network_assist.client_service_admin import build_parser, run


class FakeStore:
    def __init__(self):
        self.calls = []

    def create_plan(self, *args, **kwargs):
        self.calls.append(('create_plan', args, kwargs))
        return {'id': args[0], 'private_key': 'must-not-leak'}

    def grant_line(self, *args, **kwargs):
        self.calls.append(('grant_line', args, kwargs))
        return {'grant_id': 'grant-1', 'token': 'secret-token', 'nested': {'api_key': 'secret-key'}}

    def revoke(self, *args, **kwargs):
        self.calls.append(('revoke', args, kwargs))

    def customer(self, customer_id):
        return {'id': customer_id, 'enabled': 0}

    def set_customer_enabled(self, *args, **kwargs):
        self.calls.append(('set_customer_enabled', args, kwargs))

    def create_enrollment_token(self, *args, **kwargs):
        self.calls.append(('create_enrollment_token', args, kwargs))
        return 'enr_super-secret'


def test_plan_create_dispatches_limits_and_redacts_secrets():
    store = FakeStore()
    output = io.StringIO()
    code = run([
        'plan', 'create', '--id', 'basic', '--name', '基础套餐',
        '--quota-bytes', '1000', '--download-bps', '200', '--upload-bps', '100',
        '--max-devices', '2', '--lease-seconds', '3600',
    ], store=store, output=output)
    assert code == 0
    assert store.calls == [('create_plan', ('基础套餐',), {
        'plan_id': 'basic', 'quota_bytes': 1000, 'download_bps': 200, 'upload_bps': 100,
        'max_devices': 2, 'lease_seconds': 3600, 'period_seconds': 2592000,
    })]
    value = json.loads(output.getvalue())
    assert value['result']['private_key'] == '[REDACTED]'
    assert 'must-not-leak' not in output.getvalue()


def test_grant_issue_accepts_multiple_lines_and_redacts_nested_tokens():
    store = FakeStore()
    output = io.StringIO()
    code = run([
        'grant', 'issue', '--customer', 'customer-1', '--name', '移动线路',
        '--endpoint', 'relay.example:443', '--tunnel', 'mobile-a', '--expires-at', '2000000000',
    ], store=store, output=output)
    assert code == 0
    assert store.calls[0][0] == 'grant_line'
    assert store.calls[0][1] == ('customer-1', '移动线路', 'mobile-a', 'relay.example:443')
    value = json.loads(output.getvalue())
    assert value['result']['token'] == '[REDACTED]'
    assert value['result']['nested']['api_key'] == '[REDACTED]'


def test_customer_suspend_is_an_explicit_status_operation():
    store = FakeStore()
    output = io.StringIO()
    assert run(['customer', 'suspend', 'customer-1', '--reason', '欠费'],
               store=store, output=output) == 0
    assert store.calls == [('set_customer_enabled', ('customer-1', False), {})]


def test_invalid_zero_rate_is_rejected_by_parser():
    parser = build_parser()
    try:
        parser.parse_args([
            'plan', 'create', '--id', 'bad', '--name', 'bad',
            '--download-bps', '0', '--lease-seconds', '60',
        ])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError('zero rate must be rejected')


def test_enrollment_token_is_written_but_never_printed():
    store = FakeStore()
    output = io.StringIO()
    directory = Path('tests') / ('.admin-token-' + uuid.uuid4().hex)
    destination = directory / 'token.txt'
    try:
        assert run([
            'customer', 'enrollment-token', 'customer-1', '--ttl', '600',
            '--output', str(destination),
        ], store=store, output=output) == 0
        assert destination.read_text(encoding='utf-8').strip() == 'enr_super-secret'
        assert 'enr_super-secret' not in output.getvalue()
        assert json.loads(output.getvalue())['result']['token'] == '[REDACTED]'
    finally:
        if destination.exists():
            destination.unlink()
        if directory.exists():
            directory.rmdir()
