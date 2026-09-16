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

    def __call__(self, argv, input_text=None, check=True):
        self.calls.append((argv, input_text, check))
        if self.fail_at is not None and len(self.calls) == self.fail_at and check:
            raise subprocess.CalledProcessError(1, argv)
        stdout = self.transfer if argv[:2] == ["wg", "show"] else ""
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

    def test_transfer_parser_is_strict(self):
        self.assertEqual(parse_wg_transfer(f"{KEY}\t12\t34\n")[KEY]["sent_bytes"], 34)
        with self.assertRaises(ValueError):
            parse_wg_transfer("bad row")


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

    def test_desired_reconcile_revokes_missing_peer(self):
        self.manager.apply(policy())
        result = self.manager.reconcile([])
        self.assertEqual(result["peers"]["customer-1"]["reason"], "not_desired")

    def test_second_peer_does_not_replace_shared_qdisc(self):
        self.manager.apply(policy())
        before = len(self.runner.calls)
        self.manager.apply(policy(peer_id="customer-2", public_key="B" * 43 + "=",
                                  address="100.64.77.3/32"))
        later = self.runner.calls[before:]
        self.assertFalse(any(call[0][0:3] == ["tc", "qdisc", "replace"] for call in later))


if __name__ == "__main__":
    unittest.main()
