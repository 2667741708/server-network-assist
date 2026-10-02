import base64
import json
import unittest

from server_network_assist.network_assist import parse_probe, probe_command
from server_network_assist.route_diagnostics import runtime_profiles, return_target
from server_network_assist.route_diagnostics import routing_evidence


class RouteDiagnosticsTests(unittest.TestCase):
    def test_segmented_public_tunnel_is_visible_without_false_lan_warning(self):
        result = routing_evidence([{'dst': '0.0.0.0/2', 'dev': 'sna-client'},
                                   {'dst': '64.0.0.0/2', 'dev': 'sna-client'},
                                   {'dst': '128.0.0.0/2', 'dev': 'sna-client'}],
                                  return_route='10.80.62.217 via 10.20.32.1 dev enp4s0')
        self.assertEqual(result['global_tunnels'], ['sna-client'])
        self.assertEqual(result['route_warnings'], [])
    def test_split_route_and_wrong_return_are_visible(self):
        encoded = base64.b64encode(json.dumps([{'dst': '0.0.0.0/1', 'dev': 'wg-fleet'}]).encode()).decode()
        result = parse_probe(f'os=Linux\npublic_route=1.1.1.1 dev wg-fleet\nreturn_route=10.80.62.217 dev wg-fleet\nroutes_json={encoded}\n')
        self.assertEqual(result['global_tunnels'], ['wg-fleet'])
        self.assertEqual(len(result['route_warnings']), 2)
        self.assertEqual(result['public_route'], '1.1.1.1 dev wg-fleet')
        self.assertIn('10.80.62.217', result['return_route'])

    def test_remote_disabled_state_does_not_overwrite_intent(self):
        profile = {'id': 'p', 'gateway_id': 'source', 'client_ids': ['client'],
                   'state': 'enabled', 'interface': 'na1234567890'}
        probes = {node: {'checked_at': 1000, 'ssh': True, 'helper': True,
                        'assist': [{'profile_id': 'p', 'active': False, 'desired': False}]}
                  for node in ('source', 'client')}
        result = runtime_profiles([profile], probes, now=1000)[0]
        self.assertEqual(result['state'], 'enabled')
        self.assertEqual(result['runtime']['status'], 'disabled')
        self.assertTrue(result['runtime']['mismatch'])
        self.assertEqual(runtime_profiles([profile], probes, now=1201)[0]['runtime']['status'], 'unknown')

    def test_mixed_state_reports_each_node(self):
        profile = {'id': 'p', 'gateway_id': 'source', 'client_ids': ['client'], 'state': 'enabled'}
        probes = {'source': {'checked_at': 1000, 'ssh': True, 'helper': True, 'assist': []}}
        result = runtime_profiles([profile], probes, now=1000)[0]
        self.assertEqual(result['runtime']['status'], 'mixed')
        self.assertEqual(result['runtime']['nodes'][0]['status'], 'not_configured')
        self.assertEqual(result['runtime']['nodes'][1]['status'], 'unknown')

    def test_return_target_is_validated_before_shell_construction(self):
        self.assertIn('SNA_RETURN_TARGET=10.80.62.217', probe_command('10.80.62.217'))
        for target in ('1.1.1.1;touch /tmp/x', '::1', '0.0.0.0', '224.0.0.1'):
            with self.assertRaises(ValueError):
                return_target(target)
