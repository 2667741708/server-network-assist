import json
from unittest.mock import Mock

import pytest

from server_network_assist.client import ClientPanel
from server_network_assist import client_tunnel, client_clash_coexist, client_app_routing, client_original_network


def panel(tmp_path, monkeypatch):
    p = ClientPanel(tmp_path)
    monkeypatch.setattr(p.online, 'configured', lambda: True)
    monkeypatch.setattr(p.online, 'release', Mock())
    monkeypatch.setattr(client_app_routing, 'restore', Mock())
    monkeypatch.setattr(client_original_network, 'restore', Mock())
    return p


def test_leave_works_offline_and_keeps_subscription(tmp_path, monkeypatch):
    p = panel(tmp_path, monkeypatch)
    p.save_active('grant', 'sna123456789abc', 'online')
    subscription = tmp_path / 'customer-online-service.json'
    subscription.write_text('credential')
    stop = Mock()
    monkeypatch.setattr(client_tunnel, 'stop', stop)
    p.online.release.side_effect = ValueError('offline')
    v = p.leave_network()
    stop.assert_called_once_with('sna123456789abc', tmp_path)
    assert v['left_network'] and v['release_error'] == 'offline'
    assert p.active() is None and subscription.read_text() == 'credential'
    client_original_network.restore.assert_called_once_with(tmp_path)


def test_crash_before_active_save_is_recoverable(tmp_path, monkeypatch):
    p = panel(tmp_path, monkeypatch)
    client_tunnel.record_owned(tmp_path, 'sna123456789abc')
    stop = Mock()
    monkeypatch.setattr(client_tunnel, 'stop', stop)
    p.leave_network()
    stop.assert_called_once_with('sna123456789abc', tmp_path)


def test_failed_cleanup_is_never_reported_as_success(tmp_path, monkeypatch):
    p = panel(tmp_path, monkeypatch)
    p.save_active('grant', 'sna123456789abc', 'online')
    monkeypatch.setattr(client_tunnel, 'stop', Mock(side_effect=RuntimeError('cleanup failed')))
    with pytest.raises(RuntimeError, match='cleanup failed'):
        p.leave_network()
    assert p.active()
    p.online.release.assert_not_called()
    client_original_network.restore.assert_called_once_with(tmp_path)


def test_app_recovery_failure_still_removes_tunnel(tmp_path, monkeypatch):
    p = panel(tmp_path, monkeypatch)
    p.save_active('grant', 'sna123456789abc', 'online')
    client_app_routing.restore.side_effect = ValueError('core unavailable')
    stop = Mock()
    monkeypatch.setattr(client_tunnel, 'stop', stop)
    with pytest.raises(RuntimeError):
        p.leave_network()
    stop.assert_called_once()
    assert p.active()


def test_failed_exit_never_renews_or_reapplies_clash(tmp_path, monkeypatch):
    p = panel(tmp_path, monkeypatch)
    p.save_active('grant', 'sna123456789abc', 'online')
    monkeypatch.setattr(client_tunnel, 'stop', Mock(side_effect=RuntimeError('cleanup failed')))
    monkeypatch.setattr(p.online, 'renew', Mock())
    monkeypatch.setattr(client_tunnel, 'refresh_coexist', Mock())
    with pytest.raises(RuntimeError): p.leave_network()
    assert p.leaving_path.exists()
    with pytest.raises(RuntimeError): p.reconcile()
    p.online.renew.assert_not_called()
    client_tunnel.refresh_coexist.assert_not_called()
