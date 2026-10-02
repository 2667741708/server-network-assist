import base64
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet
from server_network_assist.client_store import ClientStore, ClientStoreError
from server_network_assist.subscription_archive import SubscriptionArchive


class SubscriptionArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ClientStore(Path(self.tmp.name) / 'test.sqlite3')
        self.cipher = Fernet(Fernet.generate_key())
        self.archive = SubscriptionArchive(self.store, self.cipher)
        self.store.save_source({'id':'source','name':'校园源网','endpoint':'10.20.32.13:51910',
            'address_pool':'10.77.0.0/24','relay_public_key':base64.b64encode(b'x'*32).decode(),
            'relay_interface':'wg-test','egress_interface':'eth0','egress_gateway':'10.20.32.1'})
        self.payload = {'name':'客户','quota_gb':10,'source_ids':['source'],'base_url':'https://service.example'}

    def tearDown(self):
        self.tmp.cleanup()

    def generate(self):
        return self.archive.generate(self.payload, now=1000)

    def test_address_survives_store_reopen_and_is_encrypted(self):
        result = self.generate()
        archive = SubscriptionArchive(ClientStore(self.store.path), self.cipher)
        self.assertEqual(archive.view(result['customer_id'], now=1001)['url'], result['url'])
        with self.store._connect() as db:
            blob = db.execute('SELECT encrypted_url FROM subscription_addresses').fetchone()[0]
        self.assertNotIn(result['url'].encode(), blob)
        self.assertNotIn(result['url'].split('#enroll=')[1].encode(), self.store.path.read_bytes())
        self.assertNotIn('url', archive.statuses(now=1001)[0])
        self.assertEqual(archive.view(result['customer_id'], now=1001)['status'], 'ready')

    def test_encryption_failure_rolls_back_customer_plan_grants_and_token(self):
        class BrokenCipher:
            def encrypt(self, value):
                raise RuntimeError('fixture encryption failed')
        with self.assertRaises(RuntimeError):
            SubscriptionArchive(self.store, BrokenCipher()).generate(self.payload, now=1000)
        for table in ('customers','plans','line_grants','billing_anchors','enrollment_tokens','subscription_addresses'):
            with self.store._connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0], 0, table)

    def test_legacy_address_requires_reissue_without_recreating_customer(self):
        legacy = self.store.generate_monthly_subscription('旧客户',10,['source'],now=1000)
        with self.assertRaisesRegex(ClientStoreError,'无法还原'):
            self.archive.view(legacy['customer_id'],now=1001)
        before = (self.store.list_customers(),self.store.list_plans(),self.store.list_grants())
        renewed = self.archive.reissue(legacy['customer_id'],'https://service.example',now=1002)
        self.assertEqual(before,(self.store.list_customers(),self.store.list_plans(),self.store.list_grants()))
        self.assertEqual(renewed['customer_id'],legacy['customer_id'])
        self.assertEqual(renewed['enrollment_expires_at'],1002+86400)
        with self.assertRaisesRegex(ClientStoreError,'已过期'):
            self.store.enroll_device(legacy['token'],'key1','设备',now=1003)
        device = self.store.enroll_device(renewed['url'].split('#enroll=')[1],'key2','设备',now=1003)
        self.assertEqual(device['customer_id'],legacy['customer_id'])

    def test_reissue_failure_preserves_old_url_and_token(self):
        result = self.generate()
        class BrokenCipher:
            def encrypt(self, value):
                raise RuntimeError('fixture encryption failed')
        with self.assertRaises(RuntimeError):
            SubscriptionArchive(self.store,BrokenCipher()).reissue(result['customer_id'],'https://service.example',now=1001)
        self.assertEqual(self.archive.view(result['customer_id'],now=1002)['url'],result['url'])
        self.store.enroll_device(result['url'].split('#enroll=')[1],'key','设备',now=1002)

    def test_used_address_remains_viewable_but_cannot_add_device_or_reissue(self):
        result = self.generate()
        token = result['url'].split('#enroll=')[1]
        self.store.enroll_device(token,'key','设备',now=1001)
        self.assertEqual(self.archive.view(result['customer_id'],now=1002)['status'],'used')
        self.assertFalse(self.archive.statuses(now=1002)[0]['can_reissue'])
        with self.assertRaisesRegex(ClientStoreError,'绑定设备'):
            self.archive.reissue(result['customer_id'],'https://service.example',now=1002)
        with self.assertRaisesRegex(ClientStoreError,'已使用'):
            self.store.enroll_device(token,'key2','设备2',now=1002)
        self.assertEqual(len(self.store.list_devices()),1)

    def test_expired_and_disabled_status_and_wrong_key(self):
        result = self.generate()
        self.assertEqual(self.archive.view(result['customer_id'],now=1000+86401)['status'],'expired')
        wrong = SubscriptionArchive(self.store,Fernet(Fernet.generate_key()))
        with self.assertRaisesRegex(ClientStoreError,'解密失败'):
            wrong.view(result['customer_id'],now=1001)
        self.store.set_customer_enabled(result['customer_id'],False,now=1001)
        self.assertEqual(self.archive.view(result['customer_id'],now=1002)['status'],'disabled')
        with self.assertRaisesRegex(ClientStoreError,'停用'):
            self.archive.reissue(result['customer_id'],'https://service.example',now=1002)

    def test_reissue_does_not_renew_expired_source_grants(self):
        result = self.generate()
        with self.assertRaisesRegex(ClientStoreError,'不会续期'):
            self.archive.reissue(result['customer_id'],'https://service.example',now=1000+30*86400+1)

    def test_invalid_base_url_never_creates_customer(self):
        with self.assertRaises(ValueError):
            self.archive.generate(self.payload|{'base_url':'https://service.example/unsafe'},now=1000)
        self.assertEqual(self.store.list_customers(),[])
