import json
from contextlib import nullcontext
from unittest.mock import Mock
from types import SimpleNamespace
import pytest
from server_network_assist import client_native, client_ownership


def metadata(tmp_path, monkeypatch, health):
    info = {'pid': 123, 'port': 23456, 'token': 'isolated-ui-token'}
    (tmp_path / 'client-instance.json').write_text(json.dumps(info))
    def request(request, timeout):
        value = health if request.full_url.endswith('/api/health') else {'shown': True}
        response = Mock()
        response.read.return_value = json.dumps(value).encode()
        response.geturl.return_value = request.full_url
        return nullcontext(response)
    opener = Mock(open=Mock(side_effect=request))
    monkeypatch.setattr(client_native.urllib.request, 'build_opener', lambda *a: opener)
    return info, opener


def test_second_launch_opens_existing_without_network_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(client_native, 'customer_data', lambda: tmp_path)
    monkeypatch.setattr(client_native.sys, 'argv', ['client-test'])
    monkeypatch.setattr(client_native, 'show_existing_panel', Mock(return_value=True))
    lock, run, recovery = Mock(), Mock(), Mock()
    monkeypatch.setattr(client_ownership, 'CustomerLock', lock)
    monkeypatch.setattr(client_native, 'run_native', run)
    monkeypatch.setattr(client_native, 'recover_guard', recovery)
    client_native.main()
    lock.assert_not_called(); run.assert_not_called(); recovery.assert_not_called()


def test_show_only_missing_backend_never_starts_or_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(client_native, 'customer_data', lambda: tmp_path)
    monkeypatch.setattr(client_native.sys, 'argv', ['client-test', '--show'])
    monkeypatch.setattr(client_native, 'show_existing_panel', Mock(return_value=False))
    run, recovery = Mock(), Mock()
    monkeypatch.setattr(client_native, 'run_native', run)
    monkeypatch.setattr(client_native, 'recover_guard', recovery)
    with pytest.raises(SystemExit, match='没有可打开'):
        client_native.main()
    run.assert_not_called(); recovery.assert_not_called()


@pytest.mark.parametrize('becomes_ready', [True, False])
def test_launch_during_owner_startup_never_recovers_network(tmp_path, monkeypatch, becomes_ready):
    monkeypatch.setattr(client_native, 'customer_data', lambda: tmp_path)
    monkeypatch.setattr(client_native.sys, 'argv', ['client-test'])
    show = Mock(side_effect=[False, becomes_ready] + [False] * 5)
    monkeypatch.setattr(client_native, 'show_existing_panel', show)
    owner = Mock()
    owner.__enter__ = Mock(side_effect=RuntimeError('busy owner'))
    owner.__exit__ = Mock()
    monkeypatch.setattr(client_ownership, 'CustomerLock', Mock(return_value=owner))
    monkeypatch.setattr(client_native.time, 'sleep', Mock())
    monkeypatch.setattr(client_native, 'os', SimpleNamespace(name='posix'))
    run, recovery = Mock(), Mock()
    monkeypatch.setattr(client_native, 'run_native', run)
    monkeypatch.setattr(client_native, 'recover_guard', recovery)
    if becomes_ready:
        client_native.main()
    else:
        with pytest.raises(SystemExit, match='尚未就绪'):
            client_native.main()
    run.assert_not_called(); recovery.assert_not_called()


def test_legacy_web_backend_is_opened_in_browser_without_cleanup(tmp_path, monkeypatch):
    info, _ = metadata(tmp_path, monkeypatch, {'app': 'server-network-assist-client', 'version': '0.7.0'})
    monkeypatch.setattr(client_native, 'activate_native_window', Mock(return_value=False))
    browser = Mock(return_value=True)
    monkeypatch.setattr(client_native.webbrowser, 'open', browser)
    cleanup = Mock()
    monkeypatch.setattr(client_native, 'ClientPanel', cleanup)
    assert client_native.show_existing_panel(tmp_path)
    browser.assert_called_once_with('http://127.0.0.1:23456/#token=' + info['token'])
    cleanup.assert_not_called()


def test_native_show_endpoint_prevents_browser_duplicate(tmp_path, monkeypatch):
    _, opener = metadata(tmp_path, monkeypatch, {'app': 'server-network-assist-client', 'pid': 123, 'native_ui': True})
    browser = Mock()
    monkeypatch.setattr(client_native.webbrowser, 'open', browser)
    assert client_native.show_existing_panel(tmp_path)
    browser.assert_not_called()
    request = opener.open.call_args.args[0]
    assert request.full_url.endswith('/api/ui/show')
    assert request.get_header('Origin') == 'http://127.0.0.1:23456'


def test_old_native_window_is_restored_before_browser_fallback(tmp_path, monkeypatch):
    metadata(tmp_path, monkeypatch, {'app': 'server-network-assist-client', 'pid': 123})
    restore = Mock(return_value=True)
    monkeypatch.setattr(client_native, 'activate_native_window', restore)
    browser = Mock()
    monkeypatch.setattr(client_native.webbrowser, 'open', browser)
    assert client_native.show_existing_panel(tmp_path)
    restore.assert_called_once_with(123); browser.assert_not_called()


@pytest.mark.parametrize('health', [
    {'app': 'other'}, {'app': 'server-network-assist-client', 'pid': True},
    {'app': 'server-network-assist-client', 'pid': 456},
])
def test_wrong_health_cannot_activate_window(tmp_path, monkeypatch, health):
    metadata(tmp_path, monkeypatch, health)
    assert client_native.existing_panel(tmp_path) is None


@pytest.mark.parametrize('failure', [RuntimeError('restore failed'), SystemExit('HTTP 403')])
def test_windowed_disconnect_failure_is_visible(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(client_native, 'customer_data', lambda: tmp_path)
    monkeypatch.setattr(client_native.sys, 'argv', ['client-test', '--disconnect'])
    monkeypatch.setattr(client_native, 'client_main', Mock(side_effect=failure))
    notice = Mock()
    monkeypatch.setattr(client_native, 'windowed_notice', notice)
    with pytest.raises(type(failure)):
        client_native.main()
    assert notice.call_count == 1
    assert notice.call_args.args[1] is True


def test_custom_close_uses_window_lifecycle_not_direct_network_cleanup():
    controls = client_native.NativeWindowControls('http://127.0.0.1:23456')
    controls._window = Mock()
    controls._window.get_current_url.return_value = 'http://127.0.0.1:23456/'
    controls.close()
    controls._window.destroy.assert_called_once()
    controls._window.minimize.assert_not_called()


@pytest.mark.parametrize('url', ['https://outside.example/', 'http://127.0.0.1:23457/', 'file:///tmp/page.html'])
def test_external_navigation_cannot_use_native_window_controls(url):
    controls = client_native.NativeWindowControls('http://127.0.0.1:23456')
    controls._window = Mock()
    controls._window.get_current_url.return_value = url
    controls.minimize(); controls.toggle_maximize(); controls.close()
    controls._window.minimize.assert_not_called()
    controls._window.maximize.assert_not_called()
    controls._window.destroy.assert_not_called()
