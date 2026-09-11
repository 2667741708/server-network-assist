"""Desktop APIs tested against real private fleet storage and fake OS controls."""
import asyncio
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

import test_desktop as base_desktop
from server_network_assist.desktop_observability import TrafficSampler, EventLog, release_check, RELEASES
from server_network_assist.desktop_proxy import ProxySettings, validate_server


class ExtendedAPITests(base_desktop.DesktopTests):
    def tearDown(self):
        self.panel.close()
        super().tearDown()

    def get_json(self, path, body=None):
        with self.request(path, body) as response:
            return json.load(response)

    def test_new_routes_reject_missing_token_and_foreign_origin(self):
        for path in ('/api/fleet/hosts', '/api/proxy', '/api/diagnostics', '/api/updates'):
            with self.assertRaises(HTTPError) as exc:
                self.request(path, token=False)
            self.assertEqual(exc.exception.code, 403)
        for path in ('/api/fleet/credential/save', '/api/proxy/save'):
            request = Request(self.panel.origin + path, data=b'{}',
                              headers={'X-Desktop-Token': self.panel.token, 'Origin': 'https://evil.invalid'})
            with self.assertRaises(HTTPError) as exc:
                self.opener.open(request)
            self.assertEqual(exc.exception.code, 403)
        self.assertIsNone(self.panel.fleet_runtime)

    def test_fleet_rejects_wrong_host_header(self):
        request = Request(self.panel.origin + '/api/fleet/hosts',
                          headers={'X-Desktop-Token': self.panel.token, 'Host': 'evil.invalid'})
        with self.assertRaises(HTTPError) as exc:
            self.opener.open(request)
        self.assertEqual(exc.exception.code, 403)
        self.assertIsNone(self.panel.fleet_runtime)

    def test_real_fleet_crud_encrypts_secrets_and_guards_references(self):
        self.assertEqual(self.get_json('/api/fleet/hosts'), {'hosts': []})
        secret = 'example-test-password-never-return'
        credential = self.get_json('/api/fleet/credential/save', {'name': 'test', 'kind': 'password', 'secret': secret})['id']
        metadata = self.get_json('/api/fleet/credentials')
        self.assertNotIn(secret, json.dumps(metadata))
        self.assertNotIn(secret.encode(), (Path(self.tmp.name) / 'fleet/console.sqlite3').read_bytes())
        hosts = []
        for name, address in [('gateway', '192.0.2.10'), ('client', '192.0.2.11')]:
            hosts.append(self.get_json('/api/fleet/host/save', {'name': name, 'address': address,
                               'username': 'tester', 'credential_id': credential})['host'])
        plan = self.get_json('/api/fleet/network/profile/save', {'name': 'share', 'gateway_id': hosts[0]['id'],
                         'client_ids': [hosts[1]['id']], 'proxy_mode': 'share', 'proxy_host': '127.0.0.1', 'proxy_port': 7897})['profile']
        self.assertEqual(plan['proxy_mode'], 'share')
        self.assertEqual(self.get_json('/api/fleet/network')['profiles'][0]['id'], plan['id'])
        for path, target in [('host', hosts[0]['id']), ('credential', credential)]:
            with self.assertRaises(HTTPError):
                self.get_json('/api/fleet/' + path + '/delete', {'id': target})
        # A saved but unpinned host cannot be contacted by the trusted SSH route.
        from server_network_assist.app import ssh_route
        async def check_pin():
            async with ssh_route(self.panel.fleet_runtime.state, hosts[0]['id']):
                self.fail('unverified host must never connect')
        with patch('server_network_assist.app.asyncssh.connect') as connect:
            with self.assertRaisesRegex(ValueError, '指纹'):
                asyncio.run(check_pin())
            connect.assert_not_called()
        self.get_json('/api/fleet/network/profile/delete', {'id': plan['id']})
        for host in hosts:
            self.get_json('/api/fleet/host/delete', {'id': host['id']})
        self.get_json('/api/fleet/credential/delete', {'id': credential})
        self.assertEqual(self.get_json('/api/fleet/hosts')['hosts'], [])
        self.assertEqual(self.get_json('/api/fleet/credentials')['credentials'], [])

    def test_enable_dispatches_existing_rollback_aware_operation(self):
        self.get_json('/api/fleet/hosts')
        with patch('server_network_assist.app.network_enable_profile', new_callable=AsyncMock,
                   side_effect=ValueError('切换失败，已回退')) as enable:
            with self.assertRaises(HTTPError) as exc:
                self.get_json('/api/fleet/network/profile/enable', {'id': 'plan'})
            self.assertIn('已回退', json.load(exc.exception)['error'])
            enable.assert_awaited_once_with(self.panel.fleet_runtime.state, 'plan')

    def test_pending_recovery_keeps_host_connection_immutable(self):
        credential = self.get_json('/api/fleet/credential/save', {'name': 'test', 'kind': 'password', 'secret': 'test'})['id']
        hosts = [self.get_json('/api/fleet/host/save', {'name': name, 'address': address,
                 'username': 'tester', 'credential_id': credential})['host']
                 for name, address in [('gateway', '192.0.2.10'), ('client', '192.0.2.11')]]
        plan = self.get_json('/api/fleet/network/profile/save', {'name': 'share', 'gateway_id': hosts[0]['id'],
                 'client_ids': [hosts[1]['id']]})['profile']
        network = self.panel.fleet_runtime.state.network
        plan.update(state='error', cleanup_pending=True)
        network.put(plan)
        with self.assertRaises(HTTPError):
            self.get_json('/api/fleet/host/save', {**hosts[1], 'address': '192.0.2.99'})
        self.assertEqual(self.panel.fleet_runtime.state.host(hosts[1]['id'])['address'], '192.0.2.11')
        # Harmless labels remain editable while the exact recovery route is retained.
        result = self.get_json('/api/fleet/host/save', {**hosts[1], 'name': 'client recovery'})
        self.assertEqual(result['host']['name'], 'client recovery')

    def test_proxy_restore_api_dispatch_and_no_untrusted_body(self):
        with patch.object(self.panel.proxy_settings, 'restore', return_value={'ok': True}) as restore:
            self.assertTrue(self.get_json('/api/proxy/restore', {'id': 'proxy-before-test.json'})['ok'])
            restore.assert_called_once_with('proxy-before-test.json')
        with self.assertRaises(HTTPError):
            self.get_json('/api/proxy/save', ['not-an-object'])

    def test_diagnostics_keep_logs_when_native_status_fails(self):
        self.panel.events.add('test', 'success')
        with patch.object(self.panel, 'status', side_effect=RuntimeError('native tools missing')):
            result = self.get_json('/api/diagnostics')
        self.assertIsNone(result['status'])
        self.assertEqual(result['events'][0]['action'], 'test')
        self.assertEqual(result['guidance'][0]['code'], 'status-unavailable')

    def test_existing_vault_missing_key_is_not_silently_replaced(self):
        from server_network_assist.desktop_fleet import FleetRuntime
        folder = Path(self.tmp.name) / 'fleet'
        folder.mkdir()
        (folder / 'console.sqlite3').write_bytes(b'existing-vault')
        with self.assertRaisesRegex(ValueError, '原密钥'):
            FleetRuntime(Path(self.tmp.name))
        self.assertFalse((folder / 'master.key').exists())


