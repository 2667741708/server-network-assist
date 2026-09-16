"""SQLite state for customer enrollment, leases, revocation and accounting."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .client_crypto import new_secret, secret_digest


class ClientStoreError(ValueError):
    """A safe, user-facing control-plane validation error."""


class _Connection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


class ClientStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, factory=_Connection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS plans(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, download_bps INTEGER, upload_bps INTEGER,
              quota_bytes INTEGER, period_seconds INTEGER NOT NULL, max_devices INTEGER NOT NULL,
              lease_seconds INTEGER NOT NULL, enabled INTEGER NOT NULL, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS customers(
              id TEXT PRIMARY KEY, display_name TEXT NOT NULL, plan_id TEXT NOT NULL REFERENCES plans(id),
              enabled INTEGER NOT NULL, revoked_at INTEGER, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS devices(
              id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), label TEXT NOT NULL,
              public_key TEXT NOT NULL UNIQUE, wireguard_public_key TEXT NOT NULL UNIQUE,
              enabled INTEGER NOT NULL, revoked_at INTEGER,
              created_at INTEGER NOT NULL, last_seen_at INTEGER);
            CREATE TABLE IF NOT EXISTS enrollment_tokens(
              digest TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id),
              expires_at INTEGER NOT NULL, used_at INTEGER, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS line_grants(
              id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), alias TEXT NOT NULL,
              tunnel TEXT NOT NULL, endpoint TEXT NOT NULL, enabled INTEGER NOT NULL,
              relay_public_key TEXT NOT NULL, allocated_address TEXT NOT NULL, dns TEXT NOT NULL,
              allowed_ips TEXT NOT NULL, mtu INTEGER NOT NULL, relay_interface TEXT NOT NULL,
              egress_interface TEXT NOT NULL, expires_at INTEGER, created_at INTEGER NOT NULL,
              UNIQUE(customer_id, alias));
            CREATE TABLE IF NOT EXISTS leases(
              id TEXT PRIMARY KEY, token_digest TEXT NOT NULL UNIQUE, customer_id TEXT NOT NULL REFERENCES customers(id),
              device_id TEXT NOT NULL REFERENCES devices(id), grant_id TEXT NOT NULL REFERENCES line_grants(id),
              issued_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER,
              last_rx INTEGER NOT NULL DEFAULT 0, last_tx INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS used_nonces(
              scope TEXT NOT NULL, nonce_digest TEXT NOT NULL, expires_at INTEGER NOT NULL,
              PRIMARY KEY(scope, nonce_digest));
            CREATE TABLE IF NOT EXISTS usage_periods(
              customer_id TEXT NOT NULL REFERENCES customers(id), period_start INTEGER NOT NULL,
              period_end INTEGER NOT NULL, rx_bytes INTEGER NOT NULL DEFAULT 0, tx_bytes INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(customer_id, period_start));
            CREATE TABLE IF NOT EXISTS usage_reports(
              lease_id TEXT NOT NULL REFERENCES leases(id), report_id TEXT NOT NULL,
              rx_total INTEGER NOT NULL, tx_total INTEGER NOT NULL, created_at INTEGER NOT NULL,
              PRIMARY KEY(lease_id, report_id));
            """)

    @staticmethod
    def _id(prefix: str) -> str:
        return prefix + "_" + uuid.uuid4().hex

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def create_plan(self, name: str, *, plan_id: str | None = None, download_bps: int | None = None,
                    upload_bps: int | None = None, quota_bytes: int | None = None,
                    period_seconds: int = 30 * 86400, max_devices: int = 1,
                    lease_seconds: int = 900, now: int | None = None) -> dict[str, Any]:
        values = (download_bps, upload_bps, quota_bytes)
        if not name.strip() or any(v is not None and v < 0 for v in values) or period_seconds < 60 or max_devices < 1 or not 60 <= lease_seconds <= 86400:
            raise ClientStoreError("套餐参数无效")
        now = int(time.time()) if now is None else now
        plan_id = plan_id or self._id("plan")
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (plan_id, name.strip(), download_bps, upload_bps, quota_bytes,
                        period_seconds, max_devices, lease_seconds, 1, now))
        return self.get_plan(plan_id)

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise ClientStoreError("套餐不存在")
        return dict(row)

    def list_plans(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM plans ORDER BY created_at DESC").fetchall()]

    def create_customer(self, display_name: str, plan_id: str, *, customer_id: str | None = None,
                        now: int | None = None) -> dict[str, Any]:
        if not display_name.strip():
            raise ClientStoreError("客户名称无效")
        now = int(time.time()) if now is None else now
        customer_id = customer_id or self._id("cus")
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO customers VALUES(?,?,?,?,?,?)",
                       (customer_id, display_name.strip(), plan_id, 1, None, now))
        return self.customer(customer_id)

    def customer(self, customer_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not row:
            raise ClientStoreError("客户不存在")
        return dict(row)

    def list_customers(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM customers ORDER BY created_at DESC").fetchall()]

    def list_devices(self, customer_id: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM devices", ()
        if customer_id is not None:
            sql, args = sql + " WHERE customer_id=?", (customer_id,)
        with self._connect() as db:
            return [dict(row) for row in db.execute(sql + " ORDER BY created_at DESC", args).fetchall()]

    def create_enrollment_token(self, customer_id: str, *, ttl: int = 900,
                                now: int | None = None) -> str:
        if not 60 <= ttl <= 86400:
            raise ClientStoreError("开户令牌有效期无效")
        now = int(time.time()) if now is None else now
        token = new_secret("enr_")
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO enrollment_tokens VALUES(?,?,?,?,?)",
                       (secret_digest(token, "enrollment"), customer_id, now + ttl, None, now))
        return token

    def enroll_device(self, token: str, public_key: str, label: str, *, wireguard_public_key: str | None = None,
                      device_id: str | None = None,
                      now: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        digest = secret_digest(token, "enrollment")
        device_id = device_id or self._id("dev")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT e.*, c.enabled customer_enabled, c.plan_id FROM enrollment_tokens e JOIN customers c ON c.id=e.customer_id WHERE digest=?", (digest,)).fetchone()
            if not row or row["used_at"] is not None or row["expires_at"] < now or not row["customer_enabled"]:
                raise ClientStoreError("开户令牌无效、已使用或已过期")
            plan = db.execute("SELECT * FROM plans WHERE id=? AND enabled=1", (row["plan_id"],)).fetchone()
            count = db.execute("SELECT COUNT(*) n FROM devices WHERE customer_id=? AND enabled=1", (row["customer_id"],)).fetchone()["n"]
            if not plan or count >= plan["max_devices"]:
                raise ClientStoreError("设备数量已达到套餐上限")
            db.execute("INSERT INTO devices VALUES(?,?,?,?,?,?,?,?,?)",
                       (device_id, row["customer_id"], label.strip() or "客户设备", public_key,
                        wireguard_public_key or public_key, 1, None, now, now))
            db.execute("UPDATE enrollment_tokens SET used_at=? WHERE digest=?", (now, digest))
        return self.device(device_id)

    def device(self, device_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        if not row:
            raise ClientStoreError("设备不存在")
        return dict(row)

    def grant_line(self, customer_id: str, alias: str, tunnel: str, endpoint: str, *,
                   relay_public_key: str = "", allocated_address: str = "",
                   dns: str = "1.1.1.1", allowed_ips: str = "0.0.0.0/0,::/0",
                   mtu: int = 1420, relay_interface: str = "wg0",
                   egress_interface: str = "",
                   grant_id: str | None = None, expires_at: int | None = None,
                   now: int | None = None) -> dict[str, Any]:
        if not alias.strip() or not tunnel.strip() or not endpoint.strip():
            raise ClientStoreError("线路授权参数无效")
        now = int(time.time()) if now is None else now
        grant_id = grant_id or self._id("grant")
        if not 576 <= mtu <= 9000:
            raise ClientStoreError("线路 MTU 无效")
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO line_grants VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (grant_id, customer_id, alias.strip(), tunnel.strip(), endpoint.strip(), 1,
                        relay_public_key, allocated_address, dns, allowed_ips, mtu,
                        relay_interface, egress_interface, expires_at, now))
        return self.line_grant(grant_id)

    def line_grant(self, grant_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM line_grants WHERE id=?", (grant_id,)).fetchone()
        if not row:
            raise ClientStoreError("线路授权不存在")
        return dict(row)

    def list_grants(self, customer_id: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM line_grants", ()
        if customer_id is not None:
            sql, args = sql + " WHERE customer_id=?", (customer_id,)
        with self._connect() as db:
            return [dict(row) for row in db.execute(sql + " ORDER BY created_at DESC", args).fetchall()]

    def list_leases(self, *, customer_id: str | None = None,
                    device_id: str | None = None) -> list[dict[str, Any]]:
        clauses, args = [], []
        if customer_id is not None:
            clauses.append("customer_id=?")
            args.append(customer_id)
        if device_id is not None:
            clauses.append("device_id=?")
            args.append(device_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            rows = db.execute("SELECT id,customer_id,device_id,grant_id,issued_at,expires_at,revoked_at,last_rx,last_tx FROM leases" + where + " ORDER BY issued_at DESC", args).fetchall()
        return [dict(row) for row in rows]

    def lease_relay_id(self, lease_id: str) -> str:
        with self._connect() as db:
            row = db.execute("SELECT g.relay_interface FROM leases l JOIN line_grants g ON g.id=l.grant_id WHERE l.id=?",
                             (lease_id,)).fetchone()
        if not row:
            raise ClientStoreError("租约不存在")
        return row["relay_interface"]

    def set_customer_enabled(self, customer_id: str, enabled: bool, *, now: int | None = None) -> None:
        self._set_enabled("customer", customer_id, enabled, now=now)

    def set_device_enabled(self, device_id: str, enabled: bool, *, now: int | None = None) -> None:
        self._set_enabled("device", device_id, enabled, now=now)

    def set_grant_enabled(self, grant_id: str, enabled: bool, *, now: int | None = None) -> None:
        self._set_enabled("grant", grant_id, enabled, now=now)

    def _set_enabled(self, kind: str, object_id: str, enabled: bool, *, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        table, lease_column = {"customer": ("customers", "customer_id"),
                               "device": ("devices", "device_id"),
                               "grant": ("line_grants", "grant_id")}[kind]
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if kind == "grant":
                cursor = db.execute("UPDATE line_grants SET enabled=? WHERE id=?", (int(enabled), object_id))
            else:
                cursor = db.execute(f"UPDATE {table} SET enabled=?,revoked_at=? WHERE id=?",
                                    (int(enabled), None if enabled else now, object_id))
            if cursor.rowcount == 0:
                raise ClientStoreError("对象不存在")
            if not enabled:
                db.execute(f"UPDATE leases SET revoked_at=? WHERE {lease_column}=? AND revoked_at IS NULL",
                           (now, object_id))

    def consume_nonce(self, scope: str, nonce: str, *, ttl: int = 300, now: int | None = None) -> None:
        if not scope or len(nonce) < 16 or not 1 <= ttl <= 3600:
            raise ClientStoreError("请求随机标识无效")
        now = int(time.time()) if now is None else now
        digest = secret_digest(nonce, "nonce")
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM used_nonces WHERE expires_at<?", (now,))
            try:
                db.execute("INSERT INTO used_nonces VALUES(?,?,?)", (scope, digest, now + ttl))
            except sqlite3.IntegrityError:
                raise ClientStoreError("请求已处理，请勿重放") from None

    def issue_lease(self, device_id: str, grant_id: str, *, ttl: int | None = None,
                    now: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        lease_id, token = self._id("lease"), new_secret("lea_")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT d.customer_id,d.enabled device_enabled,c.enabled customer_enabled,
              c.plan_id,g.enabled grant_enabled,g.expires_at grant_expires,g.customer_id grant_customer,
              p.enabled plan_enabled,p.lease_seconds,p.quota_bytes,p.period_seconds FROM devices d JOIN customers c ON c.id=d.customer_id
              JOIN plans p ON p.id=c.plan_id JOIN line_grants g ON g.id=? WHERE d.id=?""", (grant_id, device_id)).fetchone()
            if not row or not all(row[k] for k in ("device_enabled", "customer_enabled", "grant_enabled", "plan_enabled")) or row["customer_id"] != row["grant_customer"]:
                raise ClientStoreError("设备或线路未获授权")
            usage = self._usage_in_db(db, row["customer_id"], row["period_seconds"], now)
            if row["quota_bytes"] is not None and usage["used_bytes"] >= row["quota_bytes"]:
                raise ClientStoreError("本账期流量额度已经用完")
            duration = row["lease_seconds"] if ttl is None else min(ttl, row["lease_seconds"])
            expires = now + duration
            if row["grant_expires"] is not None:
                expires = min(expires, row["grant_expires"])
            if expires <= now:
                raise ClientStoreError("线路授权已经过期")
            # One active peer per device and grant avoids duplicate WireGuard
            # address/key policies during retries and lease rotation.
            db.execute("UPDATE leases SET revoked_at=? WHERE device_id=? AND grant_id=? AND revoked_at IS NULL",
                       (now, device_id, grant_id))
            db.execute("INSERT INTO leases(id,token_digest,customer_id,device_id,grant_id,issued_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                       (lease_id, secret_digest(token, "lease"), row["customer_id"], device_id, grant_id, now, expires))
        result = self.validate_lease(token, now=now)
        result["token"] = token
        return result

    def renew_lease(self, device_id: str, lease_id: str, *, now: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        with self._connect() as db:
            row = db.execute("SELECT device_id,grant_id FROM leases WHERE id=? AND revoked_at IS NULL", (lease_id,)).fetchone()
        if not row or row["device_id"] != device_id:
            raise ClientStoreError("租约不存在或不属于当前设备")
        replacement = self.issue_lease(device_id, row["grant_id"], now=now)
        self.revoke("lease", lease_id, now=now)
        return replacement

    def release_lease(self, device_id: str, lease_id: str, *, now: int | None = None) -> None:
        with self._connect() as db:
            row = db.execute("SELECT device_id,revoked_at FROM leases WHERE id=?", (lease_id,)).fetchone()
        if not row or row["device_id"] != device_id:
            raise ClientStoreError("租约不存在或不属于当前设备")
        if row["revoked_at"] is None:
            self.revoke("lease", lease_id, now=now)

    def validate_lease(self, token: str, *, now: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        with self._connect() as db:
            row = db.execute("""SELECT l.*,g.alias,g.tunnel,g.endpoint,g.enabled grant_enabled,g.expires_at grant_expires,
              d.enabled device_enabled,d.public_key device_signing_public_key,
              d.wireguard_public_key,c.enabled customer_enabled,p.download_bps,p.upload_bps,p.quota_bytes,p.period_seconds,
              g.relay_public_key,g.allocated_address,g.dns,g.allowed_ips,g.mtu,g.relay_interface,g.egress_interface
              FROM leases l JOIN line_grants g ON g.id=l.grant_id JOIN devices d ON d.id=l.device_id
              JOIN customers c ON c.id=l.customer_id JOIN plans p ON p.id=c.plan_id WHERE l.token_digest=?""",
              (secret_digest(token, "lease"),)).fetchone()
            if not row or row["revoked_at"] is not None or row["expires_at"] <= now or not row["grant_enabled"] or not row["device_enabled"] or not row["customer_enabled"] or (row["grant_expires"] is not None and row["grant_expires"] <= now):
                raise ClientStoreError("租约无效、已撤销或已过期")
            used = self._usage_in_db(db, row["customer_id"], row["period_seconds"], now)
            if row["quota_bytes"] is not None and used["used_bytes"] >= row["quota_bytes"]:
                raise ClientStoreError("本账期流量额度已经用完")
        value = dict(row)
        value.update(used)
        value.pop("token_digest", None)
        return value

    @staticmethod
    def _usage_in_db(db: sqlite3.Connection, customer_id: str, period_seconds: int, now: int) -> dict[str, int]:
        start = now - now % period_seconds
        row = db.execute("SELECT rx_bytes,tx_bytes FROM usage_periods WHERE customer_id=? AND period_start=?", (customer_id, start)).fetchone()
        rx, tx = (row["rx_bytes"], row["tx_bytes"]) if row else (0, 0)
        return {"period_start": start, "period_end": start + period_seconds, "rx_bytes": rx, "tx_bytes": tx, "used_bytes": rx + tx}

    def record_usage(self, token: str, report_id: str, rx_total: int, tx_total: int, *, now: int | None = None) -> dict[str, Any]:
        if not report_id or rx_total < 0 or tx_total < 0:
            raise ClientStoreError("流量报告无效")
        now = int(time.time()) if now is None else now
        lease = self.validate_lease(token, now=now)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT 1 FROM usage_reports WHERE lease_id=? AND report_id=?", (lease["id"], report_id)).fetchone()
            if existing:
                return self._usage_in_db(db, lease["customer_id"], lease["period_seconds"], now)
            current = db.execute("SELECT last_rx,last_tx FROM leases WHERE id=?", (lease["id"],)).fetchone()
            if rx_total < current["last_rx"] or tx_total < current["last_tx"]:
                raise ClientStoreError("流量计数器不能回退")
            delta_rx, delta_tx = rx_total - current["last_rx"], tx_total - current["last_tx"]
            usage = self._usage_in_db(db, lease["customer_id"], lease["period_seconds"], now)
            db.execute("INSERT INTO usage_periods VALUES(?,?,?,?,?) ON CONFLICT(customer_id,period_start) DO UPDATE SET rx_bytes=rx_bytes+excluded.rx_bytes,tx_bytes=tx_bytes+excluded.tx_bytes",
                       (lease["customer_id"], usage["period_start"], usage["period_end"], delta_rx, delta_tx))
            db.execute("UPDATE leases SET last_rx=?,last_tx=? WHERE id=?", (rx_total, tx_total, lease["id"]))
            db.execute("INSERT INTO usage_reports VALUES(?,?,?,?,?)", (lease["id"], report_id, rx_total, tx_total, now))
            result = self._usage_in_db(db, lease["customer_id"], lease["period_seconds"], now)
            if lease["quota_bytes"] is not None and result["used_bytes"] >= lease["quota_bytes"]:
                db.execute("UPDATE leases SET revoked_at=? WHERE customer_id=? AND revoked_at IS NULL", (now, lease["customer_id"]))
            return result

    def record_usage_by_lease(self, lease_id: str, report_id: str, rx_total: int, tx_total: int, *,
                              now: int | None = None) -> dict[str, Any]:
        """Record trusted relay counters, including the final sample after revocation."""
        if not report_id or rx_total < 0 or tx_total < 0:
            raise ClientStoreError("流量报告无效")
        now = int(time.time()) if now is None else now
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            lease = db.execute("""SELECT l.*,p.period_seconds,p.quota_bytes FROM leases l
                JOIN customers c ON c.id=l.customer_id JOIN plans p ON p.id=c.plan_id WHERE l.id=?""",
                               (lease_id,)).fetchone()
            if not lease:
                raise ClientStoreError("租约不存在")
            existing = db.execute("SELECT 1 FROM usage_reports WHERE lease_id=? AND report_id=?",
                                  (lease_id, report_id)).fetchone()
            if existing:
                return self._usage_in_db(db, lease["customer_id"], lease["period_seconds"], now)
            if rx_total < lease["last_rx"] or tx_total < lease["last_tx"]:
                raise ClientStoreError("流量计数器不能回退")
            delta_rx, delta_tx = rx_total - lease["last_rx"], tx_total - lease["last_tx"]
            usage = self._usage_in_db(db, lease["customer_id"], lease["period_seconds"], now)
            db.execute("INSERT INTO usage_periods VALUES(?,?,?,?,?) ON CONFLICT(customer_id,period_start) DO UPDATE SET rx_bytes=rx_bytes+excluded.rx_bytes,tx_bytes=tx_bytes+excluded.tx_bytes",
                       (lease["customer_id"], usage["period_start"], usage["period_end"], delta_rx, delta_tx))
            db.execute("UPDATE leases SET last_rx=?,last_tx=? WHERE id=?", (rx_total, tx_total, lease_id))
            db.execute("INSERT INTO usage_reports VALUES(?,?,?,?,?)", (lease_id, report_id, rx_total, tx_total, now))
            result = self._usage_in_db(db, lease["customer_id"], lease["period_seconds"], now)
            if lease["quota_bytes"] is not None and result["used_bytes"] >= lease["quota_bytes"]:
                db.execute("UPDATE leases SET revoked_at=? WHERE customer_id=? AND revoked_at IS NULL",
                           (now, lease["customer_id"]))
            return result

    def revoke(self, kind: str, object_id: str, *, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        mapping = {"customer": ("customers", "id"), "device": ("devices", "id"),
                   "grant": ("line_grants", "id"), "lease": ("leases", "id")}
        if kind not in mapping:
            raise ClientStoreError("撤销对象类型无效")
        table, column = mapping[kind]
        with self._lock, self._connect() as db:
            if kind == "lease":
                cursor = db.execute(f"UPDATE {table} SET revoked_at=? WHERE {column}=?", (now, object_id))
            else:
                cursor = db.execute(f"UPDATE {table} SET enabled=0,revoked_at=? WHERE {column}=?", (now, object_id)) if kind != "grant" else db.execute("UPDATE line_grants SET enabled=0 WHERE id=?", (object_id,))
                if kind in {"customer", "device"}:
                    db.execute(f"UPDATE leases SET revoked_at=? WHERE {kind}_id=? AND revoked_at IS NULL", (now, object_id))
                elif kind == "grant":
                    db.execute("UPDATE leases SET revoked_at=? WHERE grant_id=? AND revoked_at IS NULL", (now, object_id))
            if cursor.rowcount == 0:
                raise ClientStoreError("撤销对象不存在")

    def usage(self, customer_id: str, *, now: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        with self._connect() as db:
            plan = db.execute("SELECT p.* FROM plans p JOIN customers c ON c.plan_id=p.id WHERE c.id=?", (customer_id,)).fetchone()
            if not plan:
                raise ClientStoreError("客户不存在")
            result = self._usage_in_db(db, customer_id, plan["period_seconds"], now)
        result["quota_bytes"] = plan["quota_bytes"]
        result["remaining_bytes"] = None if plan["quota_bytes"] is None else max(0, plan["quota_bytes"] - result["used_bytes"])
        return result

    def active_leases(self, *, now: int | None = None) -> list[dict[str, Any]]:
        """Return relay-safe desired state; bearer token digests are never exposed."""
        now = int(time.time()) if now is None else now
        with self._connect() as db:
            rows = db.execute("""SELECT l.id,l.customer_id,l.device_id,l.grant_id,l.issued_at,l.expires_at,l.last_rx,l.last_tx,
              d.wireguard_public_key,g.alias,g.endpoint,g.relay_public_key,g.allocated_address,g.dns,
              g.allowed_ips,g.mtu,g.relay_interface,g.egress_interface,p.download_bps,p.upload_bps,
              p.quota_bytes,p.period_seconds
              FROM leases l JOIN devices d ON d.id=l.device_id JOIN customers c ON c.id=l.customer_id
              JOIN plans p ON p.id=c.plan_id JOIN line_grants g ON g.id=l.grant_id
              WHERE l.revoked_at IS NULL AND l.expires_at>? AND d.enabled=1 AND c.enabled=1
              AND g.enabled=1 AND (g.expires_at IS NULL OR g.expires_at>?) ORDER BY l.issued_at""",
              (now, now)).fetchall()
        return [dict(row) for row in rows]

    def lease_reconciliation(self, *, since: int = 0, now: int | None = None) -> dict[str, Any]:
        """Desired active peers plus leases explicitly revoked since a relay checkpoint."""
        now = int(time.time()) if now is None else now
        with self._connect() as db:
            revoked = db.execute("SELECT id,device_id,grant_id,revoked_at FROM leases WHERE revoked_at IS NOT NULL AND revoked_at>=? ORDER BY revoked_at", (since,)).fetchall()
        return {"generated_at": now, "active": self.active_leases(now=now),
                "revoked": [dict(row) for row in revoked]}
