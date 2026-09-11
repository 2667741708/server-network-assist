import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from server_network_assist.desktop_recovery import CampusLogin, LocalRecovery
from server_network_assist.desktop import native_status


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.state = {'elevated': True, 'tunnels': [{'name': 'test', 'active': True, 'start_mode': 'Automatic', 'delayed': True}]}

    def test_windows_pause_and_restore_keep_first_backup(self):
        def command(args):
            record = json.loads(recovery.path('test').read_text())
            self.assertEqual(record['start_mode'], 'Automatic')
            if 'pause' in args:
                self.state['tunnels'][0].update(active=False, start_mode='Disabled')
            else:
                self.assertIn('restore-startup', args)
                self.state['tunnels'][0].update(active=True, start_mode='Automatic', delayed=True)
        recovery = LocalRecovery(self.data, True, command, lambda: self.state)
        recovery.change('test')
        recovery.change('test')
        self.assertTrue(recovery.records()[0]['was_active'])
        self.assertEqual(recovery.change('test', True)['recovery']['state'], 'restored')

    def test_permission_unknown_and_missing_backup_do_not_mutate(self):
        run = Mock()
        recovery = LocalRecovery(self.data, True, run, lambda: self.state)
        for name, restore in [('missing', False), ('test', True)]:
            with self.assertRaises(ValueError): recovery.change(name, restore)
        self.state['elevated'] = False
        with self.assertRaises(ValueError): recovery.change('test')
        run.assert_not_called()

    def test_failed_pause_preserves_backup_and_reports_failure(self):
        recovery = LocalRecovery(self.data, True, Mock(side_effect=RuntimeError('fail')), lambda: self.state)
        with self.assertRaises(RuntimeError): recovery.change('test')
        self.assertEqual(recovery.records()[0]['state'], 'pause_failed')
        self.assertEqual(recovery.records()[0]['start_mode'], 'Automatic')

    def test_no_false_success_when_service_did_not_stop(self):
        recovery = LocalRecovery(self.data, True, Mock(), lambda: self.state)
        with self.assertRaises(RuntimeError): recovery.change('test')

    def test_linux_mask_and_restore(self):
        self.state['tunnels'][0]['start_mode'] = 'enabled'
        calls = []
        def run(args):
            calls.append(args)
            if 'mask' in args: self.state['tunnels'][0].update(active=False, start_mode='masked')
            if 'start' in args: self.state['tunnels'][0].update(active=True, start_mode='enabled')
        recovery = LocalRecovery(self.data, False, run, lambda: self.state)
        recovery.change('test')
        recovery.change('test', True)
        self.assertEqual(calls[0], ['systemctl', 'mask', '--now', '--', 'wg-quick@test.service'])

    def test_linux_masked_service_remains_visible_after_unloaded(self):
        replies = ['[]', '[]', '[{"unit_file":"wg-quick@test.service","state":"masked"}]', 'inactive', 'masked']
        with patch('server_network_assist.desktop.WINDOWS', False), patch('server_network_assist.desktop.run', side_effect=replies), patch('os.geteuid', return_value=0, create=True):
            state = native_status()
        self.assertEqual(state['tunnels'][0]['name'], 'test')
        self.assertFalse(state['tunnels'][0]['active'])
        self.assertEqual(state['tunnels'][0]['start_mode'], 'masked')


class CampusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        (self.data / 'netlogin.py').write_text('# trusted script')
        self.native = Mock(return_value={'tunnels': [], 'proxy': {'enabled': False}})
        self.campus = CampusLogin(self.data, self.native)
        self.payload = {'username': 'account', 'password': 'private-secret', 'service': '0', 'physical_network_confirmed': True}

    def test_missing_script_and_invalid_input_do_not_execute(self):
        with patch('server_network_assist.desktop_recovery.subprocess.run') as run:
            for change in ({'service': '8'}, {'password': ''}, {'physical_network_confirmed': False}):
                with self.assertRaises(ValueError): self.campus.login({**self.payload, **change})
            (self.data / 'netlogin.py').unlink()
            self.assertFalse(self.campus.status()['configured'])
            with self.assertRaises(ValueError): self.campus.login(self.payload)
            run.assert_not_called()

    def test_active_or_unknown_tunnel_state_never_logs_in(self):
        with patch('server_network_assist.desktop_recovery.subprocess.run') as run:
            self.native.return_value = {'tunnels': [{'active': True}]}
            with self.assertRaises(ValueError): self.campus.login(self.payload)
            self.native.return_value = {'tunnels': [{'active': False, 'service_state': 'StopPending'}]}
            with self.assertRaises(ValueError): self.campus.login(self.payload)
            self.native.side_effect = RuntimeError('native probe failed')
            with self.assertRaises(RuntimeError): self.campus.login(self.payload)
            run.assert_not_called()

    def test_login_stdin_followed_by_redacted_identity_verification(self):
        status = {'summary': {'state': 'online', 'online': True, 'userId': 'account', 'service': '校园网', 'userIp': '10.0.0.2'}, 'auth1Session': {'sessionId': 'private-session'}, 'internetOnline': True}
        responses = [subprocess.CompletedProcess([], 0, '{"ok":true}'), subprocess.CompletedProcess([], 0, json.dumps(status))]
        with patch('server_network_assist.desktop_recovery.subprocess.run', side_effect=responses) as run:
            result = self.campus.login(self.payload)
            args, kwargs = run.call_args_list[0]
            self.assertNotIn('private-secret', str(args))
            self.assertEqual(json.loads(kwargs['input'])['password'], 'private-secret')
            self.assertEqual(run.call_args_list[1].args[0][-2:], ['current-status', '--json'])
            self.assertTrue(result['verified'])
            self.assertNotIn('private-session', json.dumps(result))
            self.assertNotIn('private-secret', json.dumps(result))

    def test_script_error_and_timeout_do_not_echo_secrets(self):
        for effect in [subprocess.CompletedProcess([], 1, '{"ok":false,"message":"private-secret"}'), subprocess.TimeoutExpired('private-secret', 180, output='private-secret')]:
            with patch('server_network_assist.desktop_recovery.subprocess.run', side_effect=effect if isinstance(effect, Exception) else None, return_value=effect):
                with self.assertRaises(ValueError) as result: self.campus.login(self.payload)
                self.assertNotIn('private-secret', str(result.exception))

    def test_existing_other_account_not_reported_as_verified(self):
        with patch.object(self.campus, 'execute', return_value=(0, {'ok': True})), patch.object(self.campus, 'current', return_value={'online': True, 'account': 'someone-else'}):
            self.assertFalse(self.campus.login(self.payload)['verified'])

    def test_existing_other_service_not_reported_as_verified(self):
        with patch.object(self.campus, 'execute', return_value=(0, {'ok': True})), patch.object(self.campus, 'current', return_value={'online': True, 'account': 'account', 'service': '中国移动'}):
            self.assertFalse(self.campus.login(self.payload)['verified'])

    def test_current_probe_failure_cannot_execute_script(self):
        self.native.side_effect = RuntimeError('cannot read service state')
        with patch('server_network_assist.desktop_recovery.subprocess.run') as run:
            with self.assertRaises(RuntimeError): self.campus.current()
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
