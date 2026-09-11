"""Desktop-owned fleet runtime. Only the desktop's authenticated route calls this.

There is no second web listener and no proxy to an arbitrary management URL.
The same State, pinned SSH routes and rollback-aware network operations used by
the management application operate against a private local database.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import secrets
import threading


class FleetRuntime:
    def __init__(self, data: Path, audit=None):
        self.data = Path(os.environ.get('SNA_FLEET_DATA', data / 'fleet'))
        self.key = Path(os.environ.get('SNA_FLEET_KEY', self.data / 'master.key'))
        self.audit = audit or (lambda *args: None)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True, name='desktop-fleet')
        self.thread.start()
        try:
            self.state = asyncio.run_coroutine_threadsafe(self._initialize(), self.loop).result(30)
        except BaseException:
            self.close()
            raise

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        if pending:
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.close()

    async def _initialize(self):
        from cryptography.fernet import Fernet
        from .app import State
        from .auth import hash_password
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Never generate a replacement key for an existing encrypted vault.
        if not self.key.exists():
            if (self.data / 'console.sqlite3').exists():
                raise ValueError('本地凭据库缺少 master.key，请恢复原密钥；不能重新生成')
            with self.key.open('xb') as stream:
                stream.write(Fernet.generate_key())
            self.key.chmod(0o600)
        credentials = self.data / 'credentials.json'
        if not credentials.exists():
            credentials.write_text(json.dumps({'password_hash': hash_password(secrets.token_urlsafe(48)),
                                               'key_hash': ''}), encoding='utf-8')
            credentials.chmod(0o600)
        servers = self.data / 'servers.json'
        if not servers.exists():
            servers.write_text('[]', encoding='utf-8')
        state = State(self.data, self.key)
        return state

    def close(self):
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=10)

    def request(self, method, path, payload=None):
        future = asyncio.run_coroutine_threadsafe(self._request(method, path, payload or {}), self.loop)
        # Cancellation here could interrupt the existing rollback sequence.
        # Leave the operation running if the caller disconnects; only the
        # application shutdown cancels the runtime.
        return future.result()

    async def _request(self, method, path, p):
        from . import app
        state = self.state
        if method == 'GET':
            if path == '/hosts':
                return {'hosts': state.hosts()}
            if path == '/credentials':
                return {'credentials': state.credentials()}
            if path == '/network':
                return {'profiles': state.network.profiles()}
            if path == '/audit':
                return {'events': state.legacy.store.list_audit(100)}
            raise ValueError('未知的多机管理接口')
        if method != 'POST' or not isinstance(p, dict):
            raise ValueError('请求必须为 JSON 对象')
        if path == '/network/probe':
            ids = p.get('ids') or [h['id'] for h in state.hosts()]
            if not isinstance(ids, list) or len(ids) > 64:
                raise ValueError('探测主机列表无效')
            import time
            return {'results': await asyncio.gather(*(app.network_probe_host(state, str(i)) for i in ids)),
                    'checked_at': int(time.time())}
        # Serialize all fleet mutations against enable/disable and cleanup.
        # A host cannot be edited/deleted halfway through a network operation.
        async with state.network_changes:
            if path == '/host/save':
                result = {'host': state.save_host(p)}
            elif path == '/credential/save':
                result = {'id': state.save_credential(p)}
            elif path == '/credential/delete':
                if any(h['credential_id'] == p.get('id') for h in state.hosts()):
                    raise ValueError('凭据仍被主机使用')
                with state.connect() as db:
                    db.execute('DELETE FROM vault WHERE id=?', (p.get('id'),))
                result = {'ok': True}
            elif path == '/host/delete':
                target = p.get('id')
                if any(h['jump_id'] == target for h in state.hosts()):
                    raise ValueError('该主机仍被用作跳板')
                if any(row['gateway_id'] == target or target in row['client_ids'] for row in state.network.profiles()):
                    raise ValueError('该主机仍被网络共享方案使用')
                with state.connect() as db:
                    db.execute('DELETE FROM hosts WHERE id=?', (target,))
                result = {'ok': True}
            elif path == '/host/inspect':
                host = state.host(p.get('id'))
                if not host:
                    raise ValueError('请先保存主机再读取指纹')
                async with state.operations, app.ssh_route(state, host['id'], stop_before=True) as tunnel:
                    key = await asyncio.wait_for(app.asyncssh.get_server_host_key(
                        host['address'], port=host['port'], tunnel=tunnel, config=[]), 15)
                if not key:
                    raise ValueError('未读取到主机公钥')
                # Reading a key does NOT pin it. The user must compare and save.
                result = {'host_key': key.export_public_key().decode().strip(),
                          'fingerprint': key.get_fingerprint('sha256')}
            elif path == '/network/profile/save':
                result = {'profile': state.network.save(p)}
            elif path == '/network/profile/delete':
                state.network.delete(str(p.get('id', '')))
                result = {'ok': True}
            elif path == '/network/profile/enable':
                result = {'profile': await app.network_enable_profile(state, str(p.get('id', '')))}
            elif path == '/network/profile/disable':
                result = {'profile': await app.network_disable_profile(state, str(p.get('id', '')))}
            elif path == '/network/helper/install':
                ids = p.get('ids', [])
                if not isinstance(ids, list) or not ids or len(ids) > 32:
                    raise ValueError('请选择 1–32 台要安装辅助程序的主机')
                result = {'results': []}
                for node in dict.fromkeys(str(i) for i in ids):
                    result['results'].append({'id': node, **await app.network_install_helper(state, node)})
            else:
                raise ValueError('未知的多机管理接口')
            state.audit('desktop' + path.replace('/', '_'))
            self.audit('fleet' + path.replace('/', '_'), 'success')
            return result
