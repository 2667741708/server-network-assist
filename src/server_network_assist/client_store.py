"""SQLite state for customer enrollment, leases, revocation and accounting."""
from __future__ import annotations

import json
import ipaddress
import base64
import os
import re
import hashlib
import hmac
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .client_crypto import new_secret, secret_digest


CLIENT_STORE_SCHEMA_VERSION = 6
MAX_ACTIVE_V2_PROBES_PER_DEVICE = 3
SCHEMA_INCOMPATIBLE_MESSAGE = "数据库 schema 版本不兼容，请先停止旧 worker 并完成迁移"
# SQLite requires an integer for lease expiry. This sentinel represents an
# unbounded lease; explicit revocation and the relay offline deadline still apply.
LEASE_NEVER_EXPIRES_AT = 2**63 - 1
# Enrollment tokens use the same never-expire representation; they remain
# one-use and can still be revoked.
ENROLLMENT_NEVER_EXPIRES_AT = LEASE_NEVER_EXPIRES_AT

PUBLIC_PROXY_CONFIG = {
    "type": "vmess",
    "server": "140.143.202.144",
    "port": 443,
    "cipher": "auto",
    "alter_id": 0,
    "network": "ws",
    "ws_path": "/sna-proxy/v2",
    "tls": True,
    "servername": "whm12.art",
}


