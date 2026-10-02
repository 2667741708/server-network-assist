import json
from contextlib import nullcontext
from unittest.mock import Mock
import pytest
from server_network_assist import client_native, client_ownership


def setup(monkeypatch):
    cleanup = Mock()
    monkeypatch.setattr(client_native, 'ClientPanel', lambda _: Mock(leave_network=cleanup))
    monkeypatch.setattr(client_ownership, 'CustomerLock', lambda *a, **k: nullcontext())
    warning = Mock()
    monkeypatch.setattr(client_native, 'notify_guard_recovery', warning)
    monkeypatch.setattr(client_native.time, 'sleep', Mock())
    monkeypatch.setattr(client_native, 'confirmed_replacement', lambda *a: False)
    return cleanup, warning


def test_guard_retries_local_failure_and_clears_diagnostic(tmp_path, monkeypatch):
    cleanup, warning = setup(monkeypatch)
    cleanup.side_effect = [RuntimeError('temporary uninstall failure'), {'ok': True}]
    client_native.recover_guard(123, tmp_path)
    assert cleanup.call_count == 2
    warning.assert_called_once()
    assert not (tmp_path / 'guard-recovery-123.json').exists()


@pytest.mark.parametrize('value', ['{broken', '{}', '[]', '{"pid":"invalid"}', '{"pid":true}', '{"pid":999999}'])
def test_corrupt_ui_instance_cannot_prevent_owned_cleanup(tmp_path, monkeypatch, value):
    cleanup, warning = setup(monkeypatch)
    (tmp_path / 'client-instance.json').write_text(value)
    client_native.recover_guard(123, tmp_path)
    cleanup.assert_called_once()
    warning.assert_not_called()


def test_replacement_owner_during_retry_is_not_cleaned(tmp_path, monkeypatch):
    cleanup, warning = setup(monkeypatch)
    cleanup.side_effect = RuntimeError('temporary failure')
    def replacement(_):
        (tmp_path / 'client-instance.json').write_text(json.dumps({'pid': 456}))
    monkeypatch.setattr(client_native, 'confirmed_replacement', lambda info, parent: info.get('pid') == 456)
    monkeypatch.setattr(client_native.time, 'sleep', replacement)
    client_native.recover_guard(123, tmp_path)
    cleanup.assert_called_once()
    warning.assert_called_once()


def test_recheck_owner_after_acquiring_lock(tmp_path, monkeypatch):
    cleanup, warning = setup(monkeypatch)
    class NewOwner:
        def __enter__(self):
            (tmp_path / 'client-instance.json').write_text('{"pid":456}')
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(client_ownership, 'CustomerLock', lambda *a, **k: NewOwner())
    monkeypatch.setattr(client_native, 'confirmed_replacement', lambda info, parent: info.get('pid') == 456)
    client_native.recover_guard(123, tmp_path)
    cleanup.assert_not_called()


def test_health_confirmation_matches_authenticated_owner_pid(monkeypatch):
    monkeypatch.setattr(client_native, 'native_process_alive', lambda _: True)
    response = Mock()
    response.read.return_value = b'{"app":"server-network-assist-client","pid":456}'
    response.geturl.return_value = 'http://127.0.0.1:23456/api/health'
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = context
    monkeypatch.setattr(client_native.urllib.request, 'build_opener', lambda *a: opener)
    info = {'pid':456, 'port':23456, 'token':'isolated-test-token'}
    assert client_native.confirmed_replacement(info, 123)
    info['pid'] = 999999
    assert not client_native.confirmed_replacement(info, 123)
    info['pid'] = True
    assert not client_native.confirmed_replacement(info, 123)
    response.read.return_value = b'{"app":"another-app","pid":456}'
    info['pid'] = 456
    assert not client_native.confirmed_replacement(info, 123)
    response.read.return_value = b'{"app":"server-network-assist-client","pid":456}'
    response.geturl.return_value = 'https://example.invalid/'
    assert not client_native.confirmed_replacement(info, 123)
    response.geturl.return_value = 'http://127.0.0.1:23456/api/health'
    monkeypatch.setattr(client_native, 'native_process_alive', lambda _: False)
    assert not client_native.confirmed_replacement(info, 123)


def test_startup_failure_recovers_after_lock_release_before_modal(tmp_path, monkeypatch):
    events = []
    class Lifecycle:
        def __enter__(self):
            events.append('locked')
        def __exit__(self, *args):
            events.append('released')
    monkeypatch.setattr(client_ownership, 'CustomerLock', lambda *a, **k: Lifecycle())
    monkeypatch.setattr(client_native, 'customer_data', lambda: tmp_path)
    monkeypatch.setattr(client_native.sys, 'argv', ['isolated-client-test'])
    monkeypatch.setattr(client_native, 'run_native', Mock(side_effect=RuntimeError('startup cleanup failed')))
    monkeypatch.setattr(client_native, 'recover_guard', lambda *a: events.append('recovered'))
    monkeypatch.setattr(client_native.ctypes.windll.user32, 'MessageBoxW', lambda *a: events.append('modal'))
    with pytest.raises(RuntimeError, match='startup cleanup failed'):
        client_native.main()
    assert events == ['locked', 'released', 'recovered', 'modal']


@pytest.mark.parametrize('failure', ['write', 'flush'])
def test_window_log_failure_does_not_abort_tray_setup(monkeypatch, failure):
    monkeypatch.setattr(client_native.faulthandler, 'cancel_dump_traceback_later', Mock())
    log, panel = Mock(), Mock()
    getattr(log, failure).side_effect = OSError('disk full')
    client_native.record_window_shown(panel, log)
    panel.publish_event.assert_called_once()
    assert panel.publish_event.call_args.args[0] == 'diagnostic_log_failed'
