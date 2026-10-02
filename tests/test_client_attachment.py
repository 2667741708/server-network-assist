from copy import deepcopy
from unittest.mock import Mock
import pytest
from server_network_assist import client_attachment as attachment
from server_network_assist import client_campus, client_original_network, client_app_routing
from server_network_assist.client import ClientPanel
from server_network_assist.client_native_tray import NativeTray


@pytest.fixture(autouse=True)
def physical_probe_is_offline_by_default(monkeypatch):
    from server_network_assist import client_lan_transport
    monkeypatch.setattr(client_lan_transport, 'connect', Mock(side_effect=TimeoutError()))


def network(ip='10.20.31.134', index=16, profile='campus', metric=100):
    return {'supported': True, 'links': [{'id': index, 'connected': True,
        'addresses': [ip], 'profile': profile, 'defaults': [{'metric': metric, 'gateway': '10.20.28.1'}]}]}


def test_selected_hotspot_rejects_campus_even_with_second_campus_adapter():
    v = network('192.168.43.2', 17, 'hotspot', 10)
    v['links'] += network()['links']
    assert attachment.preferred(v)[0]['id'] == 17
    with pytest.raises(ValueError, match='不是已配置的校园'):
        client_campus.campus_link(v)


def test_verified_dormitory_prefix_is_supported_without_ssid_trust():
    value = network('10.126.63.249', profile='arbitrary name')
    assert client_campus.campus_link(value)['addresses'] == ['10.126.63.249']


@pytest.mark.parametrize('ip', ['172.20.128.2', '192.168.63.249', '192.168.43.2'])
def test_campus_ssid_does_not_authorize_unconfigured_subnets(ip):
    with pytest.raises(ValueError, match='不是已配置的校园'):
        client_campus.campus_link(network(ip, profile='iYanDa-Dormitory'))


@pytest.mark.parametrize('value,reason', [({'supported': True, 'links': []}, 'attachment_lost'),
    (network('192.168.43.2', 17, 'hotspot'), 'attachment_lost'),
    (network(profile='different'), 'network_changed')])
def test_change_leaves_and_notifies_without_editing_physical_network(tmp_path, monkeypatch, value, reason):
    p = ClientPanel(tmp_path)
    p.attachment = network()
    p.save_active('grant', 'sna123456789abc', 'online')
    leave = Mock(side_effect=p.clear_active)
    monkeypatch.setattr(p, 'leave_network', leave)
    p.observe_attachment(value)
    leave.assert_called_once()
    assert p.active() is None and p.events[-1]['code'] == reason and p.events[-1]['error']


def test_recovery_failure_is_visible_and_keeps_retry_state(tmp_path, monkeypatch):
    p = ClientPanel(tmp_path)
    p.attachment = network()
    monkeypatch.setattr(p, 'leave_network', Mock(side_effect=RuntimeError('failure')))
    p.observe_attachment({'supported': True, 'links': []})
    assert p.events[-1]['code'] == 'recovery_failed'


def test_notification_failure_cannot_block_primary_operation(tmp_path):
    p = ClientPanel(tmp_path)
    p.event_callbacks.append(Mock(side_effect=RuntimeError('tray failed')))
    assert p.publish_event('test', 'visible')['sequence'] == 1


def test_tray_exit_does_not_destroy_window_when_recovery_fails():
    tray = NativeTray.__new__(NativeTray)
    tray.window = Mock()
    close = Mock(return_value=False)
    tray.quit(close)
    tray.window.destroy.assert_not_called()
    close.return_value = None
    tray.quit(close)
    tray.window.destroy.assert_called_once()


def test_address_order_is_not_a_network_change():
    a = network(); a['links'][0]['addresses'].append('10.20.31.135')
    b = deepcopy(a); b['links'][0]['addresses'].reverse()
    assert attachment.change_reason(a, b) is None