class TrafficTests(unittest.TestCase):
    def row(self, sent, received, telemetry=True, active=True):
        return {'name': 'wg', 'sent': sent, 'received': received, 'telemetry': telemetry, 'active': active}

    def test_deltas_reset_missing_and_bounded_history(self):
        sampler = TrafficSampler(capacity=3)
        self.assertIsNone(sampler.sample([self.row(100, 100)], now=1)['sent_per_second'])
        value = sampler.sample([self.row(200, 300)], now=3)
        self.assertEqual(value['sent_per_second'], 50)
        self.assertEqual(value['received_per_second'], 100)
        self.assertIsNone(sampler.sample([self.row(1, 2)], now=4)['sent_per_second'])
        self.assertIsNone(sampler.sample([self.row(0, 0, False)], now=5)['sent_per_second'])
        self.assertIsNone(sampler.sample([self.row(10, 10)], now=6)['sent_per_second'])
        value = sampler.sample([self.row(10, 10)], now=7)
        self.assertEqual(value['sent_per_second'], 0)
        self.assertEqual(len(value['history']), 3)
        self.assertIsNone(sampler.sample([], now=8)['sent_per_second'])

    def test_logging_failure_does_not_block_and_memory_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            events = EventLog(Path(folder) / 'missing-folder', capacity=2)
            for i in range(3):
                events.add(str(i), 'success')
            self.assertEqual([e['action'] for e in events.read()], ['2', '1'])


