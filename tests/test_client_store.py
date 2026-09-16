import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server_network_assist.client_crypto import (
    private_key_text, public_key_text, secret_digest, sign_payload, verify_payload,
)
from server_network_assist.client_store import ClientStore, ClientStoreError


class ClientStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ClientStore(Path(self.tmp.name) / "commercial.sqlite3")
        self.plan = self.store.create_plan("10G", plan_id="basic", quota_bytes=10_000,
                                           period_seconds=3600, max_devices=1,
                                           lease_seconds=300, now=7200)
        self.customer = self.store.create_customer("客户甲", "basic", customer_id="alice", now=7200)

    def tearDown(self):
        self.tmp.cleanup()

    def enroll(self):
        token = self.store.create_enrollment_token("alice", ttl=600, now=7200)
        device = self.store.enroll_device(token, "device-public-key", "笔记本", device_id="laptop", now=7201)
        return token, device

    def grant(self):
        return self.store.grant_line("alice", "线路 A", "opaque-tunnel-a",
                                     "relay.example:51820", grant_id="line-a", now=7200,
                                     relay_public_key="relay-key", allocated_address="10.77.0.2/32",
                                     dns="10.77.0.1", relay_interface="wg-commercial",
                                     egress_interface="eth0")

    def test_enrollment_token_is_one_time_digest_and_device_limit_is_enforced(self):
        token, device = self.enroll()
        self.assertEqual(device["customer_id"], "alice")
        db = sqlite3.connect(self.store.path)
        try:
            saved = db.execute("SELECT digest FROM enrollment_tokens").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(saved, secret_digest(token, "enrollment"))
        self.assertNotEqual(saved, token)
        with self.assertRaisesRegex(ClientStoreError, "已使用"):
            self.store.enroll_device(token, "another-key", "另一台", now=7202)
        second = self.store.create_enrollment_token("alice", now=7202)
        with self.assertRaisesRegex(ClientStoreError, "设备数量"):
            self.store.enroll_device(second, "another-key", "另一台", now=7203)

    def test_nonce_cannot_be_replayed_but_can_be_reused_in_another_scope(self):
        nonce = "1234567890abcdef"
        self.store.consume_nonce("device:laptop", nonce, now=100)
        with self.assertRaisesRegex(ClientStoreError, "重放"):
            self.store.consume_nonce("device:laptop", nonce, now=101)
        self.store.consume_nonce("device:desktop", nonce, now=101)
        self.store.consume_nonce("device:laptop", nonce, now=500)

    def test_short_lease_hides_bearer_secret_and_honors_immediate_revocation(self):
        self.enroll()
        self.grant()
        lease = self.store.issue_lease("laptop", "line-a", ttl=120, now=7300)
        self.assertEqual(lease["expires_at"], 7420)
        self.assertNotIn("token_digest", lease)
        self.assertEqual(self.store.validate_lease(lease["token"], now=7419)["tunnel"], "opaque-tunnel-a")
        active = self.store.active_leases(now=7310)[0]
        self.assertEqual(active["allocated_address"], "10.77.0.2/32")
        self.assertEqual(active["egress_interface"], "eth0")
        self.store.revoke("grant", "line-a", now=7350)
        with self.assertRaisesRegex(ClientStoreError, "撤销"):
            self.store.validate_lease(lease["token"], now=7351)

    def test_customer_and_device_must_own_grant(self):
        self.enroll()
        other = self.store.create_customer("客户乙", "basic", customer_id="bob", now=7200)
        grant = self.store.grant_line(other["id"], "乙线路", "b", "relay:1", grant_id="line-b", now=7200)
        with self.assertRaisesRegex(ClientStoreError, "未获授权"):
            self.store.issue_lease("laptop", grant["id"], now=7300)

    def test_usage_reports_are_monotonic_idempotent_and_periodic(self):
        self.enroll()
        self.grant()
        lease = self.store.issue_lease("laptop", "line-a", now=7300)
        first = self.store.record_usage(lease["token"], "report-1", 1000, 500, now=7301)
        self.assertEqual(first["used_bytes"], 1500)
        duplicate = self.store.record_usage(lease["token"], "report-1", 9999, 9999, now=7302)
        self.assertEqual(duplicate["used_bytes"], 1500)
        second = self.store.record_usage(lease["token"], "report-2", 1500, 700, now=7303)
        self.assertEqual(second["used_bytes"], 2200)
        with self.assertRaisesRegex(ClientStoreError, "回退"):
            self.store.record_usage(lease["token"], "report-3", 1400, 700, now=7304)
        self.assertEqual(self.store.usage("alice", now=7304)["remaining_bytes"], 7800)

    def test_quota_blocks_new_validation_after_report_reaches_limit(self):
        self.enroll()
        self.grant()
        lease = self.store.issue_lease("laptop", "line-a", now=7300)
        self.store.record_usage(lease["token"], "full", 6000, 4000, now=7301)
        with self.assertRaisesRegex(ClientStoreError, "撤销|额度"):
            self.store.validate_lease(lease["token"], now=7302)
        self.assertEqual(self.store.lease_reconciliation(since=7301, now=7302)["revoked"][0]["id"], lease["id"])

    def test_public_lists_and_disable_atomically_revoke_leases(self):
        self.enroll()
        self.grant()
        lease = self.store.issue_lease("laptop", "line-a", now=7300)
        self.assertEqual(len(self.store.list_plans()), 1)
        self.assertEqual(len(self.store.list_customers()), 1)
        self.assertEqual(len(self.store.list_devices("alice")), 1)
        self.assertEqual(len(self.store.list_grants("alice")), 1)
        self.assertEqual(len(self.store.list_leases(device_id="laptop")), 1)
        self.store.set_device_enabled("laptop", False, now=7310)
        self.assertEqual(self.store.active_leases(now=7311), [])
        self.assertEqual(self.store.list_leases()[0]["revoked_at"], 7310)


class ClientCryptoTests(unittest.TestCase):
    def test_ed25519_payload_round_trip_and_domain_separated_digests(self):
        private = Ed25519PrivateKey.generate()
        payload = {"device": "one", "issued_at": 123}
        signature = sign_payload(private_key_text(private), payload)
        self.assertTrue(verify_payload(public_key_text(private.public_key()), payload, signature))
        self.assertFalse(verify_payload(public_key_text(private.public_key()), {**payload, "issued_at": 124}, signature))
        self.assertNotEqual(secret_digest("same", "lease"), secret_digest("same", "enrollment"))


if __name__ == "__main__":
    unittest.main()
