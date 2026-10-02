import base64
from pathlib import Path
import tempfile
import unittest

from server_network_assist.client_store import ClientStore, ClientStoreError
from server_network_assist.client_tunnel import configuration


class MonthlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ClientStore(Path(self.tmp.name) / 'service.sqlite')
        self.key = base64.b64encode(bytes(range(32))).decode()
        self.source = self.store.save_source({'name':'源机', 'endpoint':'10.20.32.13:51909',
            'relay_public_key':self.key, 'address_pool':'10.213.40.0/24', 'relay_interface':'wg-test',
            'egress_interface':'eth0','egress_gateway':'10.20.32.1','egress_mode':'physical'})

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_presets_enroll_routes_lease_and_expire(self):
        addresses = set()
        for quota in (10, 50, 100, None):
            result = self.store.generate_monthly_subscription('测试客户', quota, [self.source['id']], now=123456)
            device = self.store.enroll_device(result['token'], 'key-' + str(quota), 'test', wireguard_public_key=self.key + str(quota), now=123457)
            grant = self.store.list_grants(result['customer_id'])[0]
            self.assertNotIn(grant['allocated_address'], addresses)
            addresses.add(grant['allocated_address'])
            lease = self.store.issue_lease(device['id'], grant['id'], now=123458)
            self.assertEqual(lease['quota_bytes'], None if quota is None else quota * 1024**3)
            self.assertEqual(self.store.usage(result['customer_id'], now=123459)['period_start'],123456)
            with self.assertRaises(ClientStoreError):
                self.store.issue_lease(device['id'], grant['id'], now=result['expires_at'] + 1)
            with self.assertRaises(ClientStoreError):
                self.store.enroll_device(result['token'], 'replay', 'test', now=123460)

    def test_invalid_source_rolls_back_customer_creation(self):
        with self.assertRaises(ClientStoreError):
            self.store.generate_monthly_subscription('test', 10, ['missing'])
        self.assertEqual(self.store.list_customers(), [])

    def test_unlimited_subscription_has_no_quota_or_speed_limits_and_still_expires(self):
        result = self.store.generate_monthly_subscription('不限额不限速', None, [self.source['id']],
            download_bps=None, upload_bps=None, now=123456)
        customer = self.store.customer(result['customer_id'])
        plan = self.store.get_plan(customer['plan_id'])
        self.assertIsNone(plan['quota_bytes'])
        self.assertIsNone(plan['download_bps'])
        self.assertIsNone(plan['upload_bps'])
        self.assertEqual(result['expires_at'], 123456 + 30 * 86400)

    def test_tunnel_configuration_rejects_command_injection(self):
        lease = {'grant_id':'test', 'relay_public_key':self.key, 'endpoint':'10.20.32.13:51909',
                 'allocated_address':'10.213.40.2/32', 'dns':'223.5.5.5', 'allowed_ips':'0.0.0.0/1,128.0.0.0/1'}
        name, content = configuration(lease, self.key)
        self.assertEqual(len(name),15)
        self.assertNotIn('PostUp',content)
        lease['dns'] = '223.5.5.5\nPostUp=evil'
        with self.assertRaises(ValueError):
            configuration(lease,self.key)

if __name__ == '__main__':
    unittest.main()
