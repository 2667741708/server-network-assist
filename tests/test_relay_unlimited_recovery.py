import json
import subprocess
import pytest
from test_client_relay import FakeRunner, policy
from server_network_assist.client_relay import RelayManager, plan_revoke


@pytest.mark.parametrize('action', ['apply', 'revoke'])
def test_historical_limits_are_removed_after_transient_failure(tmp_path, monkeypatch, action):
    monkeypatch.setattr('server_network_assist.client_relay.require_linux_root', lambda: None)
    normal = FakeRunner()
    fault = [False]
    old = policy()
    deletes = [command for command in plan_revoke(old) if command[0] == 'tc']
    def runner(command, **kwargs):
        if fault[0] and command in deletes:
            normal.calls.append((command, None, kwargs.get('check', True)))
            return subprocess.CompletedProcess(command, 1, '', 'transient TC error')
        if fault[0] and command[:4] == ['tc', '-j', 'filter', 'show']:
            pref = deletes[0][deletes[0].index('pref') + 1]
            return subprocess.CompletedProcess(command, 0, json.dumps([{'pref': int(pref)}]), '')
        return normal(command, **kwargs)
    manager = RelayManager(tmp_path, runner)
    manager.apply(old)
    fault[0] = True
    with pytest.raises(RuntimeError, match='recovery state'):
        manager.apply(policy(download_bps=None, upload_bps=None))
    assert manager.status()['peers']['customer-1']['status'] == 'recovery_required'
    fault[0] = False
    normal.calls.clear()
    if action == 'apply':
        result = manager.apply(policy(download_bps=None, upload_bps=None))
        assert result['status'] == 'active'
    else:
        assert manager.revoke('customer-1')['status'] == 'revoked'
    calls = [call[0] for call in normal.calls]
    assert all(command in calls for command in deletes)
    assert not any('police' in command and 'replace' in command for command in calls)


def test_already_absent_tc_is_proved_by_readonly_queries(tmp_path, monkeypatch):
    monkeypatch.setattr('server_network_assist.client_relay.require_linux_root', lambda: None)
    normal = FakeRunner()
    def runner(command, **kwargs):
        if command[0] == 'tc' and 'del' in command:
            return subprocess.CompletedProcess(command, 1, '', 'not found')
        if command[:2] == ['tc', '-j']:
            return subprocess.CompletedProcess(command, 0, '[]', '')
        return normal(command, **kwargs)
    manager = RelayManager(tmp_path, runner)
    manager.apply(policy())
    assert manager.apply(policy(download_bps=None, upload_bps=None))['status'] == 'active'


def test_recovery_owner_reserves_minor_until_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr('server_network_assist.client_relay.require_linux_root', lambda: None)
    monkeypatch.setattr('server_network_assist.client_relay._minor', lambda _: 1234)
    normal = FakeRunner()
    manager = RelayManager(tmp_path, normal)
    original = policy()
    manager.store.save({'version':1,'peers':{'customer-1':{
        'status':'recovery_required','policy':policy(download_bps=None, upload_bps=None),
        'previous':{'status':'active','policy':original}}}})
    with pytest.raises(ValueError, match='reserved owner'):
        manager.apply(policy(peer_id='another-customer', address='100.64.77.3/32'))
    assert not normal.calls
