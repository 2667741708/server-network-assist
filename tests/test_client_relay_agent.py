import tempfile
import unittest
from pathlib import Path

from server_network_assist.client_relay_agent import sync


class FakeControl:
    def __init__(self):
        self.calls = []

    def request(self, path, value=None):
        self.calls.append((path, value))
        if path.startswith('/relay/v1/reconcile'):
            return {'generated_at': 1234, 'policies': [{'interface': 'wg-customer'}]}
        return {'ok': True}


class FakeManager:
    def __init__(self):
        self.measured = []
        self.reconciled = None

    def status(self):
        return {'peers': {'lease-1': {'status': 'active', 'policy': {'interface': 'wg-customer'},
            'accounted_received': 12, 'accounted_sent': 34}}}

    def measure(self, interface):
        self.measured.append(interface)

    def reconcile(self, policies):
        self.reconciled = policies
        return {'peers': {'lease-1': {}}}


class RelayAgentTests(unittest.TestCase):
    def test_sync_reports_real_totals_then_reconciles(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = FakeManager()
            control = FakeControl()
            cursor = Path(folder) / 'cursor'
            result = sync(manager, control, cursor, 'wg-customer')
            self.assertEqual(manager.measured, ['wg-customer'])
            self.assertEqual(manager.reconciled, [{'interface': 'wg-customer'}])
            self.assertEqual(cursor.read_text(encoding='ascii'), '1234')
            usage = [value for path, value in control.calls if path == '/relay/v1/usage']
            self.assertEqual(usage[0]['rx_total'], 12)
            self.assertEqual(usage[0]['tx_total'], 34)
            self.assertIn('lease-1', result['peers'])


if __name__ == '__main__':
    unittest.main()
