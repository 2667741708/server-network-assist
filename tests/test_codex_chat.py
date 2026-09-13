import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import asyncssh
from cryptography.fernet import Fernet

from server_network_assist.app import State, _codex_command, _codex_result
from server_network_assist.legacy import initialize
from server_network_assist.ssh_import import import_aliases


class CodexChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data = root / 'data'
        initialize(self.data)
        self.key = root / 'master.key'
        self.key.write_bytes(Fernet.generate_key())
        self.state = State(self.data, self.key)
        credential = self.state.save_credential({'name':'fixture','kind':'password','secret':'fixture'})
        host_key = asyncssh.generate_private_key('ssh-ed25519')
        self.host = self.state.save_host({'id':'fixture-host','name':'Fixture','address':'127.0.0.1',
            'port':22,'username':'tester','credential_id':credential,
            'host_key':host_key.export_public_key().decode(), 'codex_enabled':True})

    def tearDown(self):
        self.tmp.cleanup()

    def test_session_messages_and_host_dependency(self):
        session = self.state.create_codex_session({'host_id':self.host['id'], 'title':'Test',
            'workspace':'/srv/project', 'sandbox':'read-only'})
        _, message_id, prompt = self.state.begin_codex_turn(session['id'], 'hello')
        self.assertEqual(prompt, 'hello')
        self.state.finish_codex_turn(session['id'], message_id, 'world', 'remote-thread')
        result = self.state.codex_session(session['id'])
        self.assertEqual([item['role'] for item in result['messages']], ['user','assistant'])
        self.assertEqual(result['messages'][-1]['content'], 'world')
        self.assertEqual(result['remote_thread_id'], 'remote-thread')
        self.assertEqual(len(self.state.codex_sessions(self.host['id'])), 1)

    def test_command_and_jsonl_result(self):
        session = {'remote_thread_id':'', 'sandbox':'workspace-write', 'workspace':'/srv/a folder',
                   'model':'gpt-5.6-sol', 'reasoning_effort':'high', 'service_tier':'priority'}
        command = _codex_command('linux', session)
        self.assertIn('$HOME/.local/bin', command)
        self.assertIn("'/srv/a folder'", command)
        self.assertIn("gpt-5.6-sol", command)
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertIn('service_tier="priority"', command)
        resumed = _codex_command('windows', {**session, 'workspace':'', 'remote_thread_id':'abc-123'})
        self.assertIn('resume', resumed)
        stdout = '\n'.join([
            json.dumps({'type':'thread.started','thread_id':'abc-123'}),
            json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'answer'}}),
        ])
        self.assertEqual(_codex_result(stdout, '', 0), ('answer','abc-123'))
        with self.assertRaises(ValueError):
            _codex_result('', 'not installed', 127)


class SSHImportTests(unittest.TestCase):
    def test_explicit_alias_import_uses_pinned_key_and_encrypted_vault(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / 'data'
            initialize(data)
            key_file = root / 'master.key'
            key_file.write_bytes(Fernet.generate_key())
            state = State(data, key_file)
            client_key = asyncssh.generate_private_key('ssh-ed25519')
            identity = root / 'id_ed25519'
            identity.write_text(client_key.export_private_key().decode(), encoding='utf-8')
            server_key = asyncssh.generate_private_key('ssh-ed25519').export_public_key().decode().strip()
            known_hosts = root / 'known_hosts'
            known_hosts.write_text(f'192.0.2.10 {server_key}\n', encoding='utf-8')
            config = root / 'config'
            config.write_text('\n'.join(['Host fixture','  HostName 192.0.2.10','  User tester',
                '  Port 22',f'  IdentityFile {identity}',f'  UserKnownHostsFile {known_hosts}']), encoding='utf-8')
            imported = import_aliases(state, config, ['fixture'], fallback_known_hosts=known_hosts)
            self.assertEqual(imported[0]['address'], '192.0.2.10')
            self.assertTrue(imported[0]['codex_enabled'])
            self.assertNotIn('PRIVATE KEY', state.db.read_bytes().decode(errors='ignore'))


if __name__ == '__main__':
    unittest.main()
