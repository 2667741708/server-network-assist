import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_network_assist import clash_control


class ClashControlTests(unittest.TestCase):
    def test_mode_tun_and_rule_edits_preserve_readable_yaml(self):
        source = "mode: rule\nmixed-port: 7897\ntun:\n  enable: false\nrules:\n- MATCH,DIRECT\n"
        changed = clash_control.replace_mode(source, "global")
        changed = clash_control.replace_tun(changed, True)
        changed = clash_control.prepend_rule(
            changed, clash_control.validate_rule("DOMAIN-SUFFIX,openai.com,Proxy"))
        self.assertIn("mode: global", changed)
        self.assertIn("  enable: true", changed)
        self.assertLess(changed.index("openai.com"), changed.index("MATCH,DIRECT"))

    def test_apply_backs_up_and_rolls_back_failed_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.yaml"
            original = "mode: rule\nexternal-controller: 127.0.0.1:9097\nrules:\n- MATCH,DIRECT\n"
            config.write_text(original, encoding="utf-8")
            with patch.object(clash_control, "candidates", return_value=[config]), \
                 patch.object(clash_control, "api", side_effect=ValueError("reload failed")):
                with self.assertRaisesRegex(ValueError, "reload failed"):
                    clash_control.apply({"action": "rule_add", "rule": "DOMAIN,openai.com,DIRECT"})
            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertEqual(len(list(Path(folder).glob("*.sna-backup-*"))), 1)

    def test_rejects_unsafe_rule_and_controller(self):
        for value in ("DOMAIN,openai.com,DIRECT\nMATCH,DIRECT", "SCRIPT,x,DIRECT", ""):
            with self.assertRaises(ValueError):
                clash_control.validate_rule(value)

    def test_proxy_selection_is_limited_to_controller_choices(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.yaml"
            config.write_text("external-controller: 127.0.0.1:9097\n", encoding="utf-8")
            calls = []

            def fake_api(base, secret, method, path, payload=None):
                calls.append((method, path, payload))
                if method == "GET" and path == "/proxies":
                    return {"proxies": {"节点选择": {"all": ["香港 A", "DIRECT"]}}}
                if method == "GET" and path == "/version":
                    return {"version": "test"}
                if method == "GET" and path == "/configs":
                    return {"mode": "rule", "tun": {"enable": False}}
                if method == "GET" and path == "/rules":
                    return {"rules": []}
                return {}

            with patch.object(clash_control, "candidates", return_value=[config]), \
                 patch.object(clash_control, "api", side_effect=fake_api), \
                 patch.object(clash_control, "system_proxy", return_value={"supported": False}):
                result = clash_control.apply({"action": "proxy_select", "group": "节点选择", "name": "香港 A"})
                self.assertEqual(result["groups"][0]["now"], "")
                self.assertIn(("PUT", "/proxies/%E8%8A%82%E7%82%B9%E9%80%89%E6%8B%A9", {"name": "香港 A"}), calls)
                with self.assertRaisesRegex(ValueError, "不存在"):
                    clash_control.apply({"action": "proxy_select", "group": "节点选择", "name": "不存在"})


if __name__ == "__main__":
    unittest.main()