def test_unknown_account_blocks_borrow_before_lease_and_logout(tmp_path, monkeypatch):
    from server_network_assist import client
    p = ClientPanel(tmp_path)
    monkeypatch.setattr(client, 'WINDOWS', True)
    monkeypatch.setattr(attachment, 'snapshot', lambda: network())
    monkeypatch.setattr(attachment, 'validate_selected_access', Mock())
    monkeypatch.setattr(client_campus, 'authentication', lambda _: 'unknown')
    logout = Mock()
    monkeypatch.setattr(client_campus, 'logout', logout)
    monkeypatch.setattr(p.online, '_record', lambda: {'base_url': 'http://10.20.32.13:9182'})
    monkeypatch.setattr(p.online, 'lease', Mock())
    with pytest.raises(ValueError, match='无法确认'):
        p.online_connect('grant')
    p.online.lease.assert_not_called()
    logout.assert_not_called()


def test_verified_campus_redirect_identifies_offline_without_contacting_probe(monkeypatch):
    c = Mock()
    response = Mock(status=302)
    response.getheaders.return_value = []
    response.getheader.return_value = 'http://124.124.124.124/'
    c.getresponse.return_value = response
    factory = Mock(return_value=c)
    monkeypatch.setattr(client_campus, 'BoundHTTPS', factory)
    assert client_campus.authentication(network()) == 'offline'
    factory.assert_called_once_with('auth1.ysu.edu.cn', network()['links'][0])
    c.close.assert_called_once()


def test_critical_alert_shows_window_even_if_tray_balloon_fails():
    tray = NativeTray.__new__(NativeTray)
    tray.window = Mock(); tray.icon = Mock()
    tray.icon.notify.side_effect = RuntimeError('notifications disabled')
    tray._display_event({'code': 'attachment_lost', 'message': 'Disconnected', 'error': True})
    tray.window.show.assert_called_once()


def test_ipv6_hotspot_addition_yields_and_cannot_borrow():
    before=network()
    after=deepcopy(before)
    after['links'].append({'id':17,'connected':True,'addresses':[],
        'addresses6':['2001:db8::42'],'profile':'hotspot','defaults':[],
        'defaults6':[{'metric':10,'gateway':'fe80::1'}]})
    assert attachment.change_reason(before,after)=='network_changed'
    with pytest.raises(ValueError,match='不是已配置的校园'):
        client_campus.campus_link(after)


@pytest.mark.parametrize('metric', [10, 1000])
def test_any_hotspot_default_blocks_borrow_regardless_of_family_metric(metric):
    value = network()
    value['links'] += network('192.168.43.2', 17, 'hotspot', metric)['links']
    with pytest.raises(ValueError, match='无法直达源网'):
        client_campus.campus_link(value)
    value['links'][-1]['defaults'] = []
    value['links'][-1]['defaults6'] = [{'metric': metric, 'gateway': 'fe80::1'}]
    with pytest.raises(ValueError, match='IPv6'):
        client_campus.campus_link(value)


def test_unconfigured_campus_address_is_not_declared_a_hotspot():
    value = network('172.20.63.249', profile='iYanDa-Dormitory')
    with pytest.raises(client_campus.CampusAccessBlocked) as raised:
        client_campus.campus_link(value)
    error = raised.value
    assert error.code == 'reported_campus_path_unreachable'
    assert '172.20.63.249' in str(error) and '10.20.28.1' in str(error)
    assert '不能仅凭地址认定为热点' in str(error)
    assert error.details['profile'] == 'iYanDa-Dormitory'


def test_campus_ipv6_default_has_distinct_reason_without_relaxing_guard():
    value = network()
    value['links'][0]['defaults6'] = [{'metric': 100, 'gateway': 'fe80::1'}]
    with pytest.raises(client_campus.CampusAccessBlocked) as raised:
        client_campus.campus_link(value)
    assert raised.value.code == 'unconfigured_ipv6_default'
    assert 'fe80::1' in str(raised.value)
    assert '这不代表你连接了热点' in str(raised.value)


def test_reported_address_requires_physical_source_probe(monkeypatch):
    from unittest.mock import MagicMock
    from server_network_assist import client_lan_transport
    value = network('10.30.158.28')
    value['links'][0]['defaults'][0]['gateway'] = '10.30.156.1'
    probe = Mock(return_value=MagicMock())
    monkeypatch.setattr(client_lan_transport, 'connect', probe)
    assert client_campus.campus_link(value) == value['links'][0]
    probe.assert_called_once_with('10.20.32.13', 9182, value['links'][0])
    probe.side_effect = TimeoutError()
    with pytest.raises(client_campus.CampusAccessBlocked) as raised:
        client_campus.campus_link(value)
    assert raised.value.code == 'reported_campus_path_unreachable'


