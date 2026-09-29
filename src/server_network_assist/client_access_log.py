"""Admin-visible access log for device-authenticated client API requests.

Records which device (and therefore customer) reached which client endpoint
from which source IP at what time. Recording is best-effort: a logging
failure must never break the client API. The recorded address is the direct
peer address (``request.remote``); behind a reverse proxy this is the proxy
address, so ``X-Forwarded-For`` is deliberately never trusted.
"""
import os
import time

from .client_store import ClientStoreError

MAX_ROWS_DEFAULT = 100000


class ClientAccessLog:
    def __init__(self, store, max_rows: int | None = None):
        self.store = store
        try:
            value = os.environ.get('SNA_ACCESS_LOG_MAX_ROWS', '').strip()
            configured = int(value) if value else MAX_ROWS_DEFAULT
        except ValueError:
            configured = MAX_ROWS_DEFAULT
        if configured < 1000 or configured > 10_000_000:
            configured = MAX_ROWS_DEFAULT
        self.max_rows = max_rows if max_rows is not None else configured
        with store._lock, store._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS device_access(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT REFERENCES devices(id),
                customer_id TEXT NOT NULL REFERENCES customers(id),
                remote_ip TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,
                user_agent TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS device_access_customer_time '
                       'ON device_access(customer_id, created_at)')
            db.execute('CREATE INDEX IF NOT EXISTS device_access_device_time '
                       'ON device_access(device_id, created_at)')

    def record(self, device_id, customer_id, remote_ip, method, path,
               user_agent='', now=None):
        """Best-effort insert; never raises into the client API."""
        try:
            now = int(time.time()) if now is None else now
            with self.store._lock, self.store._connect() as db:
                db.execute('INSERT INTO device_access VALUES(NULL,?,?,?,?,?,?,?)',
                           (None if device_id in (None, '') else str(device_id),
                            str(customer_id), str(remote_ip)[:64],
                            str(method)[:8], str(path)[:256], str(user_agent)[:256], now))
            if now % 100 == 0:  # occasional opportunistic prune, never blocking
                self._prune(now)
        except Exception:
            pass

    def _prune(self, now):
        try:
            with self.store._lock, self.store._connect() as db:
                overflow = db.execute('SELECT COUNT(*) n FROM device_access').fetchone()['n'] - self.max_rows
                if overflow > 0:
                    keep = db.execute('SELECT id FROM device_access ORDER BY id DESC LIMIT 1 OFFSET ?',
                                      (self.max_rows,)).fetchone()
                    if keep is not None:
                        db.execute('DELETE FROM device_access WHERE id <= ?', (keep['id'],))
        except Exception:
            pass

    def query(self, *, customer_id=None, device_id=None, from_ts=None, to_ts=None,
              limit=100, offset=0):
        try:
            limit = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            raise ClientStoreError('分页参数无效') from None
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            raise ClientStoreError('分页参数无效') from None
        for name, value in (('from', from_ts), ('to', to_ts)):
            if value is not None:
                try:
                    int(value)
                except (TypeError, ValueError):
                    raise ClientStoreError('时间参数无效') from None
        conditions, args = [], []
        if customer_id:
            conditions.append('a.customer_id=?')
            args.append(str(customer_id))
        if device_id:
            conditions.append('a.device_id=?')
            args.append(str(device_id))
        if from_ts is not None:
            conditions.append('a.created_at>=?')
            args.append(int(from_ts))
        if to_ts is not None:
            conditions.append('a.created_at<=?')
            args.append(int(to_ts))
        where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
        with self.store._connect() as db:
            total = db.execute('SELECT COUNT(*) n FROM device_access a' + where, args).fetchone()['n']
            rows = db.execute(
                'SELECT a.*, c.display_name customer_name FROM device_access a '
                'JOIN customers c ON c.id=a.customer_id' + where +
                ' ORDER BY a.id DESC LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
        return {'rows': [dict(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}

    def latest_per_device(self):
        with self.store._connect() as db:
            rows = db.execute('SELECT device_id, MAX(created_at) latest FROM device_access GROUP BY device_id').fetchall()
        return {row['device_id']: row['latest'] for row in rows}
