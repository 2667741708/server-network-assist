import json
from unittest.mock import Mock
import pytest

from server_network_assist.client import ClientPanel
from server_network_assist.client_online import OnlineServiceClient


def record(identifier='one'):
    signing, _, wireguard = OnlineServiceClient._new_keys()
    return {'base_url': 'https://service.example.test', 'device_id': 'device-' + identifier,
            'customer_id': 'customer-' + identifier, 'signing_private_key': signing,
            'wireguard_private_key': wireguard, 'enrolled_at': 1000,
            'enrollment_token_digest': identifier}


def existing(panel):
    first, second = record(), record('two')
    panel.online._write_private(panel.online.path, first)
    second_id = panel.online.saved.remember(second, '备用订阅')
    return first, second, second_id


def test_single_registration_migration_and_public_list_do_not_expose_secrets(tmp_path):
    client = OnlineServiceClient(tmp_path)
    first = record(); client._write_private(client.path, first)
    before = client.path.read_bytes()
    rows = client.saved.list()
    assert len(rows) == 1 and rows[0]['selected'] is True
    assert client.path.read_bytes() == before
    public = json.dumps(rows)
    for key in ('signing_private_key', 'wireguard_private_key', 'enrollment_token_digest', 'base_url'):
        assert key not in public
    assert first['signing_private_key'] not in public
    assert len(OnlineServiceClient(tmp_path).saved.list()) == 1


def test_add_preserves_current_identity_and_lease_and_duplicate_is_not_redeemed(tmp_path):
    client = OnlineServiceClient(tmp_path)
    first = record(); client._write_private(client.path, first)
    client._write_private(client.lease_path, {'id': 'old-lease'})
    before = client.path.read_bytes()
    new = record('two')
    import hashlib
    new['enrollment_token_digest'] = hashlib.sha256(b'new-enrollment-token').hexdigest()
    client._enroll_record = Mock(return_value=new)
    result = client.add_subscription('https://service.example.test/#enroll=new-enrollment-token', '备用')
    assert len(result['subscriptions']) == 2
    assert client.path.read_bytes() == before
    assert client.active_lease()['id'] == 'old-lease'
    assert client.add_subscription('https://service.example.test/#enroll=new-enrollment-token')['reused']
    client._enroll_record.assert_called_once()
    client._enroll_record.side_effect = ValueError('服务暂不可达')
    with pytest.raises(ValueError):
        client.add_subscription('https://service.example.test/#enroll=failed-enrollment-token')
    assert len(client.saved.list()) == 2 and client.path.read_bytes() == before


def test_switch_recovers_with_old_identity_then_preserves_both_and_clears_old_lease(tmp_path):
    panel = ClientPanel(tmp_path)
    first, second, second_id = existing(panel)
    panel.online._write_private(panel.online.lease_path, {'id': 'old-lease'})
    def recover():
        assert panel.online._record()['device_id'] == first['device_id']
        return {'ok': True, 'release_error': 'offline'}
    panel.leave_network = Mock(side_effect=recover)
    result = panel.select_online_subscription(second_id)
    panel.leave_network.assert_called_once()
    assert result['release_error'] == 'offline'
    assert panel.online._record()['signing_private_key'] == second['signing_private_key']
    assert panel.online.active_lease() is None
    first_id = next(row['id'] for row in result['subscriptions'] if not row['selected'])
    def recover_second():
        assert panel.online._record()['device_id'] == second['device_id']
        return {'ok': True}
    panel.leave_network.side_effect = recover_second
    panel.select_online_subscription(first_id)
    assert panel.online._record()['signing_private_key'] == first['signing_private_key']
    assert len(panel.online.saved.list()) == 2


