import json
import ipaddress
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from server_network_assist import client_clash_coexist as bridge
from server_network_assist.client_tunnel import split_allowed_ips


def test_campus_direct_and_local_fake_ip_have_distinct_routes():
    document = {'tun': {'route-exclude-address': ['10.20.0.0/16']},
                'dns': {'enhanced-mode': 'fake-ip', 'fake-ip-range': '198.18.0.1/16'}}
    direct = ['202.206.240.0/24', '198.18.0.0/15']
    public = [x.strip() for x in split_allowed_ips('0.0.0.0/0', direct).split(',')]
    fields = bridge.managed_fields(document, 'sna0123456789ab', public, direct)
    assert '202.206.240.0/24' in fields['tun.route-exclude-address']
    assert '198.18.0.0/15' not in fields['tun.route-exclude-address']
    assert '198.18.0.0/16' in fields['tun.route-address']
    for address in ['10.20.32.12', '10.201.250.1', '172.16.1.1', '202.206.240.1']:
        assert not any(ipaddress.ip_address(address) in ipaddress.ip_network(x)
                       for x in fields['tun.route-address'])
    assert fields['interface-name'] == 'sna0123456789ab'


@pytest.fixture
def core(tmp_path, monkeypatch):
    path = tmp_path / 'clash.yaml'
    original = {'mode': 'rule', 'proxies': [{'name': 'customer-airport', 'type': 'ss'}],
                'rules': ['MATCH,customer-airport'],
                'tun': {'enable': False, 'auto-route': True, 'auto-detect-interface': True},
                'dns': {'enable': True, 'nameserver': ['223.5.5.5']}}
    path.write_text(yaml.safe_dump(original), encoding='utf-8')
    data = tmp_path / 'data'
    data.mkdir()
    runtime = {'tun': {'enable': True, 'device': 'Mihomo'}}
    calls = []
    def api(base, secret, method, uri, body=None):
        calls.append((method, uri))
        return runtime if method == 'GET' else {}
    monkeypatch.setattr(bridge, 'controller', lambda: (path, 'pipe', 'hidden', runtime))
    monkeypatch.setattr(bridge.clash_control, 'candidates', lambda: [path])
    monkeypatch.setattr(bridge.clash_control, 'controller', lambda p: ('pipe', 'hidden'))
    monkeypatch.setattr(bridge.clash_control, 'api', api)
    return SimpleNamespace(path=path, data=data, original=original, runtime=runtime, calls=calls)


def test_software_restart_does_not_reload_clash_or_replace_airport(core):
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    document = yaml.safe_load(core.path.read_text(encoding='utf-8'))
    assert document['proxies'] == core.original['proxies']
    assert document['rules'] == core.original['rules']
    assert document['mode'] == 'rule' and document['tun']['enable'] is True
    core.calls.clear()
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    assert not core.calls
    state = (core.data / bridge.STATE).read_text(encoding='utf-8')
    assert 'hidden' not in state and 'customer-airport' not in state


def test_disconnect_restores_owned_fields_preserving_user_rules_and_toggle(core):
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    document = yaml.safe_load(core.path.read_text(encoding='utf-8'))
    document['mode'] = 'global'
    document['dns']['fallback'] = ['tls://9.9.9.9']
    core.path.write_text(yaml.safe_dump(document), encoding='utf-8')
    bridge.restore(core.data, 'sna0123456789ab')
    restored = yaml.safe_load(core.path.read_text(encoding='utf-8'))
    assert 'interface-name' not in restored
    assert restored['tun']['auto-detect-interface'] is True
    assert restored['tun']['enable'] is True
    assert restored['mode'] == 'global'
    assert restored['dns']['fallback'] == ['tls://9.9.9.9']
    assert restored['dns']['nameserver'] == ['223.5.5.5']
    assert not (core.data / bridge.STATE).exists()


def test_disconnect_also_restores_file_when_clash_was_closed(core, monkeypatch):
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    def closed(*args):
        raise OSError('core offline')
    monkeypatch.setattr(bridge.clash_control, 'api', closed)
    bridge.restore(core.data, 'sna0123456789ab')
    assert 'interface-name' not in yaml.safe_load(core.path.read_text(encoding='utf-8'))
    assert not (core.data / bridge.STATE).exists()


def test_failed_core_reload_rolls_back_runtime_file(core, monkeypatch):
    before = core.path.read_text(encoding='utf-8')
    def failing(*args):
        raise OSError('reload refused')
    monkeypatch.setattr(bridge.clash_control, 'api', failing)
    with pytest.raises(OSError):
        bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    assert core.path.read_text(encoding='utf-8') == before


def test_existing_foreign_tunnel_is_not_mistaken_for_clash(core):
    assert bridge.is_clash_route('Mihomo')
    assert not bridge.is_clash_route('some-other-vpn')
    core.runtime['tun']['enable'] = False
    assert not bridge.is_clash_route('Mihomo')


def test_refresh_does_not_reclaim_user_changed_interface(core):
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    doc = yaml.safe_load(core.path.read_text()); doc['interface-name'] = 'user-hotspot'
    core.path.write_text(yaml.safe_dump(doc), encoding='utf-8')
    core.calls.clear()
    with pytest.raises(ValueError, match='用户修改'):
        bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    assert not core.calls
    bridge.restore(core.data, 'sna0123456789ab')
    assert yaml.safe_load(core.path.read_text())['interface-name'] == 'user-hotspot'


