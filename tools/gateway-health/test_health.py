import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('health', Path(__file__).with_name('gateway-health.py'))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

class HealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.scope = patch.object(h, 'BASE', self.base)
        self.scope.start()
        self.c = h.Controller({}, {}, True)

    def tearDown(self):
        self.scope.stop()
        self.temp.cleanup()

    def test_debounce_and_cooldown(self):
        with patch.object(h, 'run', return_value=(0, '')) as call:
            self.assertFalse(self.c.action('x', ['safe'], threshold=2))
            self.assertTrue(self.c.action('x', ['safe'], threshold=2))
            self.assertFalse(self.c.action('x', ['safe'], threshold=2))
            self.assertEqual(call.call_count, 1)
            self.assertIn('x', json.loads((self.base / 'state.json').read_text())['last_action'])

    def test_readonly_and_pause(self):
        self.c.repair = False
        with patch.object(h, 'run') as call:
            self.c.action('x', ['safe'])
            call.assert_not_called()
        (self.base / 'paused').touch()
        with patch.object(h, 'run') as call:
            self.assertEqual(self.c.cycle()['mode'], 'paused')
            call.assert_not_called()

    def test_offline_peer_does_not_restart_gateway(self):
        with patch.object(self.c, 'start_missing', return_value=True), patch.object(h, 'tcp', return_value=False), patch.object(h, 'run', side_effect=[(0, 'PublicKey = key'), (0, 'key'), (0, 'key 25')]) as call:
            self.c.wg('wg-fleet', '10.203.49.2', True)
            self.assertEqual(call.call_count, 3)
            self.assertEqual(self.c.result['actions'], [])

    def test_uplink_offline_does_not_resync_cloud(self):
        with patch.object(self.c, 'start_missing', return_value=True), patch.object(h, 'tcp', return_value=False), patch.object(h, 'run', side_effect=[(0, 'PublicKey = key'), (0, 'key'), (0, 'key 25')]) as call:
            self.c.wg('wg0', '10.201.250.1', False)
            self.assertEqual(call.call_count, 3)

    def test_missing_peer_restored_without_interface_restart(self):
        with patch.object(self.c, 'start_missing', return_value=True), patch.object(h, 'tcp', return_value=False), patch.object(h, 'run', side_effect=[(0, 'PublicKey = key'), (0, ''), (0, ''), (0, 'key 25')]) as call:
            self.c.wg('wg-fleet', '10.203.49.2', True)
            self.assertEqual(call.call_args_list[2].args[0], ['wg', 'syncconf', 'wg-fleet', '/dev/stdin'])
            self.assertEqual(len(self.c.result['actions']), 1)

    def portal(self, summary):
        accounts = self.base / 'accounts.json'
        accounts.write_text(json.dumps({'accounts': [{'name': 'test', 'username': 'example', 'password': 'dummy', 'service': '中国移动'}]}))
        self.c.c = {'accounts': str(accounts), 'account_name': 'test', 'address': '10.20.32.13', 'netlogin': '/test.py'}
        return patch.object(h, 'run', return_value=(0, json.dumps({'summary': summary})))

    def test_unknown_and_wrong_active_account_never_logout(self):
        for summary in ({}, {'online': True, 'service': '中国移动', 'userId': 'different'}):
            with self.portal(summary) as call:
                self.c.portal()
                self.assertEqual(call.call_count, 1)
                self.assertEqual(self.c.result['actions'], [])

    def test_offline_auth_stdin_and_verification_required(self):
        with self.portal({'state': 'offline', 'online': False}) as call:
            self.c.portal()
            self.assertEqual(call.call_count, 1)
            self.c.portal()
            self.assertEqual(call.call_count, 3)
            args, kwargs = call.call_args
            self.assertEqual(args[0][-1], 'login-stdin')
            self.assertNotIn('dummy', str(args))
            self.assertIn('dummy', kwargs['stdin'])
            self.assertEqual(self.c.result['checks']['campus_auth']['verification'], 'pending-next-cycle')

    def test_route_repair_is_scoped(self):
        self.c.c = {'gateway': '10.20.32.1', 'interface': 'enp4s0'}
        group = {'unit': 'd408-gateway.service', 'table': 20481, 'priority': 8500, 'mark': '0xd408'}
        with patch.object(self.c, 'enabled', return_value=True), patch.object(h, 'run', side_effect=[(0, ''), (0, ''), (0, '[]'), (0, '')]) as call:
            self.c.routes(group)
            self.assertEqual(call.call_args_list[1].args[0][-2:], ['table', '20481'])
            self.assertEqual(len(self.c.result['actions']), 2)

if __name__ == '__main__':
    unittest.main()
