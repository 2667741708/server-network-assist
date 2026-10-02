from unittest.mock import MagicMock, Mock
import pytest
from test_client_attachment import network
from server_network_assist import client, client_attachment, client_campus, client_lan_transport


@pytest.mark.parametrize('ip', ['10.30.158.28', '172.20.63.249', '192.168.43.2', '100.64.1.28'])
def test_ipv4_prefix_never_denies_physically_verified_source(monkeypatch, ip):
    value = network(ip)
    probe = Mock(return_value=MagicMock())
    monkeypatch.setattr(client_lan_transport, 'connect', probe)
    assert client_campus.campus_link(value) == value['links'][0]
    probe.assert_called_once_with('10.20.32.13', 9182, value['links'][0])


@pytest.mark.parametrize('ip', ['192.168.43.2', '172.20.63.249'])
def test_source_query_ignores_admission_and_never_changes_network(tmp_path, monkeypatch, ip):
    panel = client.ClientPanel(tmp_path)
    value = network(ip)
    value['links'][0]['defaults6'] = [{'metric': 50, 'gateway': 'fe80::1'}]
    monkeypatch.setattr(client, 'WINDOWS', True)
    monkeypatch.setattr(client_attachment, 'snapshot', lambda: value)
    monkeypatch.setattr(panel.online, 'configured', lambda: True)
    monkeypatch.setattr(panel.online, 'subscription', lambda: {'routes': [
        {'id': 'g1', 'name': 'physical', 'endpoint': '10.20.32.13:51910', 'available': True, 'egress_mode': 'physical'},
        {'id': 'g2', 'name': 'proxy', 'endpoint': '10.20.32.13:51910', 'available': False, 'egress_mode': 'source_proxy'}]})
    probe = Mock(return_value=MagicMock())
    monkeypatch.setattr(client_lan_transport, 'connect', probe)
    forbidden = Mock(side_effect=AssertionError('query must not change network or inspect personal account'))
    for target, attribute in [(panel.online, 'lease'), (panel, 'leave_network'),
                              (client_campus, 'authentication'), (client_campus, 'logout'), (client_campus, 'campus_link')]:
        monkeypatch.setattr(target, attribute, forbidden)
    report = panel.access_report(check_access=False)
    assert not report['ready']
    assert all(row['reachable'] for row in report['sources'])
    assert report['sources'][0]['egress_mode'] == 'physical'
    assert report['sources'][1]['egress_mode'] == 'source_proxy'
    assert report['sources'][1]['available'] is False
    assert report['sources'][0]['latency_ms'] >= 0
    probe.assert_called_once()
    forbidden.assert_not_called()


def test_unreachable_source_preserves_original_network(tmp_path, monkeypatch):
    panel = client.ClientPanel(tmp_path)
    monkeypatch.setattr(client, 'WINDOWS', True)
    monkeypatch.setattr(client_attachment, 'snapshot', lambda: network('192.168.1.2'))
    monkeypatch.setattr(panel.online, 'configured', lambda: True)
    monkeypatch.setattr(panel.online, 'subscription', lambda: {'routes': [{'id': 'g1', 'name': 'source', 'endpoint': '10.20.32.13:51910'}]})
    monkeypatch.setattr(client_lan_transport, 'connect', Mock(side_effect=TimeoutError()))
    report = panel.access_report(check_access=False)
    assert report['sources'][0]['reachable'] is False and not report['ready']
    assert panel.active() is None
