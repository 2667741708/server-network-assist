import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from server_network_assist.client_relay import (
    RelayManager, firewall_script, parse_wg_transfer, plan_apply, validate_policy,
)


KEY = "A" * 43 + "="


def policy(**changes):
    value = {"peer_id": "customer-1", "public_key": KEY, "address": "100.64.77.2/32",
             "interface": "wg-customer", "egress_interface": "eth0",
             "egress_policy": "source_proxy",
             "customer_subnet": "100.64.77.0/24", "management_subnets": ["10.0.0.0/8"],
             "download_bps": 20_000_000, "upload_bps": 5_000_000,
             "quota_bytes": 10_000, "expires_at": 2_000_000_000, "enabled": True}
    value.update(changes)
    return value


class FakeRunner:
    def __init__(self, transfer=""):
        self.calls = []
        self.transfer = transfer
        self.fail_at = None
        self.allowed_ips = {}
        self.routes = set()

    def __call__(self, argv, input_text=None, check=True):
        self.calls.append((argv, input_text, check))
        if self.fail_at is not None and len(self.calls) == self.fail_at and check:
            raise subprocess.CalledProcessError(1, argv)
        stdout = ""
        if argv[:2] == ["wg", "show"] and len(argv) > 3 and argv[3] == "allowed-ips":
            interface = argv[2]
            stdout = "".join(f"{key}\t{address}\n" for (dev, key), address in self.allowed_ips.items()
                             if dev == interface)
        elif argv[:2] == ["wg", "show"] and len(argv) > 3 and argv[3] == "transfer":
            stdout = self.transfer
        elif argv[:3] == ["ip", "-4", "route"] and "dev" in argv:
            interface = argv[argv.index("dev") + 1]
            stdout = "".join(f"{address} dev {dev}\n" for dev, address in sorted(self.routes)
                             if dev == interface)
        if argv[:2] == ["wg", "set"] and len(argv) >= 6:
            interface, key = argv[2], argv[4]
            if argv[-1:] == ["remove"]:
                self.allowed_ips.pop((interface, key), None)
            elif "allowed-ips" in argv:
                self.allowed_ips[(interface, key)] = argv[-1]
        elif argv[:3] == ["ip", "route", "replace"] and len(argv) >= 6:
            self.routes.add((argv[5], argv[3]))
        elif argv[:3] == ["ip", "route", "del"] and len(argv) >= 6:
            self.routes.discard((argv[5], argv[3]))
        return subprocess.CompletedProcess(argv, 0, stdout, "")


class RelayPolicyTests(unittest.TestCase):
    def test_schema_rejects_commands_unknown_fields_and_bad_address(self):
        for changed in ({"command": "rm -rf /"}, {"interface": "wg0;id"},
                        {"address": "100.64.88.2/32"}, {"management_subnets": ["100.64.77.0/25"]}):
            with self.assertRaises(ValueError):
                validate_policy(policy(**changed))

    def test_planner_uses_argv_and_separate_peer_filters(self):
        commands = plan_apply(policy(), initialize=True)
        self.assertTrue(all(isinstance(command, list) for command in commands))
        flattened = json.dumps(commands)
        self.assertIn("allowed-ips", flattened)
        self.assertIn("100.64.77.2/32", flattened)
        self.assertIn("20000000bit", flattened)
        self.assertIn("5000000bit", flattened)

    def test_per_peer_plan_does_not_replace_shared_qdisc(self):
        flattened = json.dumps(plan_apply(policy()))
        self.assertNotIn('"qdisc", "replace"', flattened)

    def test_firewall_isolates_management_customers_and_wrong_egress(self):
        script = firewall_script(policy())
        self.assertIn("ip daddr @management drop", script)
        self.assertIn('oifname "wg-customer"', script)
        self.assertIn('oifname != "eth0" drop', script)
        self.assertIn('ip daddr @customers drop', script)
        self.assertIn("masquerade", script)

    def test_unlimited_peer_has_no_classification_or_policing(self):
        commands = plan_apply(policy(download_bps=None, upload_bps=None))
        self.assertFalse(any(command[0] == 'tc' for command in commands))
        self.assertTrue(any(command[0] == 'wg' for command in commands))
        self.assertTrue(any(command[0] == 'nft' for command in commands))

    def test_transfer_parser_is_strict(self):
        self.assertEqual(parse_wg_transfer(f"{KEY}\t12\t34\n")[KEY]["sent_bytes"], 34)
        with self.assertRaises(ValueError):
            parse_wg_transfer("bad row")

    def test_class_identifiers_are_valid_hexadecimal_minors(self):
        for peer in ['customer-1', 'lease_e9c8a975e1be4de9bacda8ec660417f1']:
            commands = plan_apply(policy(peer_id=peer))
            classes = [argv[argv.index('classid') + 1] for argv in commands if 'classid' in argv]
            self.assertTrue(all(0 < int(value.split(':')[1], 16) < 65536 for value in classes))


class RelayManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runner = FakeRunner()
        self.now = 1_900_000_000
        self.manager = RelayManager(Path(self.temp.name), self.runner, lambda: self.now)
        self.root = patch("server_network_assist.client_relay.require_linux_root")
        self.root.start()

    def tearDown(self):
        self.root.stop()
        self.temp.cleanup()

    def test_apply_records_active_policy_and_never_uses_shell(self):
        result = self.manager.apply(policy())
        self.assertEqual(result["status"], "active")
        self.assertTrue(all(isinstance(call[0], list) for call in self.runner.calls))
        stored = self.manager.status()["peers"]["customer-1"]
        self.assertEqual(stored["policy"]["address"], "100.64.77.2/32")

    def test_failed_apply_revokes_and_records_recovery(self):
        self.runner.fail_at = 4
        with self.assertRaisesRegex(RuntimeError, "recovery state"):
            self.manager.apply(policy())
        record = self.manager.status()["peers"]["customer-1"]
        self.assertEqual(record["status"], "recovery_required")
        self.assertTrue(any("remove" in call[0] for call in self.runner.calls))

    def test_renewal_metadata_preserves_kernel_session_and_counters(self):
        self.manager.apply(policy())
        self.runner.transfer = f"{KEY}\t100\t200\n"
        self.manager.measure('wg-customer')
        before = self.manager.status()['peers']['customer-1']
        self.runner.calls.clear()
        updated = self.manager.apply(policy(expires_at=2_000_000_300, quota_bytes=20_000))
        self.assertEqual(self.runner.calls, [], 'Renewal must not remove routes, TC or WG state')
        self.assertEqual(updated['used_bytes'], before['used_bytes'])
        self.assertEqual(updated['last_received'], before['last_received'])
        self.assertEqual(updated['policy']['expires_at'], 2_000_000_300)

    def test_unchanged_reconcile_does_not_reset_present_peer_or_route(self):
        self.manager.apply(policy())
        self.runner.calls.clear()
        self.manager.reconcile([policy()])
        self.assertFalse(any(call[0][:2] == ['wg', 'set'] for call in self.runner.calls))
        self.assertFalse(any(call[0][:3] == ['ip', 'route', 'replace'] for call in self.runner.calls))

    def test_signed_snapshot_refresh_does_not_reset_peer_or_route(self):
        self.manager.apply(policy())
        self.runner.calls.clear()
        self.manager.reconcile([policy(snapshot_at=self.now + 1,
                                       offline_deadline=self.now + 901)])
        self.assertFalse(any(call[0][:2] == ['wg', 'set'] for call in self.runner.calls))
        self.assertFalse(any(tuple(call[0][:3]) in {
            ('ip', 'route', 'replace'), ('ip', 'route', 'del')}
            for call in self.runner.calls))

    def test_unchanged_reconcile_repairs_only_missing_peer_or_route(self):
        self.manager.apply(policy())
        self.runner.allowed_ips.clear()
        self.runner.routes.clear()
        self.runner.calls.clear()
        self.manager.reconcile([policy()])
        self.assertTrue(any(call[0][:2] == ['wg', 'set'] for call in self.runner.calls))
        self.assertTrue(any(call[0][:3] == ['ip', 'route', 'replace'] for call in self.runner.calls))

    def test_switch_to_unlimited_removes_only_previous_peer_tc_limits(self):
        self.manager.apply(policy())
        self.runner.calls.clear()
        result = self.manager.apply(policy(download_bps=None, upload_bps=None))
        self.assertEqual(result['status'], 'active')
        tc = [call[0] for call in self.runner.calls if call[0][0] == 'tc']
        self.assertEqual(len([command for command in tc if 'del' in command]), 3)
        self.assertFalse(any('police' in command for command in tc))
        self.assertFalse(any('filter' in command and 'replace' in command for command in tc))

    def test_measure_accumulates_deltas_and_survives_counter_reset(self):
        self.manager.apply(policy())
        self.runner.transfer = f"{KEY}\t100\t200\n"
        self.manager.measure("wg-customer")
        self.runner.transfer = f"{KEY}\t160\t260\n"
        measured = self.manager.measure("wg-customer")
        record = measured["peers"]["customer-1"]
        self.assertEqual(record["used_bytes"], 420)
        self.assertEqual(record["delta_received"], 60)
        self.assertEqual(record["delta_sent"], 60)
        self.runner.transfer = f"{KEY}\t5\t7\n"
        self.manager.measure("wg-customer")
        self.assertEqual(self.manager.status()["peers"]["customer-1"]["used_bytes"], 432)

    def test_reconcile_revokes_expired_and_quota_peers(self):
        self.manager.apply(policy(quota_bytes=100))
        state = self.manager.store.load()
        state["peers"]["customer-1"]["used_bytes"] = 100
        self.manager.store.save(state)
        result = self.manager.reconcile()
        self.assertEqual(result["peers"]["customer-1"]["status"], "revoked")
        self.assertEqual(result["peers"]["customer-1"]["reason"], "quota_exhausted")

    def test_offline_deadline_revokes_long_lived_authorization(self):
        self.manager.apply(policy(expires_at=self.now + 30 * 86400,
                                 snapshot_at=self.now,
                                 offline_deadline=self.now + 900))
        self.now += 901
        result = self.manager.expire()
        self.assertEqual(result['peers']['customer-1']['status'], 'revoked')
        self.assertEqual(result['peers']['customer-1']['reason'], 'expired')

    def test_desired_reconcile_revokes_missing_peer(self):
        self.manager.apply(policy())
        result = self.manager.reconcile([])
        self.assertEqual(result["peers"]["customer-1"]["reason"], "not_desired")

    def test_revoke_releases_stale_reservation_when_kernel_peer_is_already_absent(self):
        self.manager.apply(policy())
        state = self.manager.store.load()
        state['peers']['customer-1']['status'] = 'recovery_required'
        self.manager.store.save(state)
        original = self.manager.runner

        def peer_already_absent(argv, **kwargs):
            if argv[:3] == ['wg', 'set', 'wg-customer'] and argv[-1:] == ['remove']:
                return subprocess.CompletedProcess(argv, 1, '', 'peer not found')
            if argv[:4] == ['wg', 'show', 'wg-customer', 'peers']:
                return subprocess.CompletedProcess(argv, 0, '', '')
            return original(argv, **kwargs)

        self.manager.runner = peer_already_absent
        result = self.manager.revoke('customer-1', 'not_desired')
        self.assertEqual(result['status'], 'revoked')
        self.assertNotIn('error', result)

    def test_revoke_releases_stale_reservation_when_nft_element_is_absent(self):
        self.manager.apply(policy())
        state = self.manager.store.load()
        state['peers']['customer-1']['status'] = 'recovery_required'
        self.manager.store.save(state)
        original = self.manager.runner

        def nft_element_already_absent(argv, **kwargs):
            if argv[:3] == ['nft', 'delete', 'element']:
                return subprocess.CompletedProcess(argv, 1, '', 'No such file or directory')
            if argv[:4] == ['nft', '-j', 'list', 'set']:
                return subprocess.CompletedProcess(argv, 0, '{"nftables":[{"set":{"elem":[]}}]}', '')
            return original(argv, **kwargs)

        self.manager.runner = nft_element_already_absent
        result = self.manager.revoke('customer-1', 'not_desired')
        self.assertEqual(result['status'], 'revoked')

    def test_nft_cleanup_matches_full_address_and_retains_real_failures(self):
        for elements, expected in [(['100.64.77.20'], 'revoked'),
                                   ([{'elem': '100.64.77.2', 'timeout': 30}],
                                    'recovery_required')]:
            with self.subTest(elements=elements):
                self.manager.apply(policy())
                state = self.manager.store.load()
                state['peers']['customer-1']['status'] = 'recovery_required'
                self.manager.store.save(state)
                original = self.manager.runner

                def nft_delete_failed(argv, **kwargs):
                    if argv[:3] == ['nft', 'delete', 'element']:
                        return subprocess.CompletedProcess(argv, 1, '', 'No such file or directory')
                    if argv[:4] == ['nft', '-j', 'list', 'set']:
                        content = json.dumps({'nftables': [{'set': {'elem': elements}}]})
                        return subprocess.CompletedProcess(argv, 0, content, '')
                    return original(argv, **kwargs)

                self.manager.runner = nft_delete_failed
                result = self.manager.revoke('customer-1', 'not_desired')
                self.assertEqual(result['status'], expected)
                self.manager.runner = original

    def test_second_peer_does_not_replace_shared_qdisc(self):
        self.manager.apply(policy())
        before = len(self.runner.calls)
        self.manager.apply(policy(peer_id="customer-2", public_key="B" * 43 + "=",
                                  address="100.64.77.3/32"))
        later = self.runner.calls[before:]
        self.assertFalse(any(call[0][0:3] == ["tc", "qdisc", "replace"] for call in later))

    def test_retry_reuses_existing_kernel_qdiscs_without_active_state(self):
        original = self.runner
        def existing(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[:4] == ['tc', '-j', 'qdisc', 'show']:
                result.stdout = json.dumps([{'kind': 'htb', 'handle': '1:'}, {'kind': 'ingress', 'handle': 'ffff:'}])
            return result
        self.manager.runner = existing
        self.manager.apply(policy())
        self.assertFalse(any(call[0][:3] == ['tc', 'qdisc', 'replace'] for call in original.calls))

    def test_old_lease_cleanup_does_not_remove_new_shared_peer_or_route(self):
        self.manager.apply(policy())
        state = self.manager.store.load()
        state['peers']['old-lease'] = {'policy': policy(peer_id='old-lease'), 'status': 'recovery_required'}
        self.manager.store.save(state)
        before = len(self.runner.calls)
        self.manager.revoke('old-lease', 'not_desired')
        cleanup = self.runner.calls[before:]
        self.assertFalse(any(call[0][0] in {'wg', 'ip', 'nft'} for call in cleanup))

    def test_revoked_leases_are_not_measured_again(self):
        self.manager.apply(policy())
        self.manager.revoke('customer-1')
        self.runner.transfer = f'{KEY}\t100\t200\n'
        self.manager.measure('wg-customer')
        self.assertEqual(self.manager.store.load()['peers']['customer-1']['used_bytes'], 0)


if __name__ == "__main__":
    unittest.main()
