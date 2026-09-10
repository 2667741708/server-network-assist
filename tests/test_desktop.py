import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
from unittest.mock import patch

from server_network_assist.desktop import Panel, handler_for, change_tunnel, running_instance


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.panel = Panel(Path(self.tmp.name))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.panel))
        self.panel.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, body=None, token=True, origin=True):
        headers = {}
        if token:
            headers['X-Desktop-Token'] = self.panel.token
        if origin:
            headers['Origin'] = self.panel.origin
        data = json.dumps(body).encode() if body is not None else None
        return self.opener.open(Request(self.panel.origin+path, data=data, headers=headers), timeout=5)

    def test_assets_work_offline_and_never_contain_token(self):
        with self.request('/', token=False) as response:
            body = response.read().decode()
            self.assertIn('服务器网络助手', body)
            self.assertNotIn(self.panel.token, body)
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])

    def test_api_requires_token(self):
        with self.assertRaises(HTTPError) as result:
            self.request('/api/health', token=False)
        self.assertEqual(result.exception.code, 403)

    def test_mutation_requires_origin_and_dispatches_once(self):
        with patch('server_network_assist.desktop.change_tunnel') as change:
            with self.assertRaises(HTTPError) as result:
                self.request('/api/action', {'action':'disconnect','tunnel':'test'}, origin=False)
            self.assertEqual(result.exception.code, 403)
            change.assert_not_called()
            with self.request('/api/action', {'action':'connect','tunnel':'test'}) as response:
                self.assertTrue(json.load(response)['ok'])
            change.assert_called_once_with('test','connect')

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(HTTPError) as result:
            self.request('/api/action', {'action':'run','command':'anything'})
        self.assertEqual(result.exception.code, 400)

    def test_nonexistent_tunnel_never_reaches_command(self):
        with patch('server_network_assist.desktop.native_status', return_value={'tunnels': [], 'elevated': True}), patch('server_network_assist.desktop.run') as run:
            with self.assertRaises(ValueError):
                change_tunnel('other-service','disconnect')
            run.assert_not_called()

    def test_reuses_authenticated_instance(self):
        import os
        state = dict(port=self.server.server_port, token=self.panel.token, pid=os.getpid())
        (Path(self.tmp.name)/'desktop-instance.json').write_text(json.dumps(state))
        self.assertEqual(running_instance(Path(self.tmp.name)), state)
        state['token'] = 'wrong-token'
        (Path(self.tmp.name)/'desktop-instance.json').write_text(json.dumps(state))
        self.assertIsNone(running_instance(Path(self.tmp.name)))


if __name__ == '__main__':
    unittest.main()
