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
        device = self.store.enroll_device(
            token, "device-public-key", "笔记本", wireguard_public_key="A" * 43 + "=",
            device_id="laptop", now=7201)
        return token, device

    def grant(self, *, expires_at=None):
        return self.store.grant_line("alice", "线路 A", "opaque-tunnel-a",
                                     "relay.example:51820", grant_id="line-a", now=7200,
                                     relay_public_key="R" * 43 + "=", allocated_address="10.77.0.2/32",
                                     dns="10.77.0.1", relay_interface="wg-commercial",
                                     egress_interface="eth0", expires_at=expires_at)

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

    def test_renewal_preserves_peer_and_counter_epoch_but_rotates_secret(self):
        self.enroll()
        self.grant(expires_at=8000)
        lease = self.store.issue_lease('laptop', 'line-a', now=7300)
        self.store.record_usage(lease['token'], 'first', 100, 200, now=7301)
        renewed = self.store.renew_lease('laptop', lease['id'], now=7490)
        self.assertEqual(renewed['id'], lease['id'])
        self.assertEqual(renewed['wireguard_public_key'], lease['wireguard_public_key'])
        self.assertEqual(renewed['allocated_address'], lease['allocated_address'])
        self.assertEqual(renewed['issued_at'], lease['issued_at'])
        self.assertEqual(renewed['expires_at'], 8000)
        self.assertEqual((renewed['last_rx'], renewed['last_tx']), (100, 200))
        self.assertNotEqual(renewed['token'], lease['token'])
        self.assertEqual(len(self.store.list_leases()), 1)
        self.assertEqual(self.store.record_usage(renewed['token'], 'second', 120, 220, now=7491)['used_bytes'], 340)
        with self.assertRaises(ClientStoreError):
            self.store.validate_lease(lease['token'], now=7491)

    def test_renewal_rejects_expired_revoked_and_wrong_owner_without_replacing(self):
        self.enroll()
        self.grant(expires_at=7550)
        lease = self.store.issue_lease('laptop', 'line-a', now=7300)
        for device, at in [('other', 7400), ('laptop', 7600)]:
            with self.assertRaises(ClientStoreError):
                self.store.renew_lease(device, lease['id'], now=at)
        self.store.revoke('lease', lease['id'], now=7401)
        with self.assertRaises(ClientStoreError):
            self.store.renew_lease('laptop', lease['id'], now=7402)
        self.assertEqual(len(self.store.list_leases()), 1)

    def test_renewal_caps_lease_at_grant_expiry(self):
        self.enroll()
        self.grant(expires_at=7550)
        lease = self.store.issue_lease('laptop', 'line-a', now=7300)
        self.assertEqual(lease['expires_at'], 7550)
        renewed = self.store.renew_lease('laptop', lease['id'], now=7490)
        self.assertEqual(renewed['id'], lease['id'])
        self.assertEqual(renewed['expires_at'], 7550)

    def test_renewal_and_token_validation_recheck_disabled_plan(self):
        self.enroll()
        self.grant(expires_at=8000)
        lease = self.store.issue_lease('laptop', 'line-a', now=7300)
        with self.store._connect() as db:
            db.execute('UPDATE plans SET enabled=0 WHERE id=?', ('basic',))
        with self.assertRaises(ClientStoreError):
            self.store.renew_lease('laptop', lease['id'], now=7400)
        with self.assertRaises(ClientStoreError):
            self.store.validate_lease(lease['token'], now=7400)

    def test_monthly_subscription_lease_expires_with_its_30_day_grant(self):
        created_at = 10_000
        subscription = self.store.generate_monthly_subscription(
            '30-day proxy', None, [], access_mode='public_proxy',
            validity_days=30, now=created_at)
        self.assertEqual(self.store.get_plan(subscription['plan_id'])['lease_seconds'], 30 * 86400)
        self.store.enroll_device(subscription['token'], 'device-public-key', 'proxy',
                                 device_id='proxy-device', now=created_at + 1)
        grant = self.store.list_grants(subscription['customer_id'])[0]
        lease = self.store.issue_lease('proxy-device', grant['id'], now=created_at + 2)
        self.assertEqual(lease['expires_at'], subscription['expires_at'])

    def test_long_finite_subscription_uses_its_actual_expiry_without_30_day_cap(self):
        created_at = 30_000
        duration = 3650 * 86400
        subscription = self.store.generate_monthly_subscription(
            'long-term proxy', None, [], access_mode='public_proxy',
            validity_days=3650, now=created_at)
        plan = self.store.get_plan(subscription['plan_id'])
        self.assertEqual(plan['lease_seconds'], duration)
        self.store.enroll_device(subscription['token'], 'device-public-key', 'proxy',
                                 device_id='long-proxy-device', now=created_at + 1)
        grant = self.store.list_grants(subscription['customer_id'])[0]
        lease = self.store.issue_lease('long-proxy-device', grant['id'], now=created_at + 2)
        self.assertEqual(lease['expires_at'], subscription['expires_at'])
        self.assertGreater(lease['expires_at'], created_at + 30 * 86400)

    def test_create_plan_accepts_lease_longer_than_30_days(self):
        duration = 3650 * 86400
        plan = self.store.create_plan('long-term plan', plan_id='long-term-plan',
                                      lease_seconds=duration, now=50_000)
        self.assertEqual(plan['lease_seconds'], duration)

    def test_unlimited_term_keeps_infinite_lease_without_token_rotation(self):
        created_at = 20_000
        plan = self.store.create_plan('indefinite', plan_id='indefinite', now=created_at)
        self.assertEqual(plan['lease_seconds'], 2**63 - 1)
        customer = self.store.create_customer('unlimited term', plan['id'],
                                              customer_id='indefinite-customer', now=created_at)
        token = self.store.create_enrollment_token(customer['id'], now=created_at)
        self.store.enroll_device(token, 'device-public-key', 'proxy',
                                 device_id='indefinite-device', now=created_at + 1)
        grant = self.store.grant_line(customer['id'], 'proxy', '', 'proxy.example:443',
                                      access_mode='public_proxy', grant_id='indefinite-grant',
                                      now=created_at)
        lease = self.store.issue_lease('indefinite-device', grant['id'], now=created_at + 2)
        self.assertEqual(lease['expires_at'], 2**63 - 1)
        before = self.store.validate_lease(lease['token'], now=created_at + 100 * 365 * 86400)
        self.assertEqual(before['id'], lease['id'])
        with self.store._connect() as db:
            digest_before = db.execute('SELECT token_digest FROM leases WHERE id=?',
                                       (lease['id'],)).fetchone()[0]
        renewed = self.store.renew_lease(
            'indefinite-device', lease['id'], current_token=lease['token'],
            now=created_at + 100)
        self.assertEqual(renewed['id'], lease['id'])
        self.assertEqual(renewed['token'], lease['token'])
        self.assertEqual(renewed['expires_at'], 2**63 - 1)
        with self.store._connect() as db:
            digest_after = db.execute('SELECT token_digest FROM leases WHERE id=?',
                                      (lease['id'],)).fetchone()[0]
        self.assertEqual(digest_after, digest_before)
        self.assertEqual(self.store.validate_lease(lease['token'],
                         now=created_at + 100 * 365 * 86400)['id'], lease['id'])

    def test_unlimited_lease_migration_keeps_existing_token_and_peer(self):
        self.enroll()
        self.grant()
        lease = self.store.issue_lease('laptop', 'line-a', ttl=900, now=7300)
        self.store.record_usage(lease['token'], 'before-migration', 100, 200, now=7301)
        with self.store._connect() as db:
            before = db.execute('SELECT token_digest, last_rx, last_tx FROM leases WHERE id=?',
                                (lease['id'],)).fetchone()
        with self.assertRaisesRegex(ClientStoreError, '当前令牌'):
            self.store.renew_lease('laptop', lease['id'], current_token='not-the-current-token',
                                   now=8300)
        renewed = self.store.renew_lease(
            'laptop', lease['id'], current_token=lease['token'], now=8300)
        self.assertEqual(renewed['id'], lease['id'])
        self.assertEqual(renewed['token'], lease['token'])
        self.assertEqual(renewed['expires_at'], 2**63 - 1)
        self.assertEqual(renewed['wireguard_public_key'], lease['wireguard_public_key'])
        self.assertEqual(renewed['allocated_address'], lease['allocated_address'])
        with self.store._connect() as db:
            after = db.execute('SELECT token_digest, last_rx, last_tx FROM leases WHERE id=?',
                               (lease['id'],)).fetchone()
        self.assertEqual(tuple(after), tuple(before))

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

    def test_relay_and_client_samples_do_not_double_count_reversed_directions(self):
        self.enroll()
        self.grant()
        lease = self.store.issue_lease("laptop", "line-a", now=7300)
        first = self.store.record_usage_by_lease(lease["id"], "relay-1", 100, 50, now=7301)
        self.assertEqual(first["used_bytes"], 150)
        second = self.store.record_usage_by_device("laptop", lease["id"], "client-1", 50, 100, now=7302)
        self.assertEqual(second["used_bytes"], 150)
        third = self.store.record_usage_by_device("laptop", lease["id"], "client-2", 70, 130, now=7303)
        self.assertEqual(third["used_bytes"], 200)

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
