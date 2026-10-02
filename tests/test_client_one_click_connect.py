from unittest.mock import MagicMock, Mock

import pytest
from test_client_attachment import network
from server_network_assist import client, client_attachment, client_campus, client_lan_transport, client_tunnel


@pytest.fixture
def setup_panel(tmp_path, monkeypatch):
    panel = client.ClientPanel(tmp_path)
    attachment = network('10.126.63.249')
    calls = []
    monkeypatch.setattr(client, 'WINDOWS', True)
    monkeypatch.setattr(client_attachment, 'snapshot', Mock(return_value=attachment))
    monkeypatch.setattr(client_attachment, 'validate_selected_access', Mock())
    monkeypatch.setattr(panel.online, '_record', lambda: {
        'base_url': 'http://10.20.32.13:9182', 'wireguard_private_key': 'fixture-only'})
    monkeypatch.setattr(panel.online, 'subscription', Mock(return_value={
        'routes': [{'id': 'grant-1', 'endpoint': '10.20.32.13:51910'}]}))
    monkeypatch.setattr(client_campus, 'authentication', Mock(side_effect=['online', 'offline']))

    def logout(_):
        calls.append('logout')
        return {'ok': True, 'after': 'offline', 'logout_requested': True}

    def lease(_):
        calls.append('lease')
        return {'id': 'fixture-lease', 'endpoint': '10.20.32.13:51910'}

    def install(*_):
        calls.append('install')
        return 'fixture-tunnel'

    monkeypatch.setattr(client_campus, 'logout', Mock(side_effect=logout))
    monkeypatch.setattr(panel.online, 'lease', Mock(side_effect=lease))
    monkeypatch.setattr(client_tunnel, 'install', Mock(side_effect=install))
    monkeypatch.setattr(client_lan_transport, 'resolve_ipv4', Mock(return_value=['10.20.32.13']))
    monkeypatch.setattr(client_lan_transport, 'connect', Mock(return_value=MagicMock()))
    monkeypatch.setattr(panel, 'leave_network', Mock())
    return panel, calls


def test_start_logs_out_then_leases_then_installs(setup_panel):
    panel, calls = setup_panel
    assert panel.online_connect('grant-1')['ok']
    assert calls == ['logout', 'lease', 'install']
    assert client_lan_transport.connect.call_count == 2
    assert panel.active()['kind'] == 'online'
    with pytest.raises(ValueError, match='先退出'):
        panel.online_connect('grant-1')
    assert calls == ['logout', 'lease', 'install']


def test_offline_start_does_not_logout(setup_panel, monkeypatch):
    panel, calls = setup_panel
    monkeypatch.setattr(client_campus, 'authentication', Mock(return_value='offline'))
    panel.online_connect('grant-1')
    assert calls == ['lease', 'install']


@pytest.mark.parametrize('failure', ['logout-error', 'not-offline', 'unknown-after', 'changed-network', 'lan-blocked'])
def test_logout_failure_or_lan_change_never_creates_lease(setup_panel, monkeypatch, failure):
    panel, _ = setup_panel
    if failure == 'logout-error':
        monkeypatch.setattr(client_campus, 'logout', Mock(side_effect=ValueError('Logout 未确认成功')))
    elif failure == 'not-offline':
        monkeypatch.setattr(client_campus, 'logout', Mock(return_value={'ok': True, 'after': 'online'}))
    elif failure == 'unknown-after':
        monkeypatch.setattr(client_campus, 'authentication', Mock(side_effect=['online', 'unknown']))
    elif failure == 'changed-network':
        monkeypatch.setattr(client_attachment, 'snapshot', Mock(side_effect=[network('10.126.63.249'), network('192.168.43.2')]))
    elif failure == 'lan-blocked':
        monkeypatch.setattr(client_lan_transport, 'connect', Mock(side_effect=[MagicMock(), TimeoutError()]))
    with pytest.raises(ValueError, match='Logout 后校园私网源网不可达' if failure == 'lan-blocked' else '.'):
        panel.online_connect('grant-1')
    panel.online.lease.assert_not_called()
    client_tunnel.install.assert_not_called()
    assert panel.active() is None


@pytest.mark.parametrize('failure', ['invalid-grant', 'service-error', 'source-blocked'])
def test_check_authorization_and_source_before_logout(setup_panel, monkeypatch, failure):
    panel, _ = setup_panel
    if failure == 'invalid-grant':
        monkeypatch.setattr(panel.online, 'subscription', Mock(return_value={'routes': []}))
    elif failure == 'service-error':
        monkeypatch.setattr(panel.online, 'subscription', Mock(side_effect=ValueError('订阅不可达')))
    else:
        monkeypatch.setattr(client_lan_transport, 'connect', Mock(side_effect=TimeoutError()))
    with pytest.raises((ValueError, OSError)):
        panel.online_connect('grant-1')
    client_campus.logout.assert_not_called()
    panel.online.lease.assert_not_called()


@pytest.mark.parametrize('stage', ['lease', 'install'])
def test_failure_after_logout_reports_personal_account_offline_and_cleans_up(setup_panel, monkeypatch, stage):
    panel, _ = setup_panel
    target = panel.online if stage == 'lease' else client_tunnel
    monkeypatch.setattr(target, stage, Mock(side_effect=ValueError('fixture failure')))
    with pytest.raises(RuntimeError, match='个人校园账号已退出，但入网失败'):
        panel.online_connect('grant-1')
    panel.leave_network.assert_called_once()
    assert panel.active() is None
