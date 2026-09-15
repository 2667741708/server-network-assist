import unittest

from server_network_assist.control_cli import host_roles, parser, resolve


class FakeNetwork:
    def profiles(self):
        return [{"id": "p1", "name": "实验室借网", "gateway_id": "server",
                 "client_ids": ["client"], "state": "enabled"}]


class FakeState:
    network = FakeNetwork()


class ControlCliTests(unittest.TestCase):
    def test_reports_server_and_client_roles(self):
        self.assertEqual(host_roles(FakeState(), "server")[0]["role"], "server")
        self.assertEqual(host_roles(FakeState(), "client")[0]["role"], "client")
        self.assertEqual(host_roles(FakeState(), "other"), [])

    def test_resolves_name_or_id_and_rejects_ambiguity(self):
        values = [{"id": "a", "name": "alpha"}, {"id": "b", "name": "beta"}]
        self.assertEqual(resolve(values, "alpha", "主机")["id"], "a")
        self.assertEqual(resolve(values, "b", "主机")["name"], "beta")
        with self.assertRaises(ValueError):
            resolve(values, "missing", "主机")

    def test_parses_network_and_clash_commands(self):
        create = parser().parse_args(["profile-create", "--name", "test", "--server", "s",
                                      "--client", "c1", "--client", "c2", "--endpoint", "host"])
        self.assertEqual(create.client, ["c1", "c2"])
        self.assertEqual(create.proxy_host, "127.0.0.1")
        clash = parser().parse_args(["clash-tun", "--host", "titan", "--value", "on"])
        self.assertEqual((clash.command, clash.value), ("clash-tun", "on"))


if __name__ == "__main__":
    unittest.main()