def test_leave_preserves_user_runtime_mode_and_tun(core):
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    core.runtime['mode'] = 'global'; core.runtime['tun']['enable'] = False
    bridge.restore(core.data, 'sna0123456789ab')
    doc = yaml.safe_load(core.path.read_text())
    assert doc['mode'] == 'global' and doc['tun']['enable'] is False


def test_closed_clash_is_optional_underlay_not_a_borrowing_failure(core, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(bridge.clash_control, 'candidates', lambda: [core.path])
    monkeypatch.setattr(bridge.clash_control, 'controller', lambda p: ('pipe', 'hidden'))
    def closed(*args):
        raise FileNotFoundError('named pipe is absent')
    monkeypatch.setattr(bridge.clash_control, 'api', closed)
    assert bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])['supported'] is False
    assert not (core.data / bridge.STATE).exists()


def test_verge_regeneration_reapplies_underlay_without_changing_user_mode(core):
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    regenerated = dict(core.original)
    regenerated['mode'] = 'global'
    regenerated['tun'] = {**core.original['tun'], 'enable': False}
    core.path.write_text(yaml.safe_dump(regenerated), encoding='utf-8')
    core.runtime.update({'mode': 'global', 'interface-name': '', 'tun': {'enable': False}})
    core.calls.clear()
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    doc = yaml.safe_load(core.path.read_text())
    assert ('PUT', '/configs?force=true') in core.calls
    assert doc['interface-name'] == 'sna0123456789ab'
    assert doc['mode'] == 'global' and doc['tun']['enable'] is False
    assert doc['rules'] == core.original['rules'] and doc['proxies'] == core.original['proxies']
    bridge.restore(core.data, 'sna0123456789ab')
    assert 'interface-name' not in yaml.safe_load(core.path.read_text())


def test_core_restart_does_not_leave_stale_runtime_while_file_is_owned(core):
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    core.runtime['interface-name'] = ''
    core.calls.clear()
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    assert ('PUT', '/configs?force=true') in core.calls


def test_runtime_normalized_routes_do_not_reload_and_interrupt_airport(core):
    routes = ['2.0.0.0/8', '1.0.0.0/8', '3.0.0.0/8']
    bridge.ensure(core.data, 'sna0123456789ab', routes)
    core.runtime.update({'interface-name': 'sna0123456789ab', 'tun': {
        'enable': True, 'auto-detect-interface': False,
        'route-address': ['1.0.0.0/8', '2.0.0.0/7'],
        'route-exclude-address': list(reversed(yaml.safe_load(core.path.read_text())['tun']['route-exclude-address']))}})
    core.calls.clear()
    for _ in range(3):
        bridge.ensure(core.data, 'sna0123456789ab', routes)
    assert not core.calls
    core.runtime['tun']['route-address'] = ['1.0.0.0/8']
    bridge.ensure(core.data, 'sna0123456789ab', routes)
    assert ('PUT', '/configs?force=true') in core.calls


def test_node_dns_uses_underlay_without_a_proxy_bootstrap_loop(core):
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    doc = yaml.safe_load(core.path.read_text())
    assert all(x.startswith('https://') and x.endswith('#sna0123456789ab')
               for x in doc['dns']['proxy-server-nameserver'])
    assert doc['dns']['proxy-server-nameserver'] == doc['dns']['default-nameserver']


def test_ipv4_lease_direct_dns_and_ipv6_settings_restore(core):
    doc = yaml.safe_load(core.path.read_text())
    doc['ipv6'] = True
    doc['dns']['ipv6'] = True
    doc['dns']['direct-nameserver'] = ['https://original.example/query']
    doc['dns']['direct-nameserver-follow-policy'] = True
    core.path.write_text(yaml.safe_dump(doc), encoding='utf-8')
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    active = yaml.safe_load(core.path.read_text())
    assert active['ipv6'] is False and active['dns']['ipv6'] is False
    assert active['dns']['direct-nameserver'] == active['dns']['default-nameserver']
    assert active['dns']['direct-nameserver-follow-policy'] is False
    bridge.restore(core.data, 'sna0123456789ab')
    restored = yaml.safe_load(core.path.read_text())
    assert restored['ipv6'] is True and restored['dns']['ipv6'] is True
    assert restored['dns']['direct-nameserver'] == ['https://original.example/query']
    assert restored['dns']['direct-nameserver-follow-policy'] is True


def test_upgrade_tracks_new_owned_dns_field_before_restoration(core):
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    state_path = core.data / bridge.STATE
    state = json.loads(state_path.read_text(encoding='utf-8'))
    state['owned'].pop('dns.proxy-server-nameserver')
    state['original'].pop('dns.proxy-server-nameserver')
    state_path.write_text(json.dumps(state), encoding='utf-8')
    doc = yaml.safe_load(core.path.read_text())
    doc['dns']['proxy-server-nameserver'] = ['https://customer-dns.example/query']
    core.path.write_text(yaml.safe_dump(doc))
    bridge.ensure(core.data, 'sna0123456789ab', ['1.0.0.0/8'])
    bridge.restore(core.data, 'sna0123456789ab')
    assert yaml.safe_load(core.path.read_text())['dns']['proxy-server-nameserver'] == ['https://customer-dns.example/query']
