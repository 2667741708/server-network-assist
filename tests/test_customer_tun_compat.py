import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

from server_network_assist import client_tunnel

spec = importlib.util.spec_from_file_location('customer_tun',
    Path(__file__).parents[1] / 'scripts/d408_customer_tun.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


def test_tun_https_checks_retry_transient_failure_but_require_success(monkeypatch):
    responses = iter([SimpleNamespace(returncode=35, stdout='000', stderr='TLS closed'),
                      SimpleNamespace(returncode=0, stdout='200', stderr='')])
    monkeypatch.setattr(manager, 'run', lambda *a, **kw: next(responses))
    monkeypatch.setattr(manager.time, 'sleep', lambda _: None)
    manager.verify_https('https://github.com')


def test_tun_https_checks_do_not_accept_persistent_failure(monkeypatch):
    monkeypatch.setattr(manager, 'run', lambda *a, **kw:
        SimpleNamespace(returncode=35, stdout='000', stderr='TLS closed'))
    monkeypatch.setattr(manager.time, 'sleep', lambda _: None)
    with pytest.raises(RuntimeError, match='TLS closed'):
        manager.verify_https('https://github.com')


def test_tun_policy_marks_clash_outbound_and_preserves_campus():
    rules = manager.rule_plan(manager.DIRECT + ['202.206.240.0/24'])
    assert rules[0] == (10030, ['fwmark', '0xd40a', 'lookup', 'main'])
    assert rules[-1] == (10060, ['lookup', '20482'])
    assert len(set(p for p, _ in rules)) == len(rules)
    assert any('202.206.240.0/24' in selectors for _, selectors in rules)
    # Fake IP addresses MUST enter Clash instead of bypassing it with LAN traffic.
    assert not any('198.18.0.0/15' in selectors for _, selectors in rules)


def test_disconnect_stops_owned_clash_tun_before_wireguard(monkeypatch):
    class Marker:
        def exists(self): return True
        def read_text(self, **kwargs):
            return '{"service":"d408-mihomo-tun.service","tunnel":"sna0123456789ab"}'
        def stat(self): return SimpleNamespace(st_uid=0, st_mode=0o100600)
    monkeypatch.setattr(client_tunnel, 'Path', lambda *_: Marker())
    monkeypatch.setattr(client_tunnel.os, 'name', 'posix')
    calls = []
    monkeypatch.setattr(client_tunnel, '_run', calls.append)
    client_tunnel.stop('sna0123456789ab')
    assert calls == [
        ['systemctl', 'stop', '--', 'd408-mihomo-tun.service'],
        ['systemctl', 'stop', '--', 'wg-quick@sna0123456789ab.service']]


def test_disconnect_does_not_stop_another_customers_tun(monkeypatch):
    class Marker:
        def exists(self): return True
        def read_text(self, **kwargs):
            return '{"service":"d408-mihomo-tun.service","tunnel":"snaaaaaaaaaaaaa"}'
    monkeypatch.setattr(client_tunnel, 'Path', lambda *_: Marker())
    monkeypatch.setattr(client_tunnel.os, 'name', 'posix')
    calls = []
    monkeypatch.setattr(client_tunnel, '_run', calls.append)
    client_tunnel.stop('sna0123456789ab')
    assert calls == [['systemctl', 'stop', '--', 'wg-quick@sna0123456789ab.service']]
