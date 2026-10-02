import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from server_network_assist import network_assist_helper as helper


class RouteConflictTests(unittest.TestCase):
    def test_private_excluding_customer_tunnel_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'segmented public'):
            self.check([{'ifname': 'sna-client', 'linkinfo': {'info_kind': 'wireguard'}}],
                       [{'dst': prefix, 'dev': 'sna-client'}
                        for prefix in ('0.0.0.0/2', '64.0.0.0/2', '128.0.0.0/2')])
    def check(self, links, routes):
        replies = [SimpleNamespace(stdout=json.dumps(links)),
                   SimpleNamespace(stdout=json.dumps(routes))]
        with patch.object(helper, 'run', side_effect=replies):
            helper.assert_client_route_available({'interface': 'na1234567890'})

    def test_legacy_policy_table_default_is_rejected(self):
        links = [{'ifname': 'wg-d408', 'linkinfo': {'info_kind': 'wireguard'}}]
        with self.assertRaisesRegex(ValueError, 'wg-d408.*20480'):
            self.check(links, [{'dst': 'default', 'dev': 'wg-d408', 'table': 20480}])

    def test_split_global_route_is_rejected_even_without_link_kind(self):
        with self.assertRaisesRegex(ValueError, 'old-vpn'):
            self.check([], [{'dst': '0.0.0.0/1', 'dev': 'old-vpn'}])

    def test_management_tunnel_and_physical_default_are_allowed(self):
        self.check([{'ifname': 'wg-management', 'linkinfo': {'info_kind': 'wireguard'}}],
                   [{'dst': '10.201.250.0/24', 'dev': 'wg-management'},
                    {'dst': 'default', 'dev': 'enp4s0'}])

    def test_own_tunnel_does_not_block_recovery(self):
        self.check([{'ifname': 'na1234567890', 'linkinfo': {'info_kind': 'wireguard'}}],
                   [{'dst': '0.0.0.0/1', 'dev': 'na1234567890'},
                    {'dst': '128.0.0.0/1', 'dev': 'na1234567890'}])

    def test_enable_conflict_rejection_has_no_mutations(self):
        state = {'interface': 'na1234567890', 'role': 'client'}
        with patch.object(helper, 'load_state', return_value=state), \
                patch.object(helper, 'assert_client_route_available', side_effect=ValueError('conflict')), \
                patch.object(helper, 'save_state') as save, patch.object(helper, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'conflict'):
                helper.enable('test', 120)
            save.assert_not_called()
            run.assert_not_called()
