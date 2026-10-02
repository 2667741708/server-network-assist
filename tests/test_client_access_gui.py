import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from test_client_attachment import network
from server_network_assist import client, client_attachment, client_campus, client_tunnel, client_lan_transport


@pytest.mark.parametrize('ip,status,expected', [
    ('10.20.31.134','offline',True), ('10.20.31.134','online',True),
    ('10.20.31.134','unknown',False), ('192.168.43.2','offline',True)])
def test_access_check_is_readonly_and_uses_source_and_auth_proof(tmp_path, monkeypatch, ip, status, expected):
    panel = client.ClientPanel(tmp_path)
    monkeypatch.setattr(client, 'WINDOWS', True)
    monkeypatch.setattr(client_attachment, 'snapshot', lambda: network(ip))
    monkeypatch.setattr(client_attachment, 'validate_selected_access', Mock())
    monkeypatch.setattr(client_campus, 'authentication', lambda _: status)
    monkeypatch.setattr(panel.online, 'configured', lambda: True)
    monkeypatch.setattr(panel.online, '_record', lambda: {'base_url':'http://10.20.32.13:9182'})
    monkeypatch.setattr(panel.online, 'subscription', lambda: {'routes':[{'id':'g1','name':'source','endpoint':'10.20.32.13:51910'}]})
    from unittest.mock import MagicMock
    monkeypatch.setattr(client_lan_transport, 'connect', Mock(return_value=MagicMock()))
    lease, install, leave = Mock(), Mock(), Mock()
    monkeypatch.setattr(panel.online, 'lease', lease)
    monkeypatch.setattr(client_tunnel, 'install', install)
    monkeypatch.setattr(panel, 'leave_network', leave)
    report = panel.access_report()
    assert report['ready'] is expected
    assert '有效授权数据' in report['service']
    assert 'UDP 握手待' in report['source']
    lease.assert_not_called(); install.assert_not_called(); leave.assert_not_called()


def test_hotspot_can_read_subscription_and_report_campus_source_unreachable(tmp_path, monkeypatch):
    panel=client.ClientPanel(tmp_path)
    monkeypatch.setattr(client,'WINDOWS',True)
    monkeypatch.setattr(client_attachment,'snapshot',lambda:network('192.168.43.2'))
    monkeypatch.setattr(panel.online,'configured',lambda:True)
    monkeypatch.setattr(panel.online,'subscription',lambda:{'routes':[{'id':'g1','name':'source','endpoint':'10.20.32.13:51910'}]})
    monkeypatch.setattr(client_lan_transport,'connect',Mock(side_effect=TimeoutError()))
    report=panel.access_report()
    assert not report['ready'] and report['sources'][0]['reachable'] is False
    assert '有效授权数据' in report['service'] and '可保留热点' in report['source']


def test_background_attachment_probe_has_no_console_window(monkeypatch):
    monkeypatch.setattr(client_attachment, 'os', SimpleNamespace(name='nt'))
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(network())))
    monkeypatch.setattr(client_attachment.subprocess, 'run', run)
    assert client_attachment.snapshot()['supported']
    assert run.call_args.kwargs['creationflags'] == 0x08000000
    assert '-NonInteractive' in run.call_args.args[0]


def test_slow_adapter_query_is_unknown_not_no_network(monkeypatch):
    import subprocess
    monkeypatch.setattr(client_attachment, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(client_attachment.subprocess, 'run', Mock(side_effect=subprocess.TimeoutExpired('probe', 30)))
    with pytest.raises(RuntimeError, match='查询超时'):
        client_attachment.snapshot()


@pytest.mark.parametrize('subscription_fails', [False, True])
def test_ipv6_block_reason_survives_subscription_check(tmp_path, monkeypatch, subscription_fails):
    from unittest.mock import MagicMock
    panel = client.ClientPanel(tmp_path)
    value = network()
    value['links'][0]['defaults6'] = [{'metric': 50, 'gateway': 'fe80::1'}]
    monkeypatch.setattr(client, 'WINDOWS', True)
    monkeypatch.setattr(client_attachment, 'snapshot', lambda: value)
    monkeypatch.setattr(panel.online, 'configured', lambda: True)
    catalog = Mock(side_effect=OSError('subscription unreachable')) if subscription_fails else Mock(
        return_value={'routes': [{'id': 'g1', 'name': 'source', 'endpoint': '10.20.32.13:51910'}]})
    monkeypatch.setattr(panel.online, 'subscription', catalog)
    monkeypatch.setattr(client_lan_transport, 'connect', Mock(return_value=MagicMock()))
    auth, lease, install, leave = Mock(), Mock(), Mock(), Mock()
    monkeypatch.setattr(client_campus, 'authentication', auth)
    monkeypatch.setattr(panel.online, 'lease', lease)
    monkeypatch.setattr(client_tunnel, 'install', install)
    monkeypatch.setattr(panel, 'leave_network', leave)
    report = panel.access_report()
    assert report['ready'] is False
    assert report['access_block']['code'] == 'unconfigured_ipv6_default'
    assert 'IPv6' in report['message'] and 'fe80::1' in report['message']
    assert '10.20.31.134' in report['message']
    auth.assert_not_called(); lease.assert_not_called(); install.assert_not_called(); leave.assert_not_called()


@pytest.mark.parametrize('reason', ['ipv4', 'ipv6'])
def test_start_access_block_precedes_logout_and_lease(tmp_path, monkeypatch, reason):
    from copy import deepcopy
    from server_network_assist import client_dependencies
    panel = client.ClientPanel(tmp_path)
    value = network('172.20.63.249' if reason == 'ipv4' else '10.20.31.134')
    if reason == 'ipv6':
        value['links'][0]['defaults6'] = [{'metric': 50, 'gateway': 'fe80::1'}]
    original = deepcopy(value)
    monkeypatch.setattr(client, 'WINDOWS', True)
    monkeypatch.setattr(client_dependencies, 'require_wireguard', Mock())
    monkeypatch.setattr(client_attachment, 'snapshot', lambda: value)
    auth, logout, lease, install = Mock(), Mock(), Mock(), Mock()
    monkeypatch.setattr(client_lan_transport, 'connect', Mock(side_effect=TimeoutError()))
    monkeypatch.setattr(client_campus, 'authentication', auth)
    monkeypatch.setattr(client_campus, 'logout', logout)
    monkeypatch.setattr(panel.online, 'lease', lease)
    monkeypatch.setattr(client_tunnel, 'install', install)
    with pytest.raises(client_campus.CampusAccessBlocked) as raised:
        panel.online_connect('g1')
    assert raised.value.code == ('reported_campus_path_unreachable' if reason == 'ipv4' else 'unconfigured_ipv6_default')
    assert value == original and panel.active() is None
    auth.assert_not_called(); logout.assert_not_called(); lease.assert_not_called(); install.assert_not_called()
