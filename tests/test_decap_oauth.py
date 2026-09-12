import http.cookiejar
import importlib.util
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


MODULE_PATH = Path(__file__).parents[1] / "services" / "decap-oauth" / "decap_oauth_proxy.py"
spec = importlib.util.spec_from_file_location("decap_oauth_proxy", MODULE_PATH)
oauth = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = oauth
spec.loader.exec_module(oauth)


class DecapOAuthTests(unittest.TestCase):
    def setUp(self):
        self.old_env = os.environ.copy()
        os.environ.update(
            {
                "GITHUB_OAUTH_ID": "client-id",
                "GITHUB_OAUTH_SECRET": "client-secret",
                "OAUTH_PUBLIC_BASE_URL": "https://whm12.art/projects/oauth",
                "CMS_ORIGIN": "https://whm12.art",
                "OAUTH_STATE_SECRET": "x" * 48,
                "GITHUB_REPO": "2667741708/server-network-assist",
            }
        )
        self.config = oauth.Config.from_env()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_state_is_signed_and_expires(self):
        state = oauth.make_state(self.config, now=100)
        oauth.verify_state(self.config, state, state, now=100)
        with self.assertRaises(oauth.OAuthError):
            oauth.verify_state(self.config, state, state, now=701)
        with self.assertRaises(oauth.OAuthError):
            oauth.verify_state(self.config, state + "x", state, now=100)

    def test_auth_redirect_sets_cookie_and_fixed_callback(self):
        server = oauth.OAuthHTTPServer(("127.0.0.1", 0), self.config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/auth?provider=github&site_id=whm12"
            request = Request(url)
            class NoRedirect(HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None

            with self.assertRaises(HTTPError) as context:
                build_opener(NoRedirect()).open(request)
            response = context.exception
            self.assertEqual(response.code, 302)
            location = response.headers["Location"]
            self.assertIn("client_id=client-id", location)
            self.assertIn("redirect_uri=https%3A%2F%2Fwhm12.art%2Fprojects%2Foauth%2Fcallback", location)
            self.assertIn("state=", location)
            self.assertIn("Path=/projects/oauth", response.headers["Set-Cookie"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_health_is_public_but_does_not_disclose_secrets(self):
        server = oauth.OAuthHTTPServer(("127.0.0.1", 0), self.config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with build_opener().open(f"http://127.0.0.1:{server.server_port}/healthz") as response:
                body = json.loads(response.read())
            self.assertEqual(body, {"ok": True})
            self.assertNotIn("client", json.dumps(body))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
