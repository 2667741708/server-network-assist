"""Encrypted subscription-address archive for the unified commercial line.

The deployed control line stores only token digests; this module adds the
admin-facing address archive (encrypted at rest) plus its lifecycle:
generate, view, reissue, expire (destroy an unused address) and restore
(extend an unused address by 24 h). All tables are additive.
"""
import time

from cryptography.fernet import InvalidToken

from .client_crypto import secret_digest
from .client_online import parse_enrollment_url
from .client_store import ClientStoreError


class SubscriptionArchive:
    def __init__(self, store, cipher):
        self.store, self.cipher = store, cipher
        with store._lock, store._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS subscription_addresses(
                customer_id TEXT PRIMARY KEY REFERENCES customers(id),
                digest TEXT NOT NULL REFERENCES enrollment_tokens(digest),
                encrypted_url BLOB NOT NULL, created_at INTEGER NOT NULL)''')

    @staticmethod
    def base_url(value):
        base, _ = parse_enrollment_url(str(value).rstrip('/') + '/#enroll=validation-token-1234')
        return base

    def save(self, customer_id, token, base, now):
        encrypted = self.cipher.encrypt((base + '/#enroll=' + token).encode())
        with self.store._lock, self.store._connect() as db:
            db.execute('''INSERT INTO subscription_addresses VALUES(?,?,?,?)
                ON CONFLICT(customer_id) DO UPDATE SET digest=excluded.digest,
                encrypted_url=excluded.encrypted_url, created_at=excluded.created_at''',
                (customer_id, secret_digest(token, 'enrollment'), encrypted, now))

    def generate(self, payload, *, now=None):
        """Issue one subscription through the store and archive its address."""
        now = int(time.time()) if now is None else now
        base = self.base_url(payload.get('base_url', ''))
        ids = payload.get('source_ids')
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
            raise ClientStoreError('源网选择无效')
        modes = payload.get('egress_modes')
        if modes is not None and (not isinstance(modes, list)
                                  or any(not isinstance(item, str) for item in modes)):
            raise ClientStoreError('出口选择无效')
        result = self.store.generate_monthly_subscription(
            str(payload.get('name', '')), payload.get('quota_gb'), ids,
            access_mode=str(payload.get('access_mode') or ('wireguard' if ids else 'public_proxy')),
            group_id=payload.get('group_id') or None,
            download_bps=payload.get('download_bps', 50000000),
            upload_bps=payload.get('upload_bps', 10000000),
            validity_days=payload.get('validity_days', 30),
            egress_modes=modes, now=now)
        token = result.pop('token')
        self.save(result['customer_id'], token, base, now)
        result['url'] = base + '/#enroll=' + token
        return result

    def generate_batch(self, payload, *, now=None):
        """One transaction per customer; partial success is explicit."""
        now = int(time.time()) if now is None else now
        names = payload.get('names')
        if (not isinstance(names, list) or not 1 <= len(names) <= 100
                or any(not isinstance(item, str) or not 1 <= len(item.strip()) <= 120 for item in names)):
            raise ClientStoreError('批量签发需要 1–100 个非空客户名称（每个不超过 120 字符）')
        cleaned = [item.strip() for item in names]
        if len(set(cleaned)) != len(cleaned):
            raise ClientStoreError('批量签发列表包含重复客户名称')
        common = {key: value for key, value in payload.items() if key != 'names'}
        results, succeeded, failed = [], 0, 0
        for name in cleaned:
            try:
                row = self.generate({**common, 'name': name}, now=now)
                results.append({'name': name, 'ok': True, 'customer_id': row['customer_id'],
                                'url': row['url'], 'expires_at': row['expires_at'],
                                'enrollment_expires_at': row['enrollment_expires_at']})
                succeeded += 1
            except ClientStoreError as exc:
                results.append({'name': name, 'ok': False, 'error': str(exc)})
                failed += 1
        return {'results': results, 'succeeded': succeeded, 'failed': failed}

    def statuses(self, *, now=None):
        now = int(time.time()) if now is None else now
        statuses = {}
        with self.store._connect() as db:
            rows = db.execute('''SELECT a.customer_id, a.digest, a.created_at,
                    e.used_at, e.revoked_at, e.expires_at, c.enabled customer_enabled,
                    c.plan_id, p.enabled plan_enabled, p.max_devices,
                    (SELECT COUNT(*) FROM devices d WHERE d.customer_id=a.customer_id AND d.enabled=1) device_count
                 FROM subscription_addresses a
                 JOIN enrollment_tokens e ON e.digest=a.digest
                 JOIN customers c ON c.id=a.customer_id
                 JOIN plans p ON p.id=c.plan_id''').fetchall()
            for row in rows:
                if row['used_at'] is not None:
                    status = 'used'
                elif row['revoked_at'] is not None or row['expires_at'] <= now:
                    status = 'expired'
                else:
                    status = 'ready'
                active_grant = bool(db.execute(
                    'SELECT 1 FROM line_grants WHERE customer_id=? AND enabled=1 '
                    'AND (expires_at IS NULL OR expires_at>=?) LIMIT 1',
                    (row['customer_id'], now)).fetchone())
                statuses[row['customer_id']] = {
                    'available': True, 'status': status,
                    'can_reissue': bool(row['customer_enabled'] and row['plan_enabled'] and active_grant
                                        and row['device_count'] < row['max_devices'])}
        return statuses

    def view(self, customer_id):
        self.store.customer(customer_id)
        with self.store._connect() as db:
            row = db.execute('SELECT encrypted_url FROM subscription_addresses WHERE customer_id=?',
                             (customer_id,)).fetchone()
        if row is None:
            raise ClientStoreError('旧地址未留存，请为此客户补发订阅地址')
        try:
            return self.cipher.decrypt(row['encrypted_url']).decode()
        except (InvalidToken, UnicodeError):
            raise ClientStoreError('订阅地址解密失败，请核对服务端密钥备份；不要重复开户。') from None

    def reissue(self, customer_id, base_url, *, now=None):
        """Rotate the address: a fresh 24 h token; previous unused tokens revoked."""
        now = int(time.time()) if now is None else now
        base = self.base_url(base_url)
        customer = self.store.customer(customer_id)
        if not customer['enabled']:
            raise ClientStoreError('客户已停用，不能补发')
        with self.store._connect() as db:
            plan = db.execute('SELECT * FROM plans WHERE id=? AND enabled=1',
                              (customer['plan_id'],)).fetchone()
            count = db.execute('SELECT COUNT(*) n FROM devices WHERE customer_id=? AND enabled=1',
                               (customer_id,)).fetchone()['n']
            if not plan or count >= plan['max_devices']:
                raise ClientStoreError('客户已有绑定设备且达到套餐上限，请使用已绑定客户端；补发不能替换现有设备。')
            active = db.execute('SELECT 1 FROM line_grants WHERE customer_id=? AND enabled=1 '
                                'AND (expires_at IS NULL OR expires_at>=?) LIMIT 1',
                                (customer_id, now)).fetchone()
            if not active:
                raise ClientStoreError('客户当前没有有效线路，不能补发')
        # create_enrollment_token rotates (revokes) prior unused tokens itself.
        token = self.store.create_enrollment_token(customer_id, ttl=86400, now=now)
        self.save(customer_id, token, base, now)
        return {'ok': True, 'customer_id': customer_id,
                'url': base + '/#enroll=' + token, 'enrollment_expires_at': now + 86400}

    def expire(self, customer_id, *, now=None):
        """Destroy an unused archived address; enrollment history is preserved."""
        now = int(time.time()) if now is None else now
        with self.store._lock, self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT digest FROM subscription_addresses WHERE customer_id=?',
                             (customer_id,)).fetchone()
            if row is None:
                raise ClientStoreError('原开户地址未留存，无需销毁')
            token = db.execute('SELECT * FROM enrollment_tokens WHERE digest=?', (row['digest'],)).fetchone()
            if token is None or token['used_at'] is not None:
                raise ClientStoreError('地址已兑换，不能销毁；如需收回请停用客户或补发新地址')
            db.execute('DELETE FROM subscription_addresses WHERE customer_id=?', (customer_id,))
            db.execute('UPDATE enrollment_tokens SET expires_at=?, revoked_at=? WHERE digest=? AND used_at IS NULL',
                       (now - 1, now, row['digest']))
        return {'ok': True, 'customer_id': customer_id, 'expired': True}

    def restore(self, customer_id, *, now=None):
        """Extend an unused archived address by 24 h (same URL, no rotation)."""
        now = int(time.time()) if now is None else now
        with self.store._lock, self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT digest FROM subscription_addresses WHERE customer_id=?',
                             (customer_id,)).fetchone()
            if row is None:
                raise ClientStoreError('原开户地址未留存，无法延长')
            updated = db.execute('UPDATE enrollment_tokens SET expires_at=?, revoked_at=NULL '
                                 'WHERE digest=? AND used_at IS NULL', (now + 86400, row['digest'])).rowcount
            if not updated:
                raise ClientStoreError('地址已兑换，不能延长')
        return {'ok': True, 'customer_id': customer_id, 'enrollment_expires_at': now + 86400}