class MemoryProxy(ProxySettings):
    def __init__(self, data):
        super().__init__(data, True, Mock())
        self.value = {'platform': 'windows', 'values': {'ProxyEnable': [1, 4],
                        'ProxyServer': ['127.0.0.1:7897', 1], 'AutoConfigURL': ['https://example.invalid/proxy.pac', 1]}}
        self.fail_next = False

    def snapshot(self):
        return copy.deepcopy(self.value)

    def _apply(self, snap):
        self.value = {'platform': snap['platform'], 'values': copy.deepcopy(snap['values'])}
        if self.fail_next:
            self.fail_next = False
            raise OSError('simulated write failure')


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proxy = MemoryProxy(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_backup_restore_preserves_pac_and_creates_undo(self):
        original = self.proxy.snapshot()
        result = self.proxy.save({'enabled': True, 'server': '192.0.2.2:8080', 'bypass': '<local>;localhost'})
        self.assertEqual(self.proxy.value['values']['ProxyServer'][0], '192.0.2.2:8080')
        restored = self.proxy.restore(result['backup_id'])
        self.assertEqual(self.proxy.value, original)
        self.assertNotEqual(restored['backup_id'], result['backup_id'])
        self.assertEqual(len(restored['backups']), 2)

    def test_backup_failure_blocks_mutation(self):
        original = self.proxy.snapshot()
        with patch.object(self.proxy, 'backup', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.proxy.save({'enabled': False})
        self.assertEqual(self.proxy.value, original)

    def test_failed_write_restores_original(self):
        original = self.proxy.snapshot()
        self.proxy.fail_next = True
        with self.assertRaisesRegex(ValueError, '已恢复'):
            self.proxy.save({'enabled': False})
        self.assertEqual(self.proxy.value, original)

    def test_noop_write_does_not_report_success(self):
        with patch.object(self.proxy, '_apply'):
            with self.assertRaisesRegex(ValueError, '复检不一致'):
                self.proxy.save({'enabled': False})

    def test_silent_rollback_failure_is_not_reported_as_restored(self):
        calls = []
        def apply(snap):
            calls.append(snap)
            if len(calls) == 1:
                self.proxy.value = copy.deepcopy(snap)
                raise OSError('partial write')
            # The OS accepts a rollback request without changing the settings.
        with patch.object(self.proxy, '_apply', side_effect=apply):
            with self.assertRaisesRegex(ValueError, '自动恢复失败'):
                self.proxy.save({'enabled': False})
        self.assertEqual(len(self.proxy.backups()), 1)

    def test_restore_rejects_traversal_and_foreign_platform(self):
        with self.assertRaises(ValueError):
            self.proxy.restore('../master.key')
        identifier = self.proxy.backup({'platform': 'gnome', 'values': {}})
        with self.assertRaises(ValueError):
            self.proxy.restore(identifier)

    def test_invalid_manual_servers_rejected(self):
        for value in ('user:password@example.com:80', 'example.com:0', 'localhost:99999', 'x:80; calc.exe', 'https://x:80/path'):
            with self.assertRaises(ValueError):
                validate_server(value)
        self.assertEqual(validate_server('http=127.0.0.1:80;https=[::1]:443'), 'http=127.0.0.1:80;https=[::1]:443')

    def test_headless_linux_explains_unsupported_scope(self):
        with patch.dict('os.environ', {}, clear=True):
            value = ProxySettings(Path(self.tmp.name), False, Mock()).read()
        self.assertFalse(value['supported'])
        self.assertIn('会话', value['error'])

    def test_gnome_save_readback_and_restore_use_user_settings_only(self):
        settings = {'org.gnome.system.proxy/mode': "'none'", 'org.gnome.system.proxy/autoconfig-url': "''",
                    'org.gnome.system.proxy/ignore-hosts': "['localhost']", 'org.gnome.system.proxy/use-same-proxy': 'false'}
        for protocol in ('http', 'https', 'socks'):
            settings['org.gnome.system.proxy.'+protocol+'/host'] = "''"
            settings['org.gnome.system.proxy.'+protocol+'/port'] = '0'
        original = settings.copy()
        def gsettings(args):
            self.assertEqual(args[0], 'gsettings')
            key = args[2] + '/' + args[3]
            if args[1] == 'get':
                return settings[key]
            self.assertEqual(args[1], 'set')
            settings[key] = args[4]
            return ''
        proxy = ProxySettings(Path(self.tmp.name), False, gsettings)
        with patch.dict('os.environ', {'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/test/bus'}):
            result = proxy.save({'server': '127.0.0.1:7897', 'enabled': True, 'bypass': ''})
            self.assertTrue(result['proxy']['enabled'])
            self.assertEqual(settings['org.gnome.system.proxy/ignore-hosts'], '[]')
            self.assertEqual(settings['org.gnome.system.proxy.http/host'], "'127.0.0.1'")
            proxy.restore(result['backup_id'])
        self.assertEqual(settings, original)


class ReleaseTests(unittest.TestCase):
    def response(self, release):
        response = io.BytesIO(json.dumps(release).encode())
        response.url = 'https://api.github.com/repos/2667741708/server-network-assist/releases/latest'
        return response

    def test_only_official_assets_and_semantic_version(self):
        release = {'tag_name': 'v0.10.0', 'html_url': RELEASES+'/tag/v0.10.0', 'assets': [
            {'name': 'windows.zip', 'browser_download_url': RELEASES+'/download/v0.10.0/windows.zip'},
            {'name': 'bad.exe', 'browser_download_url': 'https://evil.invalid/bad.exe'}]}
        with patch('urllib.request.urlopen', return_value=self.response(release)):
            value = release_check('0.3.0')
        self.assertTrue(value['available'])
        self.assertEqual(len(value['assets']), 1)
        self.assertEqual(value['install_mode'], 'download-and-run-installer')

    def test_no_release_is_not_installable_update(self):
        with patch('urllib.request.urlopen', side_effect=HTTPError('url', 404, '', {}, None)):
            value = release_check('0.3.0')
        self.assertFalse(value['available'])
        self.assertIsNone(value['latest'])
        self.assertIn('尚无正式', value['error'])


if __name__ == '__main__':
    unittest.main()