@pytest.mark.parametrize('ip,gateway', [('172.20.160.1','10.30.156.1'), ('192.168.155.28','10.30.156.1')])
def test_reported_pair_does_not_open_inferred_subnet(monkeypatch, ip, gateway):
    from server_network_assist import client_lan_transport
    value = network(ip)
    value['links'][0]['defaults'][0]['gateway'] = gateway
    probe = Mock(side_effect=TimeoutError())
    monkeypatch.setattr(client_lan_transport, 'connect', probe)
    with pytest.raises(client_campus.CampusAccessBlocked, match='无法直达源网'):
        client_campus.campus_link(value)
    probe.assert_called_once_with('10.20.32.13', 9182, value['links'][0])


@pytest.mark.parametrize('ip', ['10.1.1.28','10.30.158.28','10.126.128.2','10.127.63.249','10.254.254.28'])
def test_operator_authorized_10_range_requires_source_proof(monkeypatch, ip):
    from unittest.mock import MagicMock
    from server_network_assist import client_lan_transport
    value = network(ip)
    value['links'][0]['defaults'][0]['gateway'] = '10.30.0.1'
    probe = Mock(return_value=MagicMock())
    monkeypatch.setattr(client_lan_transport, 'connect', probe)
    assert client_campus.campus_link(value) == value['links'][0]
    probe.assert_called_once_with('10.20.32.13', 9182, value['links'][0])


def test_reported_address_does_not_bypass_secondary_hotspot_or_ipv6(monkeypatch):
    from unittest.mock import MagicMock
    from server_network_assist import client_lan_transport
    value = network('10.30.158.28')
    value['links'][0]['defaults'][0]['gateway'] = '10.30.156.1'
    value['links'] += network('192.168.43.2', index=17)['links']
    probe = Mock(side_effect=[MagicMock(), TimeoutError()])
    monkeypatch.setattr(client_lan_transport, 'connect', probe)
    with pytest.raises(client_campus.CampusAccessBlocked, match='无法直达源网'):
        client_campus.campus_link(value)
    assert probe.call_count == 2
    probe.reset_mock()
    value['links'].pop()
    value['links'][0]['defaults6'] = [{'gateway':'fe80::1','metric':100}]
    with pytest.raises(client_campus.CampusAccessBlocked, match='IPv6'):
        client_campus.campus_link(value)
    probe.assert_not_called()


def test_secondary_10_hotspot_is_checked_even_when_original_campus_is_preferred(monkeypatch):
    from server_network_assist import client_lan_transport
    value = network()
    value['links'] += network('10.50.1.2', index=17, profile='personal hotspot', metric=1000)['links']
    probe = Mock(side_effect=TimeoutError())
    monkeypatch.setattr(client_lan_transport, 'connect', probe)
    assert attachment.preferred(value)[0]['id'] == 16
    with pytest.raises(client_campus.CampusAccessBlocked) as raised:
        client_campus.campus_link(value)
    assert raised.value.code == 'reported_campus_path_unreachable'
    assert raised.value.details['id'] == 17
    probe.assert_called_once_with('10.20.32.13', 9182, value['links'][1])


@pytest.mark.parametrize('family', ['defaults', 'defaults6'])
def test_new_default_on_existing_link_yields_even_when_campus_stays_preferred(family):
    before = network()
    before['links'].append({'id':17, 'connected':True, 'addresses':['192.168.43.2'],
        'addresses6':['2001:db8::42'], 'profile':'hotspot', 'defaults':[], 'defaults6':[]})
    after = deepcopy(before)
    after['links'][-1][family] = [{'metric':1000, 'gateway':'192.168.43.1' if family == 'defaults' else 'fe80::1'}]
    assert attachment.preferred(after)[0]['id'] == 16
    assert attachment.change_reason(before, after) == 'network_changed'
