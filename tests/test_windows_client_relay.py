import base64
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_network_assist.windows_client_relay import WindowsRelayManager, validate_windows_policy


KEY = base64.b64encode(b'w' * 32).decode()


def policy(**changes):
    value = {'peer_id': 'lease-1', 'public_key': KEY, 'address': '10.203.1.2/32',
             'interface': 'wg-customer', 'egress_interface': 'Ethernet',
             'customer_subnet': '10.203.1.0/24', 'management_subnets': ['10.20.0.0/16'],
             'download_bps': 10_000_000, 'upload_bps': 5_000_000,
             'quota_bytes': 1000, 'expires_at': 2000, 'enabled': True}
    value.update(changes)
    return value


class Runner:
    def __init__(self):
        self.calls = []
        self.transfer = ''

    def __call__(self, argv, check=True):
        self.calls.append((argv, check))
        stdout = self.transfer if 'transfer' in argv else ''
        return subprocess.CompletedProcess(argv, 0, stdout, '')


class WindowsRelayTests(unittest.TestCase):
    def setUp(self):
        self.admin = patch('server_network_assist.windows_client_relay.require_windows_admin')
        self.admin.start()

    def tearDown(self):
        self.admin.stop()

    def test_validation_accepts_windows_adapter_names_and_rejects_overlap(self):
        self.assertEqual(validate_windows_policy(policy(egress_interface='以太网 2'))['egress_interface'], '以太网 2')
        with self.assertRaises(ValueError):
            validate_windows_policy(policy(management_subnets=['10.203.1.0/25']))

    def test_apply_reports_rate_limit_as_unsupported(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = Runner()
            manager = WindowsRelayManager(Path(folder), runner=runner, clock=lambda: 100, helper=Path('helper.ps1'), wg_path='wg.exe')
            result = manager.apply(policy())
            self.assertEqual(result['status'], 'active')
            self.assertEqual(result['capabilities']['download_rate_limit'], 'unsupported')
            self.assertTrue(any(call[0][0] == 'wg.exe' and 'allowed-ips' in call[0] for call in runner.calls))

    def test_measure_uses_wireguard_counters_and_survives_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = Runner()
            manager = WindowsRelayManager(Path(folder), runner=runner, clock=lambda: 100, helper=Path('helper.ps1'), wg_path='wg.exe')
            manager.apply(policy())
            runner.transfer = f'{KEY}\t100\t50\n'
            manager.measure('wg-customer')
            runner.transfer = f'{KEY}\t5\t7\n'
            status = manager.measure('wg-customer')
            record = status['peers']['lease-1']
            self.assertEqual(record['accounted_received'], 105)
            self.assertEqual(record['accounted_sent'], 57)

    def test_reconcile_revokes_missing_peer_immediately(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = Runner()
            manager = WindowsRelayManager(Path(folder), runner=runner, clock=lambda: 100, helper=Path('helper.ps1'), wg_path='wg.exe')
            manager.apply(policy())
            result = manager.reconcile([])
            self.assertEqual(result['peers']['lease-1']['status'], 'revoked')
            self.assertTrue(any(call[0][:4] == ['wg.exe', 'set', 'wg-customer', 'peer'] and call[0][-1] == 'remove' for call in runner.calls))


if __name__ == '__main__':
    unittest.main()
