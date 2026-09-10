import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server_network_assist.network_assist import NetworkStore, interface_name, parse_probe, probe_command


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


if __name__ == "__main__":
    unittest.main()
