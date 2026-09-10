import contextlib
import sqlite3
import tempfile
import unittest
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pathlib import Path

from server_network_assist.network_assist import (NetworkStore, interface_name, parse_probe,
    probe_command, helper_command, detect_platform, windows_file_command)
from server_network_assist import network_assist_helper as linux_helper


class FakeState:
    def __init__(self, path):
        self.db = path
        self._hosts = [
            {"id": "gateway", "name": "Gateway", "address": "10.0.0.10"},
            {"id": "client", "name": "Client", "address": "10.0.0.11"},
        ]

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.db)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def hosts(self):
        return self._hosts

    def route(self, host_id):
        return [next(value for value in self._hosts if value["id"] == host_id)]


class NetworkAssistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = NetworkStore(FakeState(Path(self.tmp.name) / "console.sqlite3"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_profile_and_runtime_payloads(self):
        profile = self.store.save({"name": "Lab assist", "gateway_id": "gateway", "client_ids": ["client"], "port": 52001, "preserve_routes": "10.0.0.0/24"})
        self.assertEqual(profile["interface"], interface_name(profile["id"]))
        gateway, clients = self.store.runtime_payloads(profile, {"gateway": "gateway-public", "client": "client-public"})
        self.assertEqual(gateway["peers"][0]["allowed_ip"], "10.213.1.2/32")
        self.assertIn("10.0.0.0/24", clients["client"]["preserve_routes"])

    def test_rejects_conflicting_roles_and_active_edits(self):
        with self.assertRaisesRegex(ValueError, "出口机不能"):
            self.store.save({"name": "bad", "gateway_id": "gateway", "client_ids": ["gateway"]})
        profile = self.store.save({"name": "one", "gateway_id": "gateway", "client_ids": ["client"]})
        self.store.set_state(profile["id"], "enabled")
        with self.assertRaisesRegex(ValueError, "请先断开"):
            self.store.save({**profile, "port": 52003})

    def test_probe_parser(self):
        result = parse_probe("os=Linux\nhostname=node\ndns=1\nhttp_code=204\ninternet=1\nhelper=0\n")
        self.assertTrue(result["internet"])
        self.assertFalse(result["helper"])
        self.assertIn("connectivitycheck.gstatic.com", probe_command())

    def test_windows_proxy_failure_does_not_hide_working_tunnel(self):
        result = parse_probe(json.dumps({'os': 'Windows', 'internet': True,
            'system_internet': False, 'diagnosis': 'system_proxy_failed',
            'proxy_enabled': True, 'client_supported': True, 'gateway_supported': False}))
        self.assertTrue(result['internet'])
        self.assertFalse(result['system_internet'])
        self.assertEqual(result['diagnosis'], 'system_proxy_failed')
        self.assertFalse(parse_probe(json.dumps({'ssh': True}), 1)['ssh'])

    def test_windows_helper_uses_native_file_and_validates_arguments(self):
        command = helper_command('enable', None, 'profile_1', '120', platform='windows')
        self.assertIn('-File "C:/ProgramData/ServerNetworkAssist/windows_helper.ps1" enable profile_1 120', command)
        self.assertNotIn('sudo', command)
        with self.assertRaises(ValueError):
            helper_command('enable', None, 'profile;whoami', platform='windows')
        with self.assertRaises(ValueError):
            windows_file_command('C:/Users/$bad/script.ps1')
        self.assertIn('"C:/Users/a b/probe.ps1"', windows_file_command('/C:/Users/a b/probe.ps1'))

    def test_ubuntu_gateway_accepts_client_subnet_and_rejects_outside_peer(self):
        payload = {'role': 'gateway', 'profile_id': 'test', 'interface': 'na1234567890',
            'address': '10.213.1.1/24', 'subnet': '10.213.1.0/24', 'port': 51919,
            'peers': [{'public_key': 'A' * 43 + '=', 'allowed_ip': '10.213.1.2/32'}]}
        with patch.object(linux_helper, 'private_key', return_value='private'), patch.object(linux_helper, 'default_uplink', return_value='eth0'):
            self.assertIn('AllowedIPs = 10.213.1.2/32', linux_helper.wg_config(payload))
            payload['peers'][0]['allowed_ip'] = '10.214.1.2/32'
            with self.assertRaisesRegex(ValueError, 'invalid gateway peer'):
                linux_helper.wg_config(payload)


class PlatformTests(unittest.IsolatedAsyncioTestCase):
    async def test_windows_git_shell_does_not_override_native_windows(self):
        connection = SimpleNamespace(run=AsyncMock(side_effect=[
            SimpleNamespace(exit_status=0, stdout='MINGW64_NT-10.0\n'),
            SimpleNamespace(exit_status=0, stdout='Win32NT\r\n')]))
        self.assertEqual(await detect_platform(connection), 'windows')
        self.assertEqual(connection.run.await_count, 2)

    async def test_linux_fallback_and_unknown_os_rejected(self):
        connection = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(exit_status=0, stdout='Linux\n')))
        self.assertEqual(await detect_platform(connection), 'linux')
        # Do not invoke Windows PowerShell from an Ubuntu/WSL SSH session.
        connection.run.assert_awaited_once()
        connection.run = AsyncMock(return_value=SimpleNamespace(exit_status=1, stdout=''))
        with self.assertRaises(ValueError):
            await detect_platform(connection)


if __name__ == "__main__":
    unittest.main()