def test_failed_recovery_and_unknown_id_cannot_change_or_delete_subscription(tmp_path):
    panel = ClientPanel(tmp_path)
    _, _, second_id = existing(panel)
    before = panel.online.path.read_bytes()
    panel.leave_network = Mock(side_effect=RuntimeError('恢复未完成'))
    with pytest.raises(RuntimeError):
        panel.select_online_subscription(second_id)
    assert panel.online.path.read_bytes() == before
    selected_id = next(row['id'] for row in panel.online.saved.list() if row['selected'])
    with pytest.raises(RuntimeError):
        panel.remove_online_subscription(selected_id)
    assert len(panel.online.saved.list()) == 2
    panel.leave_network.reset_mock()
    with pytest.raises(ValueError):
        panel.select_online_subscription('../unknown')
    panel.leave_network.assert_not_called()


def test_remove_inactive_keeps_connection_and_remove_current_requires_recovery(tmp_path):
    panel = ClientPanel(tmp_path)
    first, _, second_id = existing(panel)
    panel.leave_network = Mock(return_value={'ok': True})
    panel.remove_online_subscription(second_id)
    panel.leave_network.assert_not_called()
    assert panel.online._record()['device_id'] == first['device_id']
    selected_id = panel.online.saved.list()[0]['id']
    panel.remove_online_subscription(selected_id)
    panel.leave_network.assert_called_once()
    assert not panel.online.configured() and panel.online.saved.list() == []


def test_explicit_history_import_deduplicates_without_selecting_or_touching_sources(tmp_path):
    root = tmp_path / 'same-user'
    client = OnlineServiceClient(root / 'gui-current')
    first = record(); client._write_private(client.path, first)
    old = OnlineServiceClient(root / 'migration-backup')
    old._write_private(old.path, record('old'))
    outside = OnlineServiceClient(tmp_path / 'other-user')
    outside._write_private(outside.path, record('other'))
    old_bytes = old.path.read_bytes(); current_bytes = client.path.read_bytes()
    assert client.saved.import_legacy(root)['imported'] == 1
    assert client.saved.import_legacy(root)['imported'] == 0
    assert old.path.read_bytes() == old_bytes and client.path.read_bytes() == current_bytes
    assert len(client.saved.list()) == 2
    old_id = next(row['id'] for row in client.saved.list() if not row['selected'])
    client.saved.remove(old_id)
    assert len(OnlineServiceClient(client.data).saved.list()) == 1, 'Removed subscriptions must not auto-reappear'


def test_corrupt_history_is_never_overwritten(tmp_path):
    client = OnlineServiceClient(tmp_path)
    client._write_private(client.path, record())
    client.saved.path.write_text('{broken')
    with pytest.raises(ValueError, match='未覆盖历史数据'):
        client.saved.list()
    assert client.saved.path.read_text() == '{broken'


def test_installed_localappdata_can_explicitly_import_own_programdata(tmp_path):
    from unittest.mock import patch
    root = tmp_path / 'ProgramData' / 'current-sid'
    old = OnlineServiceClient(root / 'portable')
    old._write_private(old.path, record('old'))
    other = OnlineServiceClient(root.parent / 'other-sid')
    other._write_private(other.path, record('other'))
    client = OnlineServiceClient(tmp_path / 'LocalAppData' / 'data')
    with patch('server_network_assist.client_paths.customer_data', return_value=root):
        assert client.saved.import_legacy()['imported'] == 1
    assert not client.configured(), 'Import must not select or connect'
    assert [row['customer_id'] for row in client.saved.list()] == ['customer-old']


def test_conflicting_device_keys_are_refused(tmp_path):
    client = OnlineServiceClient(tmp_path)
    client.saved.remember(record())
    with pytest.raises(ValueError, match='不同密钥'):
        client.saved.remember(record())


def test_catalog_persists_public_exit_type_labels_without_secrets(tmp_path):
    client = OnlineServiceClient(tmp_path)
    first = record(); client._write_private(client.path, first)
    client.saved.list()
    client.saved.remember_catalog({'routes': [
        {'id': 'physical', 'egress_mode': 'physical'},
        {'id': 'proxy', 'egress_mode': 'source_proxy'},
        {'id': 'ignored', 'egress_mode': 'unexpected'},
    ]})
    rows = client.saved.list()
    assert rows[0]['egress_modes'] == ['physical', 'source_proxy']
    public = json.dumps(rows)
    assert first['signing_private_key'] not in public
    assert first['wireguard_private_key'] not in public
