import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import Mock

from server_network_assist import client_tunnel
from server_network_assist import client_original_network


@pytest.fixture(autouse=True)
def isolate_operator_proxy(monkeypatch):
    monkeypatch.setattr(client_original_network, 'capture', Mock())
    monkeypatch.setattr(client_original_network, 'restore', Mock())


def test_peer_identity_and_recent_handshake_are_required(monkeypatch):
    monkeypatch.setattr(client_tunnel, 'os', SimpleNamespace(name='posix'))
    monkeypatch.setattr(client_tunnel.time, 'time', lambda: 2000)
    monkeypatch.setattr(client_tunnel.time, 'sleep', lambda _: None)
    replies = iter(['foreign-peer\t1999\nexpected\t0', 'expected\t1000', 'expected\t1999'])
    calls = []
    def execute(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=next(replies))
    monkeypatch.setattr(client_tunnel.subprocess, 'run', execute)
    client_tunnel.wait_handshake('sna0123456789ab', 'expected')
    assert len(calls) == 3
    assert all(argv == ['wg', 'show', 'sna0123456789ab', 'latest-handshakes'] for argv in calls)


def test_unreachable_source_does_not_report_connected(monkeypatch):
    monkeypatch.setattr(client_tunnel, 'os', SimpleNamespace(name='posix'))
    monkeypatch.setattr(client_tunnel.time, 'sleep', lambda _: None)
    clock = iter([0, 0, 31])
    monkeypatch.setattr(client_tunnel.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(client_tunnel.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout='expected\t0'))
    with pytest.raises(ValueError, match='源网未完成'):
        client_tunnel.wait_handshake('sna0123456789ab', 'expected')


def test_failed_handshake_stops_only_new_customer_tunnel(tmp_path, monkeypatch):
    monkeypatch.setattr(client_tunnel, 'os', SimpleNamespace(name='posix', geteuid=lambda: 0))
    original_path = Path
    monkeypatch.setattr(client_tunnel, 'Path', lambda p: tmp_path / 'wg' if str(p) == '/etc/wireguard' else original_path(p))
    calls = []
    monkeypatch.setattr(client_tunnel, '_run', calls.append)
    def unreachable(*a, **kw):
        raise ValueError('源网未完成 WireGuard 握手')
    monkeypatch.setattr(client_tunnel, 'wait_handshake', unreachable)
    key = base64.b64encode(b'x' * 32).decode()
    lease = {'grant_id': 'test-grant', 'endpoint': '10.20.32.13:51910',
             'allocated_address': '10.213.40.7/32', 'relay_public_key': key,
             'allowed_ips': '0.0.0.0/0'}
    with pytest.raises(ValueError, match='源网未完成'):
        client_tunnel.install(tmp_path, lease, key)
    assert len(calls) == 2
    assert calls[0][1] == 'start' and calls[1][1] == 'stop'
    assert calls[0][-1] == calls[1][-1] and 'wg-quick@sna' in calls[1][-1]


def test_rollback_failure_is_reported_instead_of_claiming_restoration(tmp_path, monkeypatch):
    monkeypatch.setattr(client_tunnel, 'os', SimpleNamespace(name='posix', geteuid=lambda: 0))
    original_path = Path
    monkeypatch.setattr(client_tunnel, 'Path', lambda p: tmp_path / 'wg' if str(p) == '/etc/wireguard' else original_path(p))
    def execute(argv):
        if argv[1] == 'stop':
            raise RuntimeError('stop failed')
    monkeypatch.setattr(client_tunnel, '_run', execute)
    def unreachable(*a, **kw):
        raise ValueError('no handshake')
    monkeypatch.setattr(client_tunnel, 'wait_handshake', unreachable)
    key = base64.b64encode(b'x' * 32).decode()
    with pytest.raises(RuntimeError, match='自动回退也未完成'):
        client_tunnel.install(tmp_path, {'grant_id': 'test', 'endpoint': '10.20.32.13:51910',
            'allocated_address': '10.213.40.7/32', 'relay_public_key': key,
            'allowed_ips': '0.0.0.0/0'}, key)


def test_partial_service_install_failure_still_attempts_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(client_tunnel, 'os', SimpleNamespace(name='posix', geteuid=lambda: 0))
    original_path = Path
    monkeypatch.setattr(client_tunnel, 'Path', lambda p: tmp_path / 'wg' if str(p) == '/etc/wireguard' else original_path(p))
    calls = []
    def execute(argv):
        calls.append(argv)
        if argv[1] == 'start':
            raise RuntimeError('partial activation')
    monkeypatch.setattr(client_tunnel, '_run', execute)
    key = base64.b64encode(b'x' * 32).decode()
    with pytest.raises(RuntimeError, match='partial activation'):
        client_tunnel.install(tmp_path, {'grant_id': 'test', 'endpoint': '10.20.32.13:51910',
            'allocated_address': '10.213.40.7/32', 'relay_public_key': key,
            'allowed_ips': '0.0.0.0/0'}, key)
    assert [x[1] for x in calls] == ['start', 'stop']
    assert not (tmp_path / client_tunnel.OWNED).exists()
    assert not list((tmp_path / 'wg').glob('*.conf'))
    client_original_network.restore.assert_called_once_with(tmp_path)
