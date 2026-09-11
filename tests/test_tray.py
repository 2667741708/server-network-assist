"""Optional tray/installer behavior without requiring an interactive desktop."""
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from server_network_assist import __version__
from server_network_assist import desktop_tray as tray
from server_network_assist import desktop_install_check as install_check


class TrayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_dependency_never_blocks_panel(self):
        with patch.object(tray.importlib.util, 'find_spec', side_effect=ValueError('bad import spec')):
            self.assertFalse(tray.background_status(self.data)['tray_available'])
            with patch.object(tray.subprocess, 'Popen') as popen:
                tray.start_tray(self.data)
            popen.assert_not_called()

    def test_stale_and_malformed_state_are_not_running(self):
        path = self.data / tray.STATE
        path.write_text(json.dumps({'updated': time.time()-60, 'running': True, 'notifications_enabled': True}))
        self.assertFalse(tray.background_status(self.data)['tray_running'])
        path.write_text('not-json')
        self.assertFalse(tray.background_status(self.data)['tray_running'])

    def test_live_heartbeat_prevents_duplicate_launch(self):
        (self.data / tray.STATE).write_text(json.dumps({'updated': time.time(), 'running': True, 'notifications_enabled': False}))
        with patch.object(tray.importlib.util, 'find_spec', return_value=object()), patch.dict('os.environ', {'DISPLAY': ':0'}):
            self.assertTrue(tray.background_status(self.data)['tray_running'])
            with patch.object(tray.subprocess, 'Popen') as popen:
                tray.start_tray(self.data)
            popen.assert_not_called()

    def test_failed_launch_is_optional(self):
        with patch.object(tray, 'background_status', return_value={'tray_available': True, 'tray_running': False}), \
                patch.object(tray.subprocess, 'Popen', side_effect=OSError('no session')):
            tray.start_tray(self.data)

    def test_exclusive_tray_lock(self):
        with tray.tray_lock(self.data):
            with self.assertRaises(OSError):
                with tray.tray_lock(self.data):
                    self.fail('second tray should not start')
        with tray.tray_lock(self.data):
            pass

    def test_connectivity_labels_distinguish_unknown_from_healthy(self):
        self.assertEqual(tray.connection_label({}), '网络连接待检查')
        self.assertEqual(tray.connection_label({'direct': {'ok': True}, 'system': {'ok': True}}), '网络连接正常')
        self.assertEqual(tray.connection_label({'direct': {'ok': True}, 'system': {'ok': False}}), '直连正常，应用连接异常')

    def test_installer_retries_a_transient_second_health_read(self):
        response = io.BytesIO(json.dumps({'version': __version__, 'pid': 123}).encode())
        opener = Mock()
        opener.open.side_effect = [OSError('backend restarting'), response]
        state = {'port': 12345, 'token': 'test-token', 'pid': 123}
        with patch('sys.argv', ['desktop_install_check', '--data', str(self.data), '--version', __version__]), \
                patch.object(install_check, 'running_instance', return_value=state), \
                patch('urllib.request.build_opener', return_value=opener), \
                patch.object(install_check.time, 'sleep'), patch('sys.stdout', new_callable=io.StringIO) as output:
            install_check.main()
        self.assertTrue(json.loads(output.getvalue())['ok'])
        self.assertEqual(opener.open.call_count, 2)


if __name__ == '__main__':
    unittest.main()