def _cloud_path_token(token_digest: str, lease_id: str) -> str:
    """Derive a distinct Cloud gateway bearer from each high-entropy lease secret."""
    digest = hmac.new(bytes.fromhex(token_digest),
                      b"sna-cloud-proxy-v2\0" + lease_id.encode("ascii"),
                      hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

from .egress_contract import canonical_egress

TRANSPORT_POLICIES = frozenset({"campus_physical", "physical_source_reachable", "public"})


def _normalise_egress_policy(value: object, access_mode: str) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        value = "source_proxy" if access_mode == "public_proxy" else "source_physical"
    try:
        policy = canonical_egress(value)
    except ValueError:
        raise ClientStoreError("公网出口策略无效或不一致") from None
    if access_mode == "public_proxy" and policy != "source_proxy":
        raise ClientStoreError("公网代理线路必须使用源机代理出口")
    return policy


def _normalise_transport_policy(value: object, access_mode: str) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        value = "public" if access_mode == "public_proxy" else "campus_physical"
    if not isinstance(value, str):
        raise ClientStoreError("源网传输策略无效")
    policy = value.strip().lower().replace("-", "_")
    if access_mode == "public_proxy":
        policy = "public"
    if policy not in TRANSPORT_POLICIES:
        raise ClientStoreError("源网传输策略无效")
    return policy


class ClientStoreError(ValueError):
    """A safe, user-facing control-plane validation error."""


class ProbeVersionUnsupported(ClientStoreError):
    """The opt-in v2 contract is unavailable before any lease is written."""


class ProbeCapacityBusy(ClientStoreError):
    """The relay's bounded probe allocation is currently occupied."""


class ClientAuthenticationError(ClientStoreError):
    """An authentication rejection whose reason is for internal audit only."""

    def __init__(self, reason: str):
        self.reason = str(reason)
        super().__init__("设备凭据无效")


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

    def _connect(self, *, timeout: float = 10) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=timeout, factory=_Connection)
        db.row_factory = sqlite3.Row
        # The trigger gate below deliberately requires every current worker to
        # identify its schema version.  An older worker has no such function,
        # so its writes fail instead of sharing an epoch-aware database.
        db.create_function("sna_schema_version", 0, lambda: CLIENT_STORE_SCHEMA_VERSION)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            # On Windows, a concurrent first opener can make this pragma
            # return SQLITE_BUSY without honoring sqlite3's busy timeout.
            # BEGIN IMMEDIATE below still provides the cross-process schema
            # lock; an existing journal mode is safe to keep for this opener.
            if "locked" not in str(exc).lower():
                raise
        return db

    def _initialize(self) -> None:
        # BEGIN IMMEDIATE is the cross-process schema lock.  Keep every DDL,
        # migration, version check and compatibility trigger in this one
        # transaction; executescript() is intentionally avoided because it
        # implicitly commits before running its script.
        with self._connect(timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS sna_schema_meta(
                id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL)""")
            version = db.execute("SELECT schema_version FROM sna_schema_meta WHERE id=1").fetchone()
            if version and int(version[0]) > CLIENT_STORE_SCHEMA_VERSION:
                raise ClientStoreError(SCHEMA_INCOMPATIBLE_MESSAGE)
            schema_statements = [
                """
            CREATE TABLE IF NOT EXISTS customer_groups(
              id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL,
              created_at INTEGER NOT NULL);
                """,
                """
            CREATE TABLE IF NOT EXISTS plans(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, download_bps INTEGER, upload_bps INTEGER,
              quota_bytes INTEGER, period_seconds INTEGER NOT NULL, max_devices INTEGER NOT NULL,
              lease_seconds INTEGER NOT NULL, enabled INTEGER NOT NULL, created_at INTEGER NOT NULL);
                """,
                """
            CREATE TABLE IF NOT EXISTS sources(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, configuration TEXT NOT NULL);
                """,
                """
            CREATE TABLE IF NOT EXISTS billing_anchors(
              customer_id TEXT PRIMARY KEY, anchor INTEGER NOT NULL);
                """,
                """
            CREATE TABLE IF NOT EXISTS customers(
              id TEXT PRIMARY KEY, display_name TEXT NOT NULL, plan_id TEXT NOT NULL REFERENCES plans(id),
              enabled INTEGER NOT NULL, revoked_at INTEGER, created_at INTEGER NOT NULL,
              auth_epoch INTEGER NOT NULL DEFAULT 0);
                """,
                """
            CREATE TABLE IF NOT EXISTS customer_service_terms(
              customer_id TEXT PRIMARY KEY REFERENCES customers(id),
              duration_seconds INTEGER NOT NULL, required_grants INTEGER NOT NULL,
              started_at INTEGER, expires_at INTEGER);
                """,
                """
            CREATE TABLE IF NOT EXISTS devices(
              id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), label TEXT NOT NULL,
              public_key TEXT NOT NULL UNIQUE, wireguard_public_key TEXT NOT NULL UNIQUE,
              enabled INTEGER NOT NULL, revoked_at INTEGER,
              created_at INTEGER NOT NULL, last_seen_at INTEGER,
              customer_epoch INTEGER NOT NULL DEFAULT 0);
                """,
                """
            CREATE TABLE IF NOT EXISTS enrollment_tokens(
              digest TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id),
              expires_at INTEGER NOT NULL, used_at INTEGER, created_at INTEGER NOT NULL,
              revoked_at INTEGER);
                """,
                """
            CREATE TABLE IF NOT EXISTS line_grants(
              id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), alias TEXT NOT NULL,
              tunnel TEXT NOT NULL, endpoint TEXT NOT NULL, enabled INTEGER NOT NULL,
              relay_public_key TEXT NOT NULL, allocated_address TEXT NOT NULL, dns TEXT NOT NULL,
              allowed_ips TEXT NOT NULL, mtu INTEGER NOT NULL, relay_interface TEXT NOT NULL,
              egress_interface TEXT NOT NULL, egress_policy TEXT NOT NULL DEFAULT 'source_physical',
              expires_at INTEGER, created_at INTEGER NOT NULL,
              UNIQUE(customer_id, alias));
                """,
                """
            CREATE TABLE IF NOT EXISTS leases(
              id TEXT PRIMARY KEY, token_digest TEXT NOT NULL UNIQUE, customer_id TEXT NOT NULL REFERENCES customers(id),
              device_id TEXT NOT NULL REFERENCES devices(id), grant_id TEXT NOT NULL REFERENCES line_grants(id),
              issued_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER,
              transport_id TEXT,
              last_rx INTEGER NOT NULL DEFAULT 0, last_tx INTEGER NOT NULL DEFAULT 0);
                """,
                """
            CREATE TABLE IF NOT EXISTS probe_leases(
              probe_digest TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id),
              lease_id TEXT NOT NULL UNIQUE REFERENCES leases(id), created_at INTEGER NOT NULL,
              probe_version INTEGER NOT NULL DEFAULT 1,
              wireguard_public_key TEXT, allocated_address TEXT, probe_address TEXT,
              echo_addresses TEXT);
                """,
                """
            CREATE TABLE IF NOT EXISTS relay_readiness(
              lease_id TEXT PRIMARY KEY REFERENCES leases(id),
              relay_interface TEXT NOT NULL, policy_digest TEXT NOT NULL,
              ready_at INTEGER);
                """,
                """
            CREATE TABLE IF NOT EXISTS used_nonces(
              scope TEXT NOT NULL, nonce_digest TEXT NOT NULL, expires_at INTEGER NOT NULL,
              PRIMARY KEY(scope, nonce_digest));
                """,
                """
            CREATE TABLE IF NOT EXISTS usage_periods(
              customer_id TEXT NOT NULL REFERENCES customers(id), period_start INTEGER NOT NULL,
              period_end INTEGER NOT NULL, rx_bytes INTEGER NOT NULL DEFAULT 0, tx_bytes INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(customer_id, period_start));
                """,
                """
            CREATE TABLE IF NOT EXISTS usage_reports(
              lease_id TEXT NOT NULL REFERENCES leases(id), report_id TEXT NOT NULL,
              rx_total INTEGER NOT NULL, tx_total INTEGER NOT NULL, created_at INTEGER NOT NULL,
              PRIMARY KEY(lease_id, report_id));
                """,
                """
            CREATE TABLE IF NOT EXISTS proxy_users(
              device_id TEXT PRIMARY KEY REFERENCES devices(id), uuid TEXT NOT NULL UNIQUE,
              created_at INTEGER NOT NULL);
                """,
            ]
            for statement in schema_statements:
                db.execute(statement)
            columns = {row[1] for row in db.execute("PRAGMA table_info(line_grants)")}
            if "access_mode" not in columns:
                db.execute("ALTER TABLE line_grants ADD COLUMN access_mode TEXT NOT NULL DEFAULT 'wireguard'")
            if "proxy_config" not in columns:
                db.execute("ALTER TABLE line_grants ADD COLUMN proxy_config TEXT NOT NULL DEFAULT '{}'")
            if "egress_policy" not in columns:
                db.execute("ALTER TABLE line_grants ADD COLUMN egress_policy TEXT NOT NULL DEFAULT 'source_physical'")
            lease_columns = {row[1] for row in db.execute("PRAGMA table_info(leases)")}
            if "transport_id" not in lease_columns:
                db.execute("ALTER TABLE leases ADD COLUMN transport_id TEXT")
            probe_columns = {row[1] for row in db.execute("PRAGMA table_info(probe_leases)")}
            if "probe_version" not in probe_columns:
                db.execute("ALTER TABLE probe_leases ADD COLUMN probe_version INTEGER NOT NULL DEFAULT 1")
            if "wireguard_public_key" not in probe_columns:
                db.execute("ALTER TABLE probe_leases ADD COLUMN wireguard_public_key TEXT")
            if "allocated_address" not in probe_columns:
                db.execute("ALTER TABLE probe_leases ADD COLUMN allocated_address TEXT")
            if "probe_address" not in probe_columns:
                db.execute("ALTER TABLE probe_leases ADD COLUMN probe_address TEXT")
            if "echo_addresses" not in probe_columns:
                db.execute("ALTER TABLE probe_leases ADD COLUMN echo_addresses TEXT")
            # Existing schema guards compare the worker version with meta.
            # Advance meta while holding the migration lock, before touching
            # guarded legacy rows; any later failure rolls everything back.
            db.execute("""INSERT INTO sna_schema_meta(id,schema_version) VALUES(1,?)
                ON CONFLICT(id) DO UPDATE SET schema_version=excluded.schema_version""",
                       (CLIENT_STORE_SCHEMA_VERSION,))
            db.execute("""UPDATE line_grants SET egress_policy=CASE
                WHEN access_mode='public_proxy' THEN 'source_proxy'
                ELSE 'source_physical' END
                WHERE egress_policy IS NULL OR TRIM(egress_policy)=''""")
            token_columns = {row[1] for row in db.execute("PRAGMA table_info(enrollment_tokens)")}
            if "revoked_at" not in token_columns:
                db.execute("ALTER TABLE enrollment_tokens ADD COLUMN revoked_at INTEGER")
            customer_columns = {row[1] for row in db.execute("PRAGMA table_info(customers)")}
            if "auth_epoch" not in customer_columns:
                db.execute("ALTER TABLE customers ADD COLUMN auth_epoch INTEGER NOT NULL DEFAULT 0")
            if "group_id" not in customer_columns:
                db.execute("ALTER TABLE customers ADD COLUMN group_id TEXT REFERENCES customer_groups(id)")
            device_columns = {row[1] for row in db.execute("PRAGMA table_info(devices)")}
            if "customer_epoch" not in device_columns:
                db.execute("ALTER TABLE devices ADD COLUMN customer_epoch INTEGER NOT NULL DEFAULT 0")
            db.execute("UPDATE customers SET auth_epoch=1 WHERE revoked_at IS NOT NULL AND auth_epoch=0")
            guarded_tables = (
                "customer_groups", "plans", "sources", "billing_anchors", "customers",
                "customer_service_terms", "devices",
                "enrollment_tokens", "line_grants", "leases", "probe_leases", "relay_readiness", "used_nonces",
                "usage_periods", "usage_reports", "proxy_users",
            )
            for table in guarded_tables:
                for event in ("INSERT", "UPDATE", "DELETE"):
                    trigger = f"sna_schema_guard_{table}_{event.lower()}"
                    db.execute(f"""CREATE TRIGGER IF NOT EXISTS {trigger}
                        BEFORE {event} ON {table}
                        WHEN sna_schema_version() != (SELECT schema_version FROM sna_schema_meta WHERE id=1)
                        BEGIN SELECT RAISE(ABORT, '数据库 schema 版本不兼容'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS sna_schema_guard_meta_update
                BEFORE UPDATE ON sna_schema_meta
                WHEN sna_schema_version() != NEW.schema_version
                BEGIN SELECT RAISE(ABORT, '数据库 schema 版本不兼容'); END""")
            db.commit()

    @staticmethod
    def _id(prefix: str) -> str:
        return prefix + "_" + uuid.uuid4().hex

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    @staticmethod
    def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}

    def _insert_line_grant(self, db: sqlite3.Connection, value: dict[str, Any]) -> None:
        """Insert a grant without dropping columns owned by an older commercial schema."""

        columns = [
            "id", "customer_id", "alias", "tunnel", "endpoint", "enabled",
            "relay_public_key", "allocated_address", "dns", "allowed_ips", "mtu",
            "relay_interface", "egress_interface", "expires_at", "created_at",
        ]
        values = [value[name] for name in columns]
        optional = {
            "access_mode": value["access_mode"],
            "proxy_config": value["proxy_config"],
            "egress_policy": value["egress_policy"],
            "transport_policy": value["transport_policy"],
        }
        available = self._table_columns(db, "line_grants")
        for name, item in optional.items():
            if name in available:
                columns.append(name)
                values.append(item)
        placeholders = ",".join("?" for _ in values)
        db.execute(
            f"INSERT INTO line_grants({','.join(columns)}) VALUES({placeholders})",
            tuple(values),
        )

    @staticmethod
    def _normalise_wireguard_address(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            return ""
        try:
            address = ipaddress.ip_interface(value.strip())
        except (TypeError, ValueError):
            raise ClientStoreError("WireGuard 客户地址无效") from None
        if address.version != 4 or address.network.prefixlen != 32:
            raise ClientStoreError("WireGuard 客户地址必须是 IPv4 /32")
        return str(address)

    @staticmethod
    def _valid_wireguard_key(value: object) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            return len(base64.b64decode(value, validate=True)) == 32
        except Exception:
            return False

    @staticmethod
    def _active_wireguard_grants(db: sqlite3.Connection, customer_id: str, now: int) -> list[sqlite3.Row]:
        return db.execute("""SELECT * FROM line_grants
            WHERE customer_id=? AND access_mode='wireguard' AND enabled=1
            AND (expires_at IS NULL OR expires_at>?) ORDER BY id""", (customer_id, now)).fetchall()

    @staticmethod
    def _ensure_grant_address_is_unique(db: sqlite3.Connection, relay_interface: str,
                                        allocated_address: str, *, exclude_grant_id: str | None = None) -> None:
        if not allocated_address:
            return
        # A configured probe pool is reserved even while no probe is active.
        # A later grant must not silently take an address from that pool.
        raw_pools = os.environ.get("SNA_PROBE_V2_POOLS", "")
        if raw_pools:
            try:
                pools = json.loads(raw_pools)
                if not isinstance(pools, dict):
                    raise ValueError()
                reserved_pools = [ipaddress.IPv4Network(value, strict=True)
                                  for value in pools.values()]
            except (TypeError, ValueError):
                raise ClientStoreError("检测地址池配置无效") from None
            if any(ipaddress.IPv4Interface(allocated_address).ip in pool
                   for pool in reserved_pools):
                raise ClientStoreError("WireGuard 客户地址属于预留检测地址池")
        raw_aliases = os.environ.get("SNA_PROBE_V2_RELAY_ADDRESSES", "")
        if raw_aliases:
            try:
                aliases = json.loads(raw_aliases)
                if not isinstance(aliases, dict):
                    raise ValueError()
                if any(not isinstance(items, list) for items in aliases.values()):
                    raise ValueError()
                address = ipaddress.IPv4Interface(allocated_address).ip
                reserved_aliases = {ipaddress.IPv4Interface(item).ip
                                    for items in aliases.values() for item in items}
            except (TypeError, ValueError):
                raise ClientStoreError("中继检测地址配置无效") from None
            if address in reserved_aliases:
                raise ClientStoreError("WireGuard 客户地址属于预留中继检测地址")
        rows = db.execute("""SELECT id FROM line_grants
            WHERE access_mode='wireguard' AND relay_interface=? AND allocated_address=?
            AND (? IS NULL OR id<>?) LIMIT 1""",
                          (relay_interface, allocated_address, exclude_grant_id, exclude_grant_id)).fetchone()
        if rows:
            raise ClientStoreError("WireGuard 客户地址已被同一中继接口占用")
        active_probe = db.execute("""SELECT 1 FROM probe_leases p
            JOIN leases l ON l.id=p.lease_id JOIN line_grants g ON g.id=l.grant_id
            WHERE p.probe_version=2 AND g.relay_interface=? AND p.allocated_address=?
              AND l.revoked_at IS NULL AND l.expires_at>? LIMIT 1""",
            (relay_interface, allocated_address, int(time.time()))).fetchone()
        if active_probe:
            raise ClientStoreError("WireGuard 客户地址正由检测租约占用")

    @classmethod
    def _ensure_active_wireguard_state(cls, db: sqlite3.Connection, now: int) -> None:
        """Reject ambiguous state before it can be handed to a relay.

        The relay interface is the routing-domain boundary.  Public endpoint
        strings are deliberately not used here: two endpoints can terminate
        on the same local WireGuard interface, while the same endpoint can be
        fronted by different interfaces.
        """
        rows = db.execute("""SELECT l.id,l.device_id,l.grant_id,g.relay_interface,
                g.egress_interface,g.relay_public_key,
                COALESCE(probe.allocated_address,g.allocated_address) allocated_address,
                COALESCE(probe.wireguard_public_key,d.wireguard_public_key) wireguard_public_key
            FROM leases l
            JOIN line_grants g ON g.id=l.grant_id
            JOIN devices d ON d.id=l.device_id
            JOIN customers c ON c.id=l.customer_id
            JOIN plans plan ON plan.id=c.plan_id
            LEFT JOIN probe_leases probe ON probe.lease_id=l.id
            WHERE l.revoked_at IS NULL AND l.expires_at>?
              AND d.enabled=1 AND c.enabled=1 AND c.revoked_at IS NULL
              AND plan.enabled=1 AND g.enabled=1
              AND (g.expires_at IS NULL OR g.expires_at>?)
              AND g.access_mode='wireguard'""", (now, now)).fetchall()
        addresses: dict[tuple[str, str], str] = {}
        keys: dict[tuple[str, str], str] = {}
        for row in rows:
            address = cls._normalise_wireguard_address(row["allocated_address"])
            if (not address or not row["relay_interface"] or not row["egress_interface"] or
                    not cls._valid_wireguard_key(row["relay_public_key"]) or
                    not cls._valid_wireguard_key(row["wireguard_public_key"])):
                raise ClientStoreError("检测到不完整的 WireGuard 授权，已阻止中继发布")
            address_key = (row["relay_interface"], address)
            previous = addresses.get(address_key)
            if previous and previous != row["id"]:
                raise ClientStoreError("检测到同一中继接口的客户地址冲突，已阻止新的授权")
            addresses[address_key] = row["id"]
            key_key = (row["relay_interface"], row["wireguard_public_key"])
            previous = keys.get(key_key)
            if previous and previous != row["id"]:
                raise ClientStoreError("检测到同一 WireGuard 接口的公钥冲突，已阻止新的授权")
            keys[key_key] = row["id"]

    @classmethod
    def _ensure_wireguard_candidate(cls, db: sqlite3.Connection, device_id: str, grant_id: str,
                                    now: int) -> None:
        row = db.execute("""SELECT g.*,d.wireguard_public_key,c.id customer_id
            FROM line_grants g JOIN devices d ON d.id=? JOIN customers c ON c.id=d.customer_id
            WHERE g.id=? AND g.customer_id=c.id""", (device_id, grant_id)).fetchone()
        if not row:
            raise ClientStoreError("设备或线路未获授权")
        address = cls._normalise_wireguard_address(row["allocated_address"])
        if not address or not row["relay_interface"] or not row["egress_interface"]:
            raise ClientStoreError("WireGuard 线路缺少可执行的地址或接口归属")
        if not cls._valid_wireguard_key(row["relay_public_key"]) or not cls._valid_wireguard_key(row["wireguard_public_key"]):
            raise ClientStoreError("WireGuard 线路或设备公钥无效")
        cls._ensure_grant_address_is_unique(db, row["relay_interface"], address, exclude_grant_id=grant_id)
        conflict = db.execute("""SELECT l.id FROM leases l
            JOIN line_grants g ON g.id=l.grant_id
            LEFT JOIN probe_leases p ON p.lease_id=l.id
            WHERE l.revoked_at IS NULL AND l.expires_at>? AND g.enabled=1
              AND (g.expires_at IS NULL OR g.expires_at>?) AND g.access_mode='wireguard'
              AND g.relay_interface=? AND (COALESCE(p.allocated_address,g.allocated_address)=? OR EXISTS(
                  SELECT 1 FROM devices d WHERE d.id=l.device_id
                  AND COALESCE(p.wireguard_public_key,d.wireguard_public_key)=?))
              AND NOT (l.device_id=? AND l.grant_id=?) LIMIT 1""",
                             (now, now, row["relay_interface"], address,
                              row["wireguard_public_key"], device_id, grant_id)).fetchone()
        if conflict:
            raise ClientStoreError("WireGuard 地址或公钥已被另一有效设备占用")
        device_count = db.execute("SELECT COUNT(*) FROM devices WHERE customer_id=? AND enabled=1",
                                  (row["customer_id"],)).fetchone()[0]
        if device_count > 1:
            raise ClientStoreError("当前 WireGuard 产品不支持同一客户多设备共用地址")

    def create_group(self, name: str, *, group_id: str | None = None,
                     now: int | None = None) -> dict[str, Any]:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ClientStoreError('客户分组名称无效')
        group_id = self._id('group') if group_id is None else group_id
        if not isinstance(group_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', group_id):
            raise ClientStoreError('客户分组编号无效')
        now = int(time.time()) if now is None else now
        with self._lock, self._connect() as db:
            try:
                db.execute('INSERT INTO customer_groups(id,name,enabled,created_at) VALUES(?,?,1,?)',
                           (group_id, name.strip(), now))
            except sqlite3.IntegrityError as exc:
                raise ClientStoreError('客户分组编号或名称已存在') from exc
        return {'id': group_id, 'name': name.strip(), 'enabled': 1, 'created_at': now}

    def list_groups(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT * FROM customer_groups ORDER BY name').fetchall()]

    @staticmethod
    def _require_enabled_group(db, group_id: str | None) -> None:
        if group_id is None:
            return
        row = db.execute('SELECT enabled FROM customer_groups WHERE id=?', (group_id,)).fetchone()
        if not row or not row['enabled']:
            raise ClientStoreError('客户分组不存在或已停用')

    def set_customer_group(self, customer_id: str, group_id: str | None) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._require_enabled_group(db, group_id)
            changed = db.execute('UPDATE customers SET group_id=? WHERE id=?',
                                 (group_id, customer_id)).rowcount
            if not changed:
                raise ClientStoreError('客户不存在')
        return self.customer(customer_id)

    def create_plan(self, name: str, *, plan_id: str | None = None, download_bps: int | None = None,
                    upload_bps: int | None = None, quota_bytes: int | None = None,
                    period_seconds: int = 30 * 86400, max_devices: int = 1,
                    lease_seconds: int = LEASE_NEVER_EXPIRES_AT,
                    now: int | None = None) -> dict[str, Any]:
        values = (download_bps, upload_bps, quota_bytes)
        if (not name.strip() or any(v is not None and v < 0 for v in values) or
                period_seconds < 60 or max_devices < 1 or
                isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or
                not 60 <= lease_seconds <= (2**63 - 1)):
            raise ClientStoreError("套餐参数无效")
        now = int(time.time()) if now is None else now
        plan_id = plan_id or self._id("plan")
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO plans(
                id,name,download_bps,upload_bps,quota_bytes,period_seconds,max_devices,
                lease_seconds,enabled,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
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

    def list_sources(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [json.loads(row['configuration']) | {'id': row['id'], 'name': row['name']}
                    for row in db.execute('SELECT * FROM sources ORDER BY name')]

    def save_source(self, value: dict) -> dict:
        from .client_subscription import _safe_host_port
        name = str(value.get('name', '')).strip()
        source_id = str(value.get('id') or self._id('source'))
        endpoint = str(value.get('endpoint', ''))
        _safe_host_port(endpoint)
        pool = ipaddress.ip_network(str(value.get('address_pool', '')), strict=True)
        if pool.version != 4 or pool.num_addresses < 8 or pool.num_addresses > 65536:
            raise ClientStoreError('地址池必须是具有至少 8 个地址的 IPv4 网段')
        key = str(value.get('relay_public_key', ''))
        try:
            if len(base64.b64decode(key, validate=True)) != 32:
                raise ValueError()
        except Exception:
            raise ClientStoreError('源网 WireGuard 公钥无效') from None
        if not name or not str(value.get('relay_interface', '')).strip():
            raise ClientStoreError('源网名称和中继接口不能为空')
        transport_policy = _normalise_transport_policy(value.get('transport_policy'), 'wireguard')
        record = {k: str(value.get(k, '')) for k in ('endpoint', 'relay_public_key',
                  'relay_interface', 'egress_interface')}
        record |= {'address_pool': str(pool), 'dns': str(value.get('dns') or '223.5.5.5,1.1.1.1'),
                   'allowed_ips': '0.0.0.0/1,128.0.0.0/1', 'mtu': 1420,
                   'transport_policy': transport_policy}
        from .source_egress import validate_egresses
        if 'egresses' in value:
            record['egresses'] = validate_egresses(value['egresses'], record['relay_interface'])
        if 'egress_mode' in value or 'egress_policy' in value:
            record['egress_policy'] = _normalise_egress_policy(value.get('egress_policy', value.get('egress_mode')), 'wireguard')
        with self._lock, self._connect() as db:
            db.execute('''INSERT INTO sources(id,name,configuration) VALUES(?,?,?)
                       ON CONFLICT(id) DO UPDATE SET name=excluded.name,configuration=excluded.configuration''',
                       (source_id, name, json.dumps(record)))
        return record | {'id': source_id, 'name': name}

    def generate_monthly_subscription(self, name: str, quota_gb: int | None,
                                      source_ids: list[str], *, access_mode: str = "wireguard",
                                      download_bps: int | None = 50000000,
                                      upload_bps: int | None = 10000000, now: int | None = None,
                                      egress_modes: list[str] | None = None,
                                      group_id: str | None = None,
                                      validity_days: int = 30,
                                      start_on_enrollment: bool = False,
                                      required_grants: int | None = None) -> dict:
        """Atomically create a bounded-duration customer, grants and enrollment token."""
        if access_mode not in ("wireguard", "public_proxy"):
            raise ClientStoreError("套餐接入模式无效")
        if not name.strip() or (quota_gb is not None and
                (type(quota_gb) is not int or not 1 <= quota_gb <= 100000)):
            raise ClientStoreError('客户名称或流量额度无效（1–100000 GiB，或不限流量）')
        if type(validity_days) is not int or not 1 <= validity_days <= 3650:
            raise ClientStoreError('使用期限必须为 1–3650 天')
        if start_on_enrollment:
            if type(required_grants) is not int or not 1 <= required_grants <= 32:
                raise ClientStoreError('首次导入起算套餐必须指定 1–32 条完整授权线路')
        elif required_grants is not None:
            raise ClientStoreError('仅首次导入起算套餐可以指定线路数量')
        if access_mode == "wireguard" and not source_ids:
            raise ClientStoreError('借网套餐必须选择至少一个源网')
        if any(v is not None and (type(v) is not int or not 1 <= v <= 10000000000)
               for v in (download_bps, upload_bps)):
            raise ClientStoreError('速度上限必须为 1–10000000000 bps 或不限速')
        if access_mode == 'public_proxy' and source_ids:
            raise ClientStoreError('Cloud 公网代理不得同时选择源网节点')
        if egress_modes is not None and access_mode != 'wireguard':
            raise ClientStoreError('Source egress choices require WireGuard access')
        now = int(time.time()) if now is None else now
        duration_seconds = validity_days * 86400
        expires = None if start_on_enrollment else now + duration_seconds
        plan_id, customer_id = self._id('plan'), self._id('cus')
        token = new_secret('enr_')
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._require_enabled_group(db, group_id)
            sources = []
            reserved = {}
            from .source_egress import expand_source_egresses
            for source_id in dict.fromkeys(source_ids if access_mode == "wireguard" else []):
                row = db.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
                if not row:
                    raise ClientStoreError('所选源网不存在')
                base = json.loads(row['configuration']) | {'name': row['name']}
                for source in expand_source_egresses(base, egress_modes):
                    pool = ipaddress.ip_network(source['address_pool'])
                    allocated = reserved.setdefault(source['relay_interface'], set())
                    allocated.update(r['allocated_address'] for r in db.execute(
                        'SELECT allocated_address FROM line_grants WHERE relay_interface=?', (source['relay_interface'],)))
                    hosts = iter(pool.hosts()); next(hosts, None)
                    address = next((str(host) + '/32' for host in hosts if str(host) + '/32' not in allocated), None)
                    if address is None:
                        raise ClientStoreError('源网客户地址池已满')
                    allocated.add(address)
                    sources.append((source, address))
            quota = None if quota_gb is None else quota_gb * 1024 ** 3
            product = '仅公网代理' if access_mode == 'public_proxy' else '入网'
            db.execute('''INSERT INTO plans(
                id,name,download_bps,upload_bps,quota_bytes,period_seconds,max_devices,
                lease_seconds,enabled,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                       (plan_id, str(validity_days) + '天' + product + ' ' + ('不限流量' if quota_gb is None else str(quota_gb) + ' GiB'),
                        download_bps, upload_bps, quota, validity_days * 86400, 1,
                        duration_seconds, 1, now))
            db.execute('''INSERT INTO customers(
                id,display_name,plan_id,enabled,revoked_at,created_at,auth_epoch,group_id)
                VALUES(?,?,?,?,?,?,?,?)''', (customer_id, name.strip(), plan_id, 1, None, now, 0, group_id))
            db.execute('INSERT INTO billing_anchors(customer_id,anchor) VALUES(?,?)', (customer_id, now))
            if start_on_enrollment:
                db.execute('''INSERT INTO customer_service_terms(
                    customer_id,duration_seconds,required_grants,started_at,expires_at)
                    VALUES(?,?,?,?,?)''', (customer_id, duration_seconds, required_grants, None, None))
            for source, address in sources:
                self._insert_line_grant(db, {
                    'id': self._id('grant'), 'customer_id': customer_id, 'alias': source['name'],
                    'tunnel': 'customer-online', 'endpoint': source['endpoint'], 'enabled': 1,
                    'relay_public_key': source['relay_public_key'], 'allocated_address': address,
                    'dns': source['dns'], 'allowed_ips': source['allowed_ips'], 'mtu': source['mtu'],
                    'relay_interface': source['relay_interface'], 'egress_interface': source['egress_interface'],
                    'expires_at': expires, 'created_at': now, 'access_mode': 'wireguard',
                    'proxy_config': '{}', 'egress_policy': source['egress_policy'],
                    'transport_policy': _normalise_transport_policy(source.get('transport_policy'), 'wireguard'),
                })
            if access_mode == 'public_proxy':
                config = json.dumps(PUBLIC_PROXY_CONFIG, separators=(',', ':'))
                self._insert_line_grant(db, {
                    'id': self._id('grant'), 'customer_id': customer_id, 'alias': '公网代理',
                    'tunnel': '', 'endpoint': f"{PUBLIC_PROXY_CONFIG['server']}:{PUBLIC_PROXY_CONFIG['port']}",
                    'enabled': 1, 'relay_public_key': '', 'allocated_address': '', 'dns': '',
                    'allowed_ips': '', 'mtu': 1420, 'relay_interface': 'cloud-vmess',
                    'egress_interface': '', 'expires_at': expires, 'created_at': now,
                    'access_mode': 'public_proxy', 'proxy_config': config,
                    'egress_policy': 'source_proxy', 'transport_policy': 'public',
                })
            db.execute('INSERT INTO enrollment_tokens(digest,customer_id,expires_at,used_at,created_at,revoked_at) VALUES(?,?,?,?,?,?)',
                       (secret_digest(token, 'enrollment'), customer_id, now + 86400, None, now, None))
        return {'customer_id': customer_id, 'group_id': group_id,
                'plan_id': plan_id, 'token': token, 'expires_at': expires,
                'service_duration_seconds': duration_seconds,
                'service_starts_on_enrollment': bool(start_on_enrollment),
                'access_mode': access_mode,
                'enrollment_expires_at': now + 86400, 'quota_bytes': quota}

    def create_customer(self, display_name: str, plan_id: str, *, customer_id: str | None = None,
                        now: int | None = None, group_id: str | None = None) -> dict[str, Any]:
        if not display_name.strip():
            raise ClientStoreError("客户名称无效")
        now = int(time.time()) if now is None else now
        customer_id = customer_id or self._id("cus")
        with self._lock, self._connect() as db:
            self._require_enabled_group(db, group_id)
            db.execute('''INSERT INTO customers(
                id,display_name,plan_id,enabled,revoked_at,created_at,auth_epoch,group_id)
                VALUES(?,?,?,?,?,?,?,?)''',
                       (customer_id, display_name.strip(), plan_id, 1, None, now, 0, group_id))
        return self.customer(customer_id)

    def customer(self, customer_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not row:
            raise ClientStoreError("客户不存在")
        return dict(row)

    def customer_service_term(self, customer_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM customer_service_terms WHERE customer_id=?",
                             (customer_id,)).fetchone()
        return dict(row) if row else None

    def list_customers(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM customers ORDER BY created_at DESC").fetchall()]

    def list_devices(self, customer_id: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM devices", ()
        if customer_id is not None:
            sql, args = sql + " WHERE customer_id=?", (customer_id,)
        with self._connect() as db:
            return [dict(row) for row in db.execute(sql + " ORDER BY created_at DESC", args).fetchall()]

    def create_enrollment_token(self, customer_id: str, *, ttl: int | None = 900,
                                now: int | None = None,
                                audit_callback: Callable[[sqlite3.Connection, str, int], None] | None = None) -> str:
        now = int(time.time()) if now is None else now
        if ttl is not None and (type(ttl) is not int or ttl < 60 or
                                ttl >= ENROLLMENT_NEVER_EXPIRES_AT - now):
            raise ClientStoreError("开户令牌有效期无效")
        expires_at = ENROLLMENT_NEVER_EXPIRES_AT if ttl is None else now + ttl
        token = new_secret("enr_")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            customer = db.execute("SELECT enabled FROM customers WHERE id=?", (customer_id,)).fetchone()
            if not customer or not customer["enabled"]:
                raise ClientStoreError("客户不存在或已停用")
            digest = secret_digest(token, "enrollment")
            # Issuing a new token is an explicit rotation.  Previously issued
            # unused tokens must not remain valid after a replacement.
            db.execute("UPDATE enrollment_tokens SET revoked_at=? WHERE customer_id=? AND used_at IS NULL AND revoked_at IS NULL",
                       (now, customer_id))
            db.execute("INSERT INTO enrollment_tokens(digest,customer_id,expires_at,used_at,created_at,revoked_at) VALUES(?,?,?,?,?,?)",
                       (digest, customer_id, expires_at, None, now, None))
            if audit_callback is not None:
                audit_callback(db, digest, expires_at)
        return token

    def enrollment_customer_id(self, token: str, *, now: int | None = None) -> str:
        """Read-only preflight; enrollment still rechecks the token atomically."""
        now = int(time.time()) if now is None else now
        with self._connect() as db:
            row = db.execute("SELECT e.customer_id,e.used_at,e.revoked_at,e.expires_at,c.enabled "
                             "FROM enrollment_tokens e JOIN customers c ON c.id=e.customer_id WHERE e.digest=?",
                             (secret_digest(token, 'enrollment'),)).fetchone()
        if not row or row['used_at'] is not None or row['revoked_at'] is not None or row['expires_at'] <= now or not row['enabled']:
            raise ClientStoreError('开户令牌无效、已使用或已过期')
        return str(row['customer_id'])

    def enroll_device(self, token: str, public_key: str, label: str, *, wireguard_public_key: str | None = None,
                      device_id: str | None = None,
                      now: int | None = None,
                      audit_callback: Callable[[sqlite3.Connection, dict[str, Any]], None] | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        digest = secret_digest(token, "enrollment")
        device_id = device_id or self._id("dev")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT e.*, c.enabled customer_enabled, c.plan_id, c.auth_epoch FROM enrollment_tokens e JOIN customers c ON c.id=e.customer_id WHERE digest=?", (digest,)).fetchone()
            if (not row or row["used_at"] is not None or row["revoked_at"] is not None or
                    row["expires_at"] <= now or not row["customer_enabled"]):
                raise ClientStoreError("开户令牌无效、已使用或已过期")
            plan = db.execute("SELECT * FROM plans WHERE id=? AND enabled=1", (row["plan_id"],)).fetchone()
            count = db.execute("SELECT COUNT(*) n FROM devices WHERE customer_id=? AND enabled=1", (row["customer_id"],)).fetchone()["n"]
            if not plan or count >= plan["max_devices"]:
                raise ClientStoreError("设备数量已达到套餐上限")
            if self._active_wireguard_grants(db, row["customer_id"], now) and count:
                raise ClientStoreError("当前 WireGuard 产品不支持同一客户多设备共用地址")
            term = db.execute("SELECT * FROM customer_service_terms WHERE customer_id=?",
                              (row["customer_id"],)).fetchone()
            if term is not None:
                if term["started_at"] is None:
                    grants = db.execute("SELECT id,expires_at FROM line_grants WHERE customer_id=? AND enabled=1",
                                        (row["customer_id"],)).fetchall()
                    if (len(grants) != term["required_grants"] or
                            any(grant["expires_at"] is not None for grant in grants)):
                        raise ClientStoreError("首次导入前授权线路尚未完整配置")
                    service_expires = now + term["duration_seconds"]
                    db.execute("UPDATE line_grants SET expires_at=? WHERE customer_id=? AND enabled=1",
                               (service_expires, row["customer_id"]))
                    db.execute("UPDATE customer_service_terms SET started_at=?,expires_at=? WHERE customer_id=?",
                               (now, service_expires, row["customer_id"]))
                    db.execute("UPDATE billing_anchors SET anchor=? WHERE customer_id=?",
                               (now, row["customer_id"]))
                elif term["expires_at"] <= now:
                    raise ClientStoreError("服务期限已结束")
            db.execute('''INSERT INTO devices(
                id,customer_id,label,public_key,wireguard_public_key,enabled,revoked_at,
                created_at,last_seen_at,customer_epoch)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                       (device_id, row["customer_id"], label.strip() or "客户设备", public_key,
                        wireguard_public_key or public_key, 1, None, now, now, row["auth_epoch"]))
            db.execute("UPDATE enrollment_tokens SET used_at=? WHERE digest=?", (now, digest))
            device = dict(db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone())
            if audit_callback is not None:
                audit_callback(db, device)
        return device

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
                   access_mode: str = "wireguard", proxy_config: dict[str, Any] | None = None,
                   egress_policy: str | None = None,
                   transport_policy: str | None = None,
                   grant_id: str | None = None, expires_at: int | None = None,
                   now: int | None = None) -> dict[str, Any]:
        if access_mode not in ("wireguard", "public_proxy"):
            raise ClientStoreError("线路接入模式无效")
        egress_policy = _normalise_egress_policy(egress_policy, access_mode)
        transport_policy = _normalise_transport_policy(transport_policy, access_mode)
        if not alias.strip() or not endpoint.strip() or (access_mode == "wireguard" and not tunnel.strip()):
            raise ClientStoreError("线路授权参数无效")
        allocated_address = self._normalise_wireguard_address(allocated_address) if access_mode == "wireguard" else ""
        now = int(time.time()) if now is None else now
        grant_id = grant_id or self._id("grant")
        if not 576 <= mtu <= 9000:
            raise ClientStoreError("线路 MTU 无效")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            term = db.execute("SELECT * FROM customer_service_terms WHERE customer_id=?",
                              (customer_id,)).fetchone()
            if term is not None:
                if term["started_at"] is None:
                    count = db.execute("SELECT COUNT(*) FROM line_grants WHERE customer_id=? AND enabled=1",
                                       (customer_id,)).fetchone()[0]
                    if expires_at is not None or count >= term["required_grants"]:
                        raise ClientStoreError("首次导入前线路数量或到期时间不符合套餐约定")
                elif (expires_at is None or expires_at <= now or
                      expires_at > term["expires_at"]):
                    raise ClientStoreError("新增线路不得超过已启动的服务期限")
            if access_mode == "wireguard":
                if db.execute("SELECT COUNT(*) FROM devices WHERE customer_id=? AND enabled=1",
                              (customer_id,)).fetchone()[0] > 1:
                    raise ClientStoreError("当前 WireGuard 产品不支持同一客户多设备共用地址")
                self._ensure_grant_address_is_unique(db, relay_interface, allocated_address)
            self._insert_line_grant(db, {
                'id': grant_id, 'customer_id': customer_id, 'alias': alias.strip(),
                'tunnel': tunnel.strip(), 'endpoint': endpoint.strip(), 'enabled': 1,
                'relay_public_key': relay_public_key, 'allocated_address': allocated_address,
                'dns': dns, 'allowed_ips': allowed_ips, 'mtu': mtu,
                'relay_interface': relay_interface, 'egress_interface': egress_interface,
                'expires_at': expires_at, 'created_at': now, 'access_mode': access_mode,
                'proxy_config': json.dumps(proxy_config or {}, separators=(',', ':')),
                'egress_policy': egress_policy, 'transport_policy': transport_policy,
            })
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
            rows = db.execute("SELECT id,customer_id,device_id,grant_id,issued_at,expires_at,revoked_at,transport_id,last_rx,last_tx FROM leases" + where + " ORDER BY issued_at DESC", args).fetchall()
        return [dict(row) for row in rows]

    def lease_relay_id(self, lease_id: str) -> str:
        with self._connect() as db:
            row = db.execute("SELECT g.relay_interface FROM leases l JOIN line_grants g ON g.id=l.grant_id WHERE l.id=?",
                             (lease_id,)).fetchone()
        if not row:
            raise ClientStoreError("租约不存在")
        return row["relay_interface"]

    def grant_relay_id(self, grant_id: str) -> str:
        with self._connect() as db:
            row = db.execute("SELECT relay_interface FROM line_grants WHERE id=?",
                             (grant_id,)).fetchone()
        if not row:
            raise ClientStoreError("线路授权不存在")
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
            if kind == "device" and enabled:
                row = db.execute("""SELECT d.customer_epoch,c.auth_epoch,c.enabled customer_enabled
                    FROM devices d JOIN customers c ON c.id=d.customer_id WHERE d.id=?""", (object_id,)).fetchone()
                if not row:
                    raise ClientStoreError("对象不存在")
                if not row["customer_enabled"] or row["customer_epoch"] != row["auth_epoch"]:
                    raise ClientStoreError("设备已撤销，请重新开户注册")
                device = db.execute("SELECT customer_id FROM devices WHERE id=?", (object_id,)).fetchone()
                if db.execute("""SELECT 1 FROM line_grants
                    WHERE customer_id=? AND access_mode='wireguard' AND enabled=1
                    AND (expires_at IS NULL OR expires_at>?) LIMIT 1""",
                              (device["customer_id"], now)).fetchone():
                    enabled_devices = db.execute(
                        "SELECT COUNT(*) FROM devices WHERE customer_id=? AND enabled=1",
                        (device["customer_id"],)).fetchone()[0]
                    if enabled_devices:
                        raise ClientStoreError("当前 WireGuard 产品不支持同一客户多设备共用地址")
            if kind == "grant":
                grant = db.execute("SELECT * FROM line_grants WHERE id=?", (object_id,)).fetchone()
                if not grant:
                    raise ClientStoreError("对象不存在")
                if enabled and grant["access_mode"] == "wireguard":
                    if db.execute("SELECT COUNT(*) FROM devices WHERE customer_id=? AND enabled=1",
                                  (grant["customer_id"],)).fetchone()[0] > 1:
                        raise ClientStoreError("当前 WireGuard 产品不支持同一客户多设备共用地址")
                    address = self._normalise_wireguard_address(grant["allocated_address"])
                    self._ensure_grant_address_is_unique(
                        db, grant["relay_interface"], address, exclude_grant_id=object_id)
                cursor = db.execute("UPDATE line_grants SET enabled=? WHERE id=?", (int(enabled), object_id))
            elif kind == "customer":
                cursor = db.execute("""UPDATE customers SET enabled=?,revoked_at=?,
                    auth_epoch=auth_epoch+CASE WHEN ?=0 THEN 1 ELSE 0 END WHERE id=?""",
                                    (int(enabled), None if enabled else now, int(enabled), object_id))
            else:
                cursor = db.execute(f"UPDATE {table} SET enabled=?,revoked_at=? WHERE id=?",
                                    (int(enabled), None if enabled else now, object_id))
            if cursor.rowcount == 0:
                raise ClientStoreError("对象不存在")
            if not enabled:
                db.execute(f"UPDATE leases SET revoked_at=? WHERE {lease_column}=? AND revoked_at IS NULL",
                           (now, object_id))
                if kind == "customer":
                    db.execute("UPDATE devices SET enabled=0,revoked_at=COALESCE(revoked_at,?) WHERE customer_id=?",
                               (now, object_id))
                    db.execute("UPDATE enrollment_tokens SET revoked_at=? WHERE customer_id=? AND used_at IS NULL AND revoked_at IS NULL",
                               (now, object_id))

    def revoke_enrollment_tokens(self, customer_id: str, *, now: int | None = None,
                                 audit_callback: Callable[[sqlite3.Connection, int], None] | None = None) -> int:
        now = int(time.time()) if now is None else now
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone():
                raise ClientStoreError("客户不存在")
            count = db.execute("UPDATE enrollment_tokens SET revoked_at=? WHERE customer_id=? AND used_at IS NULL AND revoked_at IS NULL",
                               (now, customer_id)).rowcount
            if audit_callback is not None:
                audit_callback(db, count)
            return count

    def authenticate_device(self, device_id: str, signed: dict[str, Any], signature: str,
                            verify_callback: Callable[[dict[str, Any], dict[str, Any], str], bool],
                            *, now: int | None = None, nonce_ttl: int = 300) -> dict[str, Any]:
        """Verify a device and consume its nonce in one serialized transaction.

        The state read, signature verification and nonce insert all happen
        while BEGIN IMMEDIATE holds SQLite's cross-process writer lock.  A
        disable/revoke transaction therefore either commits before this
        decision and is observed, or commits after a successful authentication.
        The exception carries only an internal reason; callers must expose the
        same generic authentication response for every rejection.
        """
        now = int(time.time()) if now is None else now
        nonce = signed.get("nonce") if isinstance(signed, dict) else None
        if not isinstance(nonce, str) or not 1 <= nonce_ttl <= 3600:
            raise ClientAuthenticationError("nonce_invalid")
        digest = secret_digest(nonce, "nonce")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT d.*, c.enabled AS customer_enabled,
                c.auth_epoch AS customer_auth_epoch
                FROM devices d JOIN customers c ON c.id=d.customer_id
                WHERE d.id=?""", (device_id,)).fetchone()
            if not row:
                raise ClientAuthenticationError("unknown_device")
            if not row["enabled"]:
                raise ClientAuthenticationError("device_disabled")
            if not row["customer_enabled"]:
                raise ClientAuthenticationError("customer_disabled")
            if row["customer_epoch"] != row["customer_auth_epoch"]:
                raise ClientAuthenticationError("epoch_mismatch")
            try:
                valid_signature = bool(verify_callback(dict(row), signed, signature))
            except Exception:
                valid_signature = False
            if not valid_signature:
                raise ClientAuthenticationError("signature_invalid")
            if len(nonce) < 16:
                raise ClientAuthenticationError("nonce_invalid")
            db.execute("DELETE FROM used_nonces WHERE expires_at<?", (now,))
            try:
                db.execute("INSERT INTO used_nonces(scope,nonce_digest,expires_at) VALUES(?,?,?)",
                           (device_id, digest, now + nonce_ttl))
            except sqlite3.IntegrityError:
                raise ClientAuthenticationError("nonce_replay") from None
            return dict(row)

    def consume_nonce(self, scope: str, nonce: str, *, ttl: int = 300, now: int | None = None) -> None:
        if not scope or len(nonce) < 16 or not 1 <= ttl <= 3600:
            raise ClientStoreError("请求随机标识无效")
        now = int(time.time()) if now is None else now
        digest = secret_digest(nonce, "nonce")
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM used_nonces WHERE expires_at<?", (now,))
            try:
                db.execute("INSERT INTO used_nonces(scope,nonce_digest,expires_at) VALUES(?,?,?)",
                           (scope, digest, now + ttl))
            except sqlite3.IntegrityError:
                raise ClientStoreError("请求已处理，请勿重放") from None

    @staticmethod
    def _require_rate_capabilities(row: sqlite3.Row, capabilities: dict[str, Any] | None) -> None:
        if row["access_mode"] != "wireguard":
            return
        capabilities = capabilities or {}
        for direction in ("download", "upload"):
            if row[f"{direction}_bps"] is not None and capabilities.get(f"{direction}_rate_limit") not in (True, "enforced"):
                raise ClientStoreError("当前中继未验证该方向的 WireGuard 限速能力，已拒绝签发")

    def _issue_lease_in_db(self, db: sqlite3.Connection, device_id: str, grant_id: str, *,
                           ttl: int | None, now: int, relay_capabilities: dict[str, Any] | None = None,
                           supersede_id: str | None = None, transport_id: str | None = None,
                           probe_v2: bool = False) -> str:
        lease_id, token = self._id("lease"), new_secret("lea_")
        row = db.execute("""SELECT d.customer_id,d.enabled device_enabled,c.enabled customer_enabled,
              c.revoked_at customer_revoked_at,
              c.plan_id,g.enabled grant_enabled,g.expires_at grant_expires,g.customer_id grant_customer,
              g.access_mode,g.relay_interface,g.allocated_address,g.relay_public_key,
              p.enabled plan_enabled,p.lease_seconds,p.quota_bytes,p.period_seconds,
              p.download_bps,p.upload_bps FROM devices d JOIN customers c ON c.id=d.customer_id
              JOIN plans p ON p.id=c.plan_id JOIN line_grants g ON g.id=? WHERE d.id=?""",
                           (grant_id, device_id)).fetchone()
        if (not row or not all(row[k] for k in ("device_enabled", "customer_enabled", "grant_enabled", "plan_enabled"))
                or row["customer_revoked_at"] is not None
                or row["customer_id"] != row["grant_customer"]):
            raise ClientStoreError("设备或线路未获授权")
        if supersede_id:
            old = db.execute("SELECT device_id,grant_id,revoked_at,expires_at,transport_id FROM leases WHERE id=?",
                             (supersede_id,)).fetchone()
            if (not old or old["device_id"] != device_id or old["grant_id"] != grant_id
                    or old["revoked_at"] is not None or old["expires_at"] <= now):
                raise ClientStoreError("租约不存在、已撤销或已过期")
            if transport_id is not None and transport_id != old["transport_id"]:
                raise ClientStoreError("续租不能更换接入入口")
            transport_id = old["transport_id"]
        self._require_rate_capabilities(row, relay_capabilities)
        usage = self._usage_in_db(db, row["customer_id"], row["period_seconds"], now)
        if row["quota_bytes"] is not None and usage["used_bytes"] >= row["quota_bytes"]:
            raise ClientStoreError("本账期流量额度已经用完")
        if row["grant_expires"] is not None:
            # A finite service grant is the authoritative lease deadline.
            # Do not shorten long subscriptions with an artificial cap.
            expires = int(row["grant_expires"])
            if ttl is not None:
                expires = min(expires, now + ttl)
        else:
            # An unbounded package stays unbounded and does not periodically
            # rotate its bearer token. Revocation is still checked on every
            # authorization request; relay offline_deadline remains separate.
            if ttl is not None:
                if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
                    raise ClientStoreError("租约时长无效")
                expires = now + ttl
            else:
                expires = LEASE_NEVER_EXPIRES_AT
        if expires <= now:
            raise ClientStoreError("线路授权已经过期")
        if supersede_id and row["access_mode"] == "wireguard":
            # A WireGuard lease ID is also the relay's peer identity. Rotate
            # only its bearer token and authorization expiry in place so the
            # relay never removes and re-adds the data-carrying peer.
            self._ensure_wireguard_candidate(db, device_id, grant_id, now)
            changed = db.execute("""UPDATE leases SET token_digest=?,expires_at=?
                WHERE id=? AND device_id=? AND grant_id=? AND revoked_at IS NULL""",
                                 (secret_digest(token, "lease"), expires,
                                  supersede_id, device_id, grant_id)).rowcount
            if changed != 1:
                raise ClientStoreError("租约已撤销或不再有效")
            self._ensure_active_wireguard_state(db, now)
            return token
        if row["access_mode"] == "wireguard":
            device_count = db.execute("SELECT COUNT(*) FROM devices WHERE customer_id=? AND enabled=1",
                                      (row["customer_id"],)).fetchone()[0]
            if device_count > 1:
                raise ClientStoreError("当前 WireGuard 产品不支持同一客户多设备共用地址")
            # Rotate/retry is serialized with the insertion.  A replacement
            # lease receives the same stable grant address and never invents a
            # second address for the same device.
            if not probe_v2:
                db.execute("UPDATE leases SET revoked_at=? WHERE device_id=? AND grant_id=? AND revoked_at IS NULL",
                           (now, device_id, grant_id))
            self._ensure_wireguard_candidate(db, device_id, grant_id, now)
        else:
            db.execute("UPDATE leases SET revoked_at=? WHERE device_id=? AND grant_id=? AND revoked_at IS NULL",
                       (now, device_id, grant_id))
        if row["access_mode"] == "public_proxy":
            db.execute("INSERT OR IGNORE INTO proxy_users(device_id,uuid,created_at) VALUES(?,?,?)",
                       (device_id, str(uuid.uuid4()), now))
        db.execute("INSERT INTO leases(id,token_digest,customer_id,device_id,grant_id,issued_at,expires_at,transport_id) VALUES(?,?,?,?,?,?,?,?)",
                   (lease_id, secret_digest(token, "lease"), row["customer_id"], device_id, grant_id, now, expires, transport_id))
        # The v2 peer identity is inserted into probe_leases in the same
        # transaction.  Validate only after that immutable binding exists.
        if not probe_v2:
            self._ensure_active_wireguard_state(db, now)
        return token

    def issue_lease(self, device_id: str, grant_id: str, *, ttl: int | None = None,
                    now: int | None = None,
                    relay_capabilities: dict[str, Any] | None = None,
                    transport_id: str | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("""SELECT 1 FROM probe_leases p JOIN leases l ON l.id=p.lease_id
                WHERE p.device_id=? AND p.probe_version=2 AND l.revoked_at IS NULL
                  AND l.expires_at>? LIMIT 1""", (device_id, now)).fetchone():
                raise ClientStoreError("检测租约仍在活动中，请先释放检测租约")
            token = self._issue_lease_in_db(db, device_id, grant_id, ttl=ttl, now=now,
                                            relay_capabilities=relay_capabilities,
                                            transport_id=transport_id)
        result = self.validate_lease(token, now=now)
        result["token"] = token
        return result

    @staticmethod
    def _probe_digest(probe_id: str) -> str:
        if not isinstance(probe_id, str) or not re.fullmatch(r'[0-9a-f]{64}', probe_id):
            raise ClientStoreError('检测编号无效')
        return secret_digest(probe_id, 'probe-lease')

    @staticmethod
    def _probe_v2_pool(db: sqlite3.Connection, relay_interface: str) -> tuple[list[str], list[str]]:
        """Validate a small, explicit per-relay pool before allocating /32s."""
        try:
            pools = json.loads(os.environ['SNA_PROBE_V2_POOLS'])
            relay_addresses = json.loads(os.environ['SNA_PROBE_V2_RELAY_ADDRESSES'])
            listeners = json.loads(os.environ.get('SNA_TUNNEL_PROBE_ADDRESSES', '{}'))
            if (not isinstance(pools, dict) or not isinstance(relay_addresses, dict)
                    or not isinstance(listeners, dict)):
                raise ValueError()
            raw_pool = pools[relay_interface]
            raw_relay_addresses = relay_addresses[relay_interface]
            if (not isinstance(raw_pool, str) or not isinstance(raw_relay_addresses, list)
                    or not raw_relay_addresses):
                raise ValueError()
            pool = ipaddress.IPv4Network(raw_pool, strict=True)
            customer = ipaddress.IPv4Network(
                os.environ['SNA_RELAY_CUSTOMER_SUBNET'], strict=True)
            if (not pool.is_private or not pool.subnet_of(customer)
                    or not 8 <= pool.num_addresses <= 4096):
                raise ValueError()
            relay_list = [str(ipaddress.IPv4Interface(item).ip)
                          for item in raw_relay_addresses]
            if (len(relay_list) < MAX_ACTIVE_V2_PROBES_PER_DEVICE
                    or len(relay_list) != len(set(relay_list))):
                raise ValueError()
            relay_ips = {ipaddress.IPv4Address(item) for item in relay_list}
            listener = listeners[relay_interface]
            if not isinstance(listener, str):
                raise ValueError()
            for items in relay_addresses.values():
                if not isinstance(items, list):
                    raise ValueError()
                relay_ips.update(ipaddress.IPv4Interface(item).ip for item in items)
            relay_ips.update(ipaddress.IPv4Address(item) for item in listeners.values())
            if os.environ.get('SNA_TUNNEL_PROBE_ADDRESS'):
                relay_ips.add(ipaddress.IPv4Address(os.environ['SNA_TUNNEL_PROBE_ADDRESS']))
            local_relay = os.environ['SNA_PROBE_V2_LOCAL_RELAY_ID']
            if not isinstance(local_relay, str) or not local_relay.strip():
                raise ValueError()
            if relay_interface == local_relay:
                local_binds = json.loads(os.environ['SNA_TUNNEL_PROBE_BIND_ADDRESSES'])
                if (not isinstance(local_binds, list) or
                        not set(relay_list) <= set(local_binds)):
                    raise ValueError()
            else:
                remote_ready = json.loads(os.environ['SNA_PROBE_V2_REMOTE_READY'])
                if (not isinstance(remote_ready, dict) or
                        remote_ready.get(relay_interface) != relay_list):
                    raise ValueError()
            if not all(address.is_private for address in relay_ips):
                raise ValueError()
            grant_addresses = set()
            for row in db.execute("""SELECT allocated_address FROM line_grants
                    WHERE access_mode='wireguard'"""):
                grant_addresses.add(ipaddress.IPv4Interface(row[0]).ip)
            if any(address in pool for address in relay_ips | grant_addresses):
                raise ValueError()
            if relay_ips & grant_addresses:
                raise ValueError()
        except (KeyError, TypeError, ValueError, ipaddress.AddressValueError):
            raise ProbeVersionUnsupported('并发检测地址池或中继地址配置无效') from None
        # Network and broadcast addresses stay unused, even for a /32 peer.
        candidates = [f'{address}/32' for address in pool.hosts()
                      if address not in relay_ips and address not in grant_addresses]
        if len(candidates) < MAX_ACTIVE_V2_PROBES_PER_DEVICE:
            raise ProbeVersionUnsupported('并发检测地址池可用容量不足')
        return candidates, relay_list

    @staticmethod
    def _probe_v2_echo_addresses(relay_interface: str) -> list[str]:
        try:
            mapping = json.loads(os.environ['SNA_PROBE_V2_ECHO_ADDRESSES'])
            if not isinstance(mapping, dict):
                raise ValueError()
            raw = mapping[relay_interface]
            if not isinstance(raw, list) or not 1 <= len(raw) <= 16:
                raise ValueError()
            addresses = []
            for value in raw:
                address = ipaddress.IPv4Address(value)
                if not isinstance(value, str) or not address.is_global or str(address) != value:
                    raise ValueError()
                addresses.append(value)
            if len(addresses) != len(set(addresses)):
                raise ValueError()
            return addresses
        except (KeyError, TypeError, ValueError):
            raise ProbeVersionUnsupported('并发检测公网回显地址配置无效') from None

    @classmethod
    def _allocate_probe_v2_address(cls, db: sqlite3.Connection,
                                   relay_interface: str, now: int) -> tuple[str, str]:
        candidates, challenge_addresses = cls._probe_v2_pool(db, relay_interface)
        occupied = {row[0] for row in db.execute("""SELECT p.allocated_address
            FROM probe_leases p JOIN leases l ON l.id=p.lease_id
            JOIN line_grants g ON g.id=l.grant_id
            WHERE p.probe_version=2 AND g.relay_interface=?
              AND l.revoked_at IS NULL AND l.expires_at>?""", (relay_interface, now))}
        occupied_challenges = {row[0] for row in db.execute("""SELECT p.probe_address
            FROM probe_leases p JOIN leases l ON l.id=p.lease_id
            JOIN line_grants g ON g.id=l.grant_id
            WHERE p.probe_version=2 AND g.relay_interface=?
              AND l.revoked_at IS NULL AND l.expires_at>?""", (relay_interface, now))}
        address = next((item for item in candidates if item not in occupied), None)
        challenge = next((item for item in challenge_addresses
                          if item not in occupied_challenges), None)
        if address is not None and challenge is not None:
            return address, challenge
        raise ProbeCapacityBusy('并发检测地址池已满')

    def issue_probe_lease(self, device_id: str, grant_id: str, probe_id: str, *,
                          transport_id: str | None = None, now: int | None = None,
                          relay_capabilities: dict[str, Any] | None = None,
                          probe_version: int = 1,
                          wireguard_public_key: str | None = None) -> dict[str, Any]:
        """Issue one short lease atomically with its durable idempotency record."""
        now = int(time.time()) if now is None else now
        digest = self._probe_digest(probe_id)
        if type(probe_version) is not int or probe_version not in (1, 2):
            raise ProbeVersionUnsupported('检测协议版本不支持')
        if probe_version == 2:
            if not self._valid_wireguard_key(wireguard_public_key):
                raise ClientStoreError('临时 WireGuard 公钥无效')
        elif wireguard_public_key is not None:
            raise ClientStoreError('旧版检测不接受临时 WireGuard 公钥')
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('''SELECT p.device_id,p.lease_id,p.probe_version,
                p.wireguard_public_key,l.grant_id,l.transport_id
                FROM probe_leases p JOIN leases l ON l.id=p.lease_id
                WHERE p.probe_digest=?''', (digest,)).fetchone()
            if previous:
                if previous['device_id'] != device_id:
                    raise ClientStoreError('检测编号不属于当前设备')
                if (previous['grant_id'] != grant_id or previous['transport_id'] != transport_id
                        or previous['probe_version'] != probe_version
                        or previous['wireguard_public_key'] != wireguard_public_key):
                    raise ClientStoreError('检测编号已经用于其他入口或出口')
            else:
                # V1 reuses the device peer, so it cannot coexist with a live
                # lease. V2 owns a separately allocated peer address and an
                # ephemeral public key; the same transaction verifies both
                # identities without touching the normal lease.
                active = db.execute('''SELECT id FROM leases WHERE device_id=?
                    AND revoked_at IS NULL AND expires_at>? LIMIT 1''',
                    (device_id, now)).fetchone()
                if active and probe_version == 1:
                    raise ClientStoreError('已有活动租约，暂不执行自动检测')
                if probe_version == 2:
                    active_v2 = db.execute('''SELECT l.grant_id,l.transport_id FROM probe_leases p
                        JOIN leases l ON l.id=p.lease_id WHERE p.device_id=?
                          AND p.probe_version=2 AND l.revoked_at IS NULL
                          AND l.expires_at>?''', (device_id, now)).fetchall()
                    if len(active_v2) >= MAX_ACTIVE_V2_PROBES_PER_DEVICE:
                        raise ProbeCapacityBusy('并发检测数量已达上限')
                    if any(row['grant_id'] == grant_id and
                           row['transport_id'] == transport_id for row in active_v2):
                        raise ProbeCapacityBusy('该线路和接入入口已有活动检测租约')
                    grant = db.execute('''SELECT relay_interface FROM line_grants
                        WHERE id=?''', (grant_id,)).fetchone()
                    if not grant:
                        raise ClientStoreError('线路授权不存在')
                    if db.execute('SELECT 1 FROM devices WHERE wireguard_public_key=? LIMIT 1',
                                  (wireguard_public_key,)).fetchone():
                        raise ClientStoreError('临时 WireGuard 公钥与设备公钥冲突')
                    if db.execute('''SELECT 1 FROM probe_leases p JOIN leases l ON l.id=p.lease_id
                        JOIN line_grants g ON g.id=l.grant_id
                        WHERE p.probe_version=2 AND p.wireguard_public_key=?
                          AND g.relay_interface=? AND l.revoked_at IS NULL
                          AND l.expires_at>? LIMIT 1''',
                        (wireguard_public_key, grant['relay_interface'], now)).fetchone():
                        raise ClientStoreError('临时 WireGuard 公钥正由其他检测占用')
                    address, challenge = self._allocate_probe_v2_address(
                        db, grant['relay_interface'], now)
                    echo_addresses = self._probe_v2_echo_addresses(grant['relay_interface'])
                else:
                    address = challenge = None
                    echo_addresses = None
                token = self._issue_lease_in_db(db, device_id, grant_id, ttl=120, now=now,
                                                relay_capabilities=relay_capabilities,
                                                transport_id=transport_id,
                                                probe_v2=probe_version == 2)
                lease_id = db.execute('SELECT id FROM leases WHERE token_digest=?',
                                      (secret_digest(token, 'lease'),)).fetchone()['id']
                db.execute('''INSERT INTO probe_leases(
                    probe_digest,device_id,lease_id,created_at,probe_version,
                    wireguard_public_key,allocated_address,probe_address,echo_addresses)
                    VALUES(?,?,?,?,?,?,?,?,?)''',
                    (digest, device_id, lease_id, now, probe_version,
                     wireguard_public_key, address, challenge,
                     json.dumps(echo_addresses, separators=(',', ':')) if echo_addresses else None))
                if probe_version == 2:
                    self._ensure_active_wireguard_state(db, now)
        return self.probe_lease(device_id, probe_id, now=now)

    def probe_lease(self, device_id: str, probe_id: str, *, now: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        digest = self._probe_digest(probe_id)
        with self._connect() as db:
            row = db.execute('''SELECT l.id,l.device_id,l.grant_id,l.transport_id,l.issued_at,
                l.expires_at,l.revoked_at,g.alias,g.tunnel,g.endpoint,g.relay_public_key,
                COALESCE(p.allocated_address,g.allocated_address) allocated_address,
                COALESCE(p.wireguard_public_key,d.wireguard_public_key) wireguard_public_key,
                p.probe_version,p.probe_address,p.echo_addresses,g.dns,g.allowed_ips,g.mtu,g.access_mode,g.relay_interface,
                g.egress_interface,g.egress_policy,g.enabled grant_enabled,
                d.enabled device_enabled,c.enabled customer_enabled,
                g.expires_at grant_expires,pn.enabled plan_enabled
                FROM probe_leases p JOIN leases l ON l.id=p.lease_id
                JOIN line_grants g ON g.id=l.grant_id JOIN devices d ON d.id=l.device_id
                JOIN customers c ON c.id=l.customer_id
                JOIN plans pn ON pn.id=c.plan_id
                WHERE p.probe_digest=? AND p.device_id=?''', (digest, device_id)).fetchone()
        if not row:
            return {'status': 'absent'}
        value = dict(row)
        state = ('released' if value['revoked_at'] is not None else
                 'expired' if value['expires_at'] <= now else
                 'unauthorized' if (not all(value[key] for key in
                    ('grant_enabled', 'device_enabled', 'customer_enabled', 'plan_enabled'))
                    or (value['grant_expires'] is not None and value['grant_expires'] <= now))
                 else 'active')
        for key in ('revoked_at', 'grant_enabled', 'device_enabled', 'customer_enabled',
                    'plan_enabled', 'grant_expires'):
            value.pop(key, None)
        if value['probe_version'] == 2:
            if not value['echo_addresses']:
                raise ClientStoreError('检测公网回显地址绑定缺失')
            value['probe_echo_addresses'] = json.loads(value['echo_addresses'])
        value.pop('echo_addresses', None)
        return {'status': state, 'lease': value if state == 'active' else
                {'id': value['id'], 'grant_id': value['grant_id'],
                 'transport_id': value['transport_id'], 'expires_at': value['expires_at'],
                 'probe_version': value['probe_version']}}

    def release_probe_lease(self, device_id: str, probe_id: str) -> dict[str, Any]:
        digest = self._probe_digest(probe_id)
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Revoke only the lease bound to this probe ID.  Repeated release
            # and lost replies cannot affect another active probe.
            db.execute('''UPDATE leases SET revoked_at=? WHERE id IN (
                SELECT lease_id FROM probe_leases WHERE probe_digest=? AND device_id=?)
                AND revoked_at IS NULL''', (now, digest, device_id))
        return self.probe_lease(device_id, probe_id)

    def renew_lease(self, device_id: str, lease_id: str, *, now: int | None = None,
                    relay_capabilities: dict[str, Any] | None = None,
                    current_token: str | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        stable_token = None
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT l.device_id,l.customer_id,l.grant_id,l.token_digest,l.expires_at,
                g.expires_at grant_expires,g.enabled grant_enabled,d.enabled device_enabled,
                c.enabled customer_enabled,c.revoked_at customer_revoked_at,
                p.enabled plan_enabled,p.quota_bytes,p.period_seconds
                FROM leases l JOIN line_grants g ON g.id=l.grant_id
                JOIN devices d ON d.id=l.device_id JOIN customers c ON c.id=l.customer_id
                JOIN plans p ON p.id=c.plan_id
                WHERE l.id=? AND l.revoked_at IS NULL""", (lease_id,)).fetchone()
            if not row or row["device_id"] != device_id:
                raise ClientStoreError("租约不存在或不属于当前设备")
            if db.execute("SELECT 1 FROM probe_leases WHERE lease_id=?",
                          (lease_id,)).fetchone():
                raise ClientStoreError("检测租约不能续租")
            if row["grant_expires"] is None:
                if (not isinstance(current_token, str) or not current_token or
                        not hmac.compare_digest(row["token_digest"],
                                                secret_digest(current_token, "lease"))):
                    raise ClientStoreError("无限期套餐续租必须携带当前令牌；不会轮换令牌")
                if (not row["grant_enabled"] or not row["device_enabled"] or
                        not row["customer_enabled"] or row["customer_revoked_at"] is not None or
                        not row["plan_enabled"]):
                    raise ClientStoreError("租约无效、已撤销或已过期")
                usage = self._usage_in_db(db, row["customer_id"], row["period_seconds"], now)
                if row["quota_bytes"] is not None and usage["used_bytes"] >= row["quota_bytes"]:
                    raise ClientStoreError("本账期流量额度已经用完")
                # Older clients may still hold the pre-migration short expiry.
                # Extend only the lease deadline in place, retaining its token,
                # lease identity, WireGuard peer and counters.
                if row["expires_at"] != LEASE_NEVER_EXPIRES_AT:
                    db.execute("UPDATE leases SET expires_at=? WHERE id=? AND revoked_at IS NULL",
                               (LEASE_NEVER_EXPIRES_AT, lease_id))
                stable_token = current_token
            else:
                token = self._issue_lease_in_db(db, device_id, row["grant_id"], ttl=None, now=now,
                                                relay_capabilities=relay_capabilities,
                                                supersede_id=lease_id)
        if stable_token is not None:
            result = self.validate_lease(stable_token, now=now)
            result["token"] = stable_token
            return result
        result = self.validate_lease(token, now=now)
        result["token"] = token
        return result

    def release_lease(self, device_id: str, lease_id: str, *, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT l.device_id,l.revoked_at,p.lease_id probe_lease_id
                FROM leases l LEFT JOIN probe_leases p ON p.lease_id=l.id
                WHERE l.id=?""", (lease_id,)).fetchone()
            if not row or row["device_id"] != device_id:
                raise ClientStoreError("租约不存在或不属于当前设备")
            if row["probe_lease_id"] is not None:
                raise ClientStoreError("检测租约必须使用检测编号释放")
            if row["revoked_at"] is None:
                db.execute("UPDATE leases SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                           (now, lease_id))

    def validate_lease(self, token: str, *, now: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        with self._connect() as db:
            row = db.execute("""SELECT l.*,g.alias,g.tunnel,g.endpoint,g.enabled grant_enabled,g.expires_at grant_expires,
              d.enabled device_enabled,d.public_key device_signing_public_key,
              c.revoked_at customer_revoked_at,
              d.wireguard_public_key,c.enabled customer_enabled,p.enabled plan_enabled,
              p.download_bps,p.upload_bps,p.quota_bytes,p.period_seconds,
              g.relay_public_key,g.allocated_address,g.dns,g.allowed_ips,g.mtu,g.relay_interface,g.egress_interface,
              g.access_mode,g.proxy_config,g.egress_policy,pu.uuid proxy_uuid
              FROM leases l JOIN line_grants g ON g.id=l.grant_id JOIN devices d ON d.id=l.device_id
              JOIN customers c ON c.id=l.customer_id JOIN plans p ON p.id=c.plan_id
              LEFT JOIN proxy_users pu ON pu.device_id=d.id WHERE l.token_digest=?""",
              (secret_digest(token, "lease"),)).fetchone()
            if (not row or row["revoked_at"] is not None or
                    (row["expires_at"] != LEASE_NEVER_EXPIRES_AT and row["expires_at"] <= now) or
                    not row["grant_enabled"] or not row["device_enabled"] or
                    not row["customer_enabled"] or not row["plan_enabled"] or
                    row["customer_revoked_at"] is not None or
                    (row["grant_expires"] is not None and row["grant_expires"] <= now)):
                raise ClientStoreError("租约无效、已撤销或已过期")
            if row["access_mode"] == "wireguard":
                self._ensure_active_wireguard_state(db, now)
            used = self._usage_in_db(db, row["customer_id"], row["period_seconds"], now)
            if row["quota_bytes"] is not None and used["used_bytes"] >= row["quota_bytes"]:
                raise ClientStoreError("本账期流量额度已经用完")
        value = dict(row)
        value.update(used)
        if value["access_mode"] == "public_proxy":
            value["proxy_path_token"] = _cloud_path_token(value["token_digest"], value["id"])
        value.pop("token_digest", None)
        return value

    @staticmethod
    def _usage_in_db(db: sqlite3.Connection, customer_id: str, period_seconds: int, now: int) -> dict[str, int]:
        anchor_row = db.execute('SELECT anchor FROM billing_anchors WHERE customer_id=?', (customer_id,)).fetchone()
        anchor = anchor_row['anchor'] if anchor_row else 0
        start = anchor + ((now - anchor) // period_seconds) * period_seconds
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
            db.execute("""INSERT INTO usage_periods(
                customer_id,period_start,period_end,rx_bytes,tx_bytes) VALUES(?,?,?,?,?)
                ON CONFLICT(customer_id,period_start) DO UPDATE SET
                rx_bytes=rx_bytes+excluded.rx_bytes,tx_bytes=tx_bytes+excluded.tx_bytes""",
                       (lease["customer_id"], usage["period_start"], usage["period_end"], delta_rx, delta_tx))
            db.execute("UPDATE leases SET last_rx=?,last_tx=? WHERE id=?", (rx_total, tx_total, lease["id"]))
            db.execute("""INSERT INTO usage_reports(
                lease_id,report_id,rx_total,tx_total,created_at) VALUES(?,?,?,?,?)""",
                       (lease["id"], report_id, rx_total, tx_total, now))
            result = self._usage_in_db(db, lease["customer_id"], lease["period_seconds"], now)
            if lease["quota_bytes"] is not None and result["used_bytes"] >= lease["quota_bytes"]:
                # A final sample belongs to this lease.  Do not revoke every
                # lease of the customer as a side effect of one counter.
                db.execute("UPDATE leases SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                           (now, lease["id"]))
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
            db.execute("""INSERT INTO usage_periods(
                customer_id,period_start,period_end,rx_bytes,tx_bytes) VALUES(?,?,?,?,?)
                ON CONFLICT(customer_id,period_start) DO UPDATE SET
                rx_bytes=rx_bytes+excluded.rx_bytes,tx_bytes=tx_bytes+excluded.tx_bytes""",
                       (lease["customer_id"], usage["period_start"], usage["period_end"], delta_rx, delta_tx))
            db.execute("UPDATE leases SET last_rx=?,last_tx=? WHERE id=?", (rx_total, tx_total, lease_id))
            db.execute("""INSERT INTO usage_reports(
                lease_id,report_id,rx_total,tx_total,created_at) VALUES(?,?,?,?,?)""",
                       (lease_id, report_id, rx_total, tx_total, now))
            result = self._usage_in_db(db, lease["customer_id"], lease["period_seconds"], now)
            if lease["quota_bytes"] is not None and result["used_bytes"] >= lease["quota_bytes"]:
                db.execute("UPDATE leases SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                           (now, lease_id))
            return result

    def revoke(self, kind: str, object_id: str, *, now: int | None = None,
               audit_callback: Callable[[sqlite3.Connection], None] | None = None) -> None:
        now = int(time.time()) if now is None else now
        mapping = {"customer": ("customers", "id"), "device": ("devices", "id"),
                   "grant": ("line_grants", "id"), "lease": ("leases", "id")}
        if kind not in mapping:
            raise ClientStoreError("撤销对象类型无效")
        table, column = mapping[kind]
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if kind == "lease":
                cursor = db.execute(f"UPDATE {table} SET revoked_at=? WHERE {column}=?", (now, object_id))
            elif kind == "customer":
                cursor = db.execute("UPDATE customers SET enabled=0,revoked_at=?,auth_epoch=auth_epoch+1 WHERE id=?",
                                    (now, object_id))
            else:
                cursor = db.execute(f"UPDATE {table} SET enabled=0,revoked_at=? WHERE {column}=?", (now, object_id)) if kind != "grant" else db.execute("UPDATE line_grants SET enabled=0 WHERE id=?", (object_id,))
                if kind == "device":
                    db.execute("UPDATE leases SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL", (now, object_id))
                elif kind == "grant":
                    db.execute("UPDATE leases SET revoked_at=? WHERE grant_id=? AND revoked_at IS NULL", (now, object_id))
            if kind == "customer":
                db.execute("UPDATE devices SET enabled=0,revoked_at=COALESCE(revoked_at,?) WHERE customer_id=?",
                           (now, object_id))
                db.execute("UPDATE leases SET revoked_at=? WHERE customer_id=? AND revoked_at IS NULL", (now, object_id))
                db.execute("UPDATE enrollment_tokens SET revoked_at=? WHERE customer_id=? AND used_at IS NULL AND revoked_at IS NULL",
                           (now, object_id))
            if cursor.rowcount == 0:
                raise ClientStoreError("撤销对象不存在")
            if audit_callback is not None:
                audit_callback(db)

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
            self._ensure_active_wireguard_state(db, now)
            rows = db.execute("""SELECT l.id,l.token_digest,l.customer_id,l.device_id,l.grant_id,l.issued_at,l.expires_at,l.last_rx,l.last_tx,
              COALESCE(pr.wireguard_public_key,d.wireguard_public_key) wireguard_public_key,
              g.alias,g.endpoint,g.relay_public_key,
              COALESCE(pr.allocated_address,g.allocated_address) allocated_address,
              pr.probe_address,pr.probe_version,pr.echo_addresses,g.dns,
              g.allowed_ips,g.mtu,g.relay_interface,g.egress_interface,g.access_mode,g.proxy_config,g.egress_policy,
              pu.uuid proxy_uuid,p.download_bps,p.upload_bps,
              p.quota_bytes,p.period_seconds
              FROM leases l JOIN devices d ON d.id=l.device_id JOIN customers c ON c.id=l.customer_id
              JOIN plans p ON p.id=c.plan_id JOIN line_grants g ON g.id=l.grant_id
              LEFT JOIN probe_leases pr ON pr.lease_id=l.id
              LEFT JOIN proxy_users pu ON pu.device_id=d.id
              WHERE l.revoked_at IS NULL AND l.expires_at>? AND d.enabled=1 AND c.enabled=1
              AND c.revoked_at IS NULL AND p.enabled=1 AND g.enabled=1
              AND (g.expires_at IS NULL OR g.expires_at>?) ORDER BY l.issued_at""",
              (now, now)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                if item['probe_version'] == 2:
                    if not item['echo_addresses']:
                        raise ClientStoreError('检测公网回显地址绑定缺失')
                    item['probe_echo_addresses'] = json.loads(item['echo_addresses'])
                item.pop('echo_addresses', None)
                usage = self._usage_in_db(db, item["customer_id"], item["period_seconds"], now)
                if item["quota_bytes"] is not None and usage["used_bytes"] >= item["quota_bytes"]:
                    continue
                if item["access_mode"] == "public_proxy":
                    item["proxy_path_token"] = _cloud_path_token(item["token_digest"], item["id"])
                item.pop("token_digest", None)
                item["remaining_bytes"] = (None if item["quota_bytes"] is None else
                                           max(0, item["quota_bytes"] - usage["used_bytes"]))
                result.append(item)
        return result

    def active_proxy_users(self, *, now: int | None = None) -> list[dict[str, Any]]:
        """Return only active public-proxy identities for the cloud reconciler."""
        users = []
        for lease in self.active_leases(now=now):
            if lease.get("access_mode") != "public_proxy" or not lease.get("proxy_uuid"):
                continue
            config = json.loads(lease.get("proxy_config") or "{}")
            users.append({"lease_id": lease["id"], "device_id": lease["device_id"],
                          "uuid": lease["proxy_uuid"], "expires_at": lease["expires_at"],
                          "path_token": lease["proxy_path_token"],
                          "last_rx": lease["last_rx"], "last_tx": lease["last_tx"],
                          "remaining_bytes": lease["remaining_bytes"],
                          "download_bps": lease["download_bps"],
                          "upload_bps": lease["upload_bps"],
                          "capabilities": {"proxy_modes": ["rule", "global"]},
                          "config": config})
        return users

    def prepare_relay_readiness(self, relay_interface: str,
                                digests: dict[str, str]) -> None:
        """Record the exact desired peer policy before returning a snapshot."""
        if not relay_interface or any(
                not isinstance(lease_id, str) or
                not isinstance(digest, str) or
                not re.fullmatch(r'[0-9a-f]{64}', digest)
                for lease_id, digest in digests.items()):
            raise ClientStoreError('中继就绪策略摘要无效')
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for lease_id, digest in digests.items():
                db.execute("""INSERT INTO relay_readiness
                    (lease_id,relay_interface,policy_digest,ready_at)
                    VALUES(?,?,?,NULL)
                    ON CONFLICT(lease_id) DO UPDATE SET
                      relay_interface=excluded.relay_interface,
                      policy_digest=excluded.policy_digest,
                      ready_at=CASE
                        WHEN relay_readiness.relay_interface=excluded.relay_interface
                         AND relay_readiness.policy_digest=excluded.policy_digest
                        THEN relay_readiness.ready_at ELSE NULL END""",
                           (lease_id, relay_interface, digest))
            db.commit()

    def acknowledge_relay_readiness(self, relay_interface: str,
                                    digests: dict[str, str], *,
                                    now: int | None = None) -> None:
        """Accept only current, matching policy from the owning relay."""
        now = int(time.time()) if now is None else now
        if not relay_interface or not isinstance(digests, dict) or len(digests) > 64:
            raise ClientStoreError('中继就绪确认无效')
        with self._lock, self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for lease_id, digest in digests.items():
                if (not isinstance(lease_id, str) or not isinstance(digest, str) or
                        not re.fullmatch(r'[0-9a-f]{64}', digest)):
                    raise ClientStoreError('中继就绪确认无效')
                row = db.execute("""SELECT r.policy_digest,l.revoked_at,l.expires_at
                    FROM relay_readiness r JOIN leases l ON l.id=r.lease_id
                    WHERE r.lease_id=? AND r.relay_interface=?""",
                                 (lease_id, relay_interface)).fetchone()
                if (not row or row['policy_digest'] != digest or
                        row['revoked_at'] is not None or row['expires_at'] <= now):
                    raise ClientStoreError('中继确认与当前租约不一致')
            for lease_id in digests:
                db.execute("""UPDATE relay_readiness SET ready_at=?
                    WHERE lease_id=? AND relay_interface=?""",
                           (now, lease_id, relay_interface))
            db.commit()

    def relay_readiness(self, lease_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("""SELECT r.ready_at,l.revoked_at,l.expires_at
                FROM relay_readiness r JOIN leases l ON l.id=r.lease_id
                WHERE r.lease_id=?""", (lease_id,)).fetchone()
        now = int(time.time())
        return {'ready': bool(row and row['ready_at'] is not None and
                              row['revoked_at'] is None and row['expires_at'] > now),
                'ready_at': row['ready_at'] if row else None}

    def lease_reconciliation(self, *, since: int = 0, now: int | None = None) -> dict[str, Any]:
        """Desired active peers plus leases explicitly revoked since a relay checkpoint."""
        now = int(time.time()) if now is None else now
        with self._connect() as db:
            revoked = db.execute("SELECT id,device_id,grant_id,revoked_at FROM leases WHERE revoked_at IS NOT NULL AND revoked_at>=? ORDER BY revoked_at", (since,)).fetchall()
        return {"generated_at": now, "active": self.active_leases(now=now),
                "revoked": [dict(row) for row in revoked]}

