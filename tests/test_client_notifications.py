import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import Mock

import pytest
from server_network_assist.client import ClientPanel, handler_for
from server_network_assist.client_native_tray import NativeTray
from server_network_assist.client_preferences import ClientPreferences


def test_preference_persists_and_invalid_input_does_not_change_it(tmp_path):
    preferences = ClientPreferences(tmp_path)
    assert preferences.state()['desktop_notifications'] is True
    preferences.set_notifications(False)
    assert ClientPreferences(tmp_path).state()['desktop_notifications'] is False
    with pytest.raises(ValueError):
        preferences.set_notifications('false')
    assert preferences.state()['desktop_notifications'] is False


def test_failed_save_keeps_running_preference(tmp_path, monkeypatch):
    import server_network_assist.client_preferences as module
    preferences = ClientPreferences(tmp_path)
    monkeypatch.setattr(module.os, 'replace', Mock(side_effect=OSError('locked')))
    with pytest.raises(OSError):
        preferences.set_notifications(False)
    assert preferences.state()['desktop_notifications'] is True
    assert not list(tmp_path.glob('client-preferences-*'))


def test_quiet_mode_keeps_critical_window_and_deduplicates(tmp_path):
    tray = NativeTray.__new__(NativeTray)
    tray.panel = ClientPanel(tmp_path)
    tray.window = Mock(); tray.icon = Mock()
    tray.panel.preferences.set_notifications(False)
    tray._display_event({'code': 'connected', 'message': 'Connected'})
    tray.window.show.assert_not_called()
    event = {'code': 'attachment_lost', 'message': 'Disconnected', 'error': True}
    tray._display_event(event)
    tray._display_event(dict(event, message='Recovery complete'))
    tray.window.show.assert_called_once()
    tray.icon.notify.assert_not_called()
    tray.panel.preferences.set_notifications(True)
    tray._display_event({'code': 'connected', 'message': 'Connected'})
    tray.icon.notify.assert_called_once()


def test_preferences_endpoint_requires_auth_and_does_not_touch_network(tmp_path):
    panel = ClientPanel(tmp_path)
    panel.leave_network = Mock(side_effect=AssertionError('network must not change'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(panel))
    panel.origin = 'http://127.0.0.1:' + str(server.server_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def post(token, origin):
        request = urllib.request.Request(panel.origin + '/api/preferences/notifications',
            data=json.dumps({'desktop_notifications': False}).encode(),
            headers={'X-Client-Token': token, 'Origin': origin, 'Content-Type': 'application/json'})
        return opener.open(request, timeout=3)
    try:
        for token, origin in [('', panel.origin), (panel.token, 'http://invalid.example')]:
            with pytest.raises(urllib.error.HTTPError) as error:
                post(token, origin)
            assert error.value.code == 403
        with post(panel.token, panel.origin) as response:
            assert json.load(response) == {'desktop_notifications': False}
        panel.leave_network.assert_not_called()
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
