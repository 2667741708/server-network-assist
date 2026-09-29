"""Logical node directory and device enrollment control plane.

The customer client deliberately keeps authorization (a subscription) separate
from the current address of a node.  This module owns the small amount of
state needed to publish that address directory and to register the device key
which is allowed to read a customer's subscription.

The implementation uses the existing :class:`ClientStore` SQLite connection so
the migration is safe for an already deployed 0.9 database.  It does not
require a network service or a database server; tests and small installations
can use a temporary SQLite file.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .client_crypto import b64url_decode, b64url_encode
from .client_store import ClientStore, ClientStoreError


SCHEMA_VERSION = 1
MAX_DIRECTORY_TTL = 31 * 86400
MAX_NODES = 256
MAX_TRANSPORTS = 16
MAX_ENDPOINTS = 16
MAX_EGRESSES = 16
MAX_FALLBACKS = 16
MAX_METADATA_BYTES = 8192
MAX_DIRECTORY_BYTES = 512 * 1024
MAX_SAFE_INTEGER = 9_007_199_254_740_991
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
TUNNEL_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
TRANSPORT_KINDS = {"wireguard", "wss"}
ACCESS_KINDS = {"campus", "campus_lan", "public", "hub", "overlay"}
_PROCESS_LOCKS: dict[str, Any] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class ControlPlaneError(ClientStoreError):
    """A safe, user-facing control-plane validation or state error."""


class DirectoryValidationError(ControlPlaneError):
    """A node directory cannot be accepted by the client protocol."""


class DirectoryRollbackError(ControlPlaneError):
    """A directory revision is lower than, or conflicts with, the fence."""


class EnrollmentError(ControlPlaneError):
    """A one-time enrollment or device credential operation failed."""


def _process_lock_for(path: Path):
    key = os.path.normcase(os.path.abspath(str(path)))
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


@contextmanager
def _cross_process_lock(path: Path):
    """Serialize file/key publication across threads and worker processes."""
    process_lock = _process_lock_for(path)
    with process_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def _safe_directory_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ControlPlaneError("节点目录地址必须使用 HTTPS")
    text = value.strip()
    try:
        parts = urlsplit(text)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise ControlPlaneError("节点目录地址必须使用有效 HTTPS 地址") from None
    if (parts.scheme != "https" or not parts.netloc or not hostname or
            parts.username or parts.password or parts.query or parts.fragment):
        raise ControlPlaneError("节点目录地址必须使用不含凭据和查询的 HTTPS 地址")
    if port is not None and not 1 <= port <= 65535:
        raise ControlPlaneError("节点目录端口无效")
    return text.rstrip("/")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _canonical_json(value: Any) -> bytes:
    """Encode the exact strict JSON subset shared with the Windows client."""
    _validate_json_value(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if _is_int(value):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise DirectoryValidationError("签名 JSON 整数超出跨语言安全范围")
        return
    if isinstance(value, float):
        raise DirectoryValidationError("签名 JSON 不接受浮点数")
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DirectoryValidationError("签名 JSON 对象键必须是字符串")
            _validate_json_value(item)
        return
    raise DirectoryValidationError("签名 JSON 包含不支持的值类型")


def strict_json_loads(value: str | bytes) -> Any:
    """Parse JSON while rejecting duplicate object keys and non-finite values."""
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise DirectoryValidationError("签名 JSON 包含重复对象键")
            result[key] = item
        return result

    def constant(_value: str) -> None:
        raise DirectoryValidationError("签名 JSON 不接受非有限数")

    return json.loads(value, object_pairs_hook=object_pairs, parse_constant=constant)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise DirectoryValidationError(f"{label}无效")
    return value


def _priority(value: Any, label: str) -> int:
    if not _is_int(value) or not 0 <= value <= 10000:
        raise DirectoryValidationError(f"{label}优先级无效")
    return value


def _safe_host_port(value: Any) -> str:
    if not isinstance(value, str):
        raise DirectoryValidationError("WireGuard 接入地址无效")
    text = value.strip()
    if text.startswith("["):
        end = text.find("]")
        if end < 0 or end + 2 > len(text) or text[end + 1] != ":":
            raise DirectoryValidationError("WireGuard 接入地址无效")
        host, port_text = text[1:end], text[end + 2:]
    else:
        host, separator, port_text = text.rpartition(":")
        if not separator or ":" in host:
            raise DirectoryValidationError("WireGuard 接入地址无效")
    if not host or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise DirectoryValidationError("WireGuard 接入地址无效")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not HOSTNAME.fullmatch(host) or host.endswith("."):
            raise DirectoryValidationError("WireGuard 接入地址无效") from None
        return f"{host}:{int(port_text)}"
    host_text = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"{host_text}:{int(port_text)}"


def _safe_wss_endpoint(value: Any) -> str:
    if not isinstance(value, str):
        raise DirectoryValidationError("WSS 接入地址无效")
    text = value.strip()
    try:
        parts = urlsplit(text)
        hostname, port = parts.hostname, parts.port
    except ValueError:
        raise DirectoryValidationError("WSS 接入地址无效") from None
    if (parts.scheme != "wss" or not parts.netloc or not hostname or
            parts.username or parts.password or parts.query or parts.fragment):
        raise DirectoryValidationError("WSS 接入地址无效")
    if port is not None and not 1 <= port <= 65535:
        raise DirectoryValidationError("WSS 接入地址无效")
    return text


def _safe_endpoint(kind: str, value: Any) -> str:
    return _safe_host_port(value) if kind == "wireguard" else _safe_wss_endpoint(value)


def _ed25519_public(value: object) -> str:
    if not isinstance(value, str):
        raise EnrollmentError("设备签名公钥无效")
    try:
        raw = b64url_decode(value)
        if len(raw) != 32:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(raw)
    except Exception:
        raise EnrollmentError("设备签名公钥无效") from None
    return value


def _wireguard_public(value: object) -> str:
    if not isinstance(value, str):
        raise EnrollmentError("WireGuard 公钥无效")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        raise EnrollmentError("WireGuard 公钥无效") from None
    if len(raw) != 32:
        raise EnrollmentError("WireGuard 公钥无效")
    return value


def _key_from_private(value: str | bytes | Ed25519PrivateKey) -> Ed25519PrivateKey:
    if isinstance(value, Ed25519PrivateKey):
        return value
    try:
        raw = value if isinstance(value, bytes) else b64url_decode(value)
        return Ed25519PrivateKey.from_private_bytes(raw)
    except Exception:
        raise ControlPlaneError("目录签名私钥无效") from None


def _key_from_public(value: str | bytes | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    try:
        raw = value if isinstance(value, bytes) else b64url_decode(value)
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception:
        raise ControlPlaneError("目录签名公钥无效") from None


def _redact_metadata(value: object) -> dict[str, Any]:
    """Keep audit metadata structured while excluding bearer/key material."""
    if not isinstance(value, dict):
        return {}
    blocked = {
        "token", "enrollment_token", "secret", "password", "private_key",
        "privatekey", "signing_private_key", "signingprivatekey",
        "wireguard_private_key", "wireguardprivatekey", "authorization",
        "signature", "credential", "raw", "access_token", "refresh_token",
        "api_key", "apikey", "bearer_token", "client_secret", "secret_key",
    }

    def clean(item: object, depth: int = 0) -> object:
        if depth > 3:
            return "<omitted>"
        if isinstance(item, dict):
            cleaned = {}
            for key, val in item.items():
                key_text = str(key)
                key_lower = key_text.lower().replace("-", "_")
                if (key_lower in blocked or
                        key_lower.endswith(("_token", "_secret", "_password"))):
                    continue
                cleaned[key_text] = clean(val, depth + 1)
            return cleaned
        if isinstance(item, list):
            return [clean(val, depth + 1) for val in item[:32]]
        if isinstance(item, (str, int, bool)) or item is None:
            return item if not isinstance(item, str) else item[:512]
        return str(item)[:128]

    cleaned = clean(value)
    return cleaned if isinstance(cleaned, dict) else {}


def _node_value(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    value = dict(row)
    for key in ("fallback_node_ids", "metadata"):
        try:
            value[key] = json.loads(value[key])
        except (TypeError, ValueError):
            value[key] = [] if key == "fallback_node_ids" else {}
    value["enabled"] = bool(value.get("enabled", 1))
    return value


class ControlPlaneService:
    """SQLite-backed logical-node and device control-plane service.

    ``store`` may be an existing ``ClientStore`` or a SQLite path.  The former
    is used by the web application, while the latter keeps unit tests and
    small deployments independent from the legacy panel state.
    """

    def __init__(self, store: ClientStore | str | Path, *, private_key: str | bytes | Ed25519PrivateKey | None = None,
                 key_file: str | Path | None = None, directory_url: str | None = None,
                 directory_ttl: int = 86400):
        if isinstance(store, ClientStore):
            self.store = store
        else:
            store_path = Path(store)
            if store_path.exists() and store_path.is_dir():
                store_path = store_path / "control-plane.sqlite3"
            self.store = ClientStore(store_path)
        self._lock = self.store._lock
        self.directory_url = (_safe_directory_url(directory_url)
                              if directory_url is not None and str(directory_url).strip() else None)
        if not 60 <= directory_ttl <= MAX_DIRECTORY_TTL:
            raise ControlPlaneError("节点目录默认有效期无效")
        self.directory_ttl = int(directory_ttl)
        self._initialize()
        self.publisher = DirectoryPublisher(self, private_key=private_key, key_file=key_file,
                                            default_ttl=self.directory_ttl)

    @contextmanager
    def _db(self):
        with self._lock, self.store._connect() as db:
            yield db

    def _initialize(self) -> None:
        with self._db() as db:
            # ClientStore serializes its own migration, but control-plane
            # tables are initialized here as well.  Hold the same SQLite
            # writer lock across every DDL statement and the seed row so two
            # workers cannot observe a half-created control schema.
            db.execute("BEGIN IMMEDIATE")
            schema_statements = [
                """
            CREATE TABLE IF NOT EXISTS control_nodes(
              node_id TEXT PRIMARY KEY, name TEXT NOT NULL, priority INTEGER NOT NULL,
              fallback_node_ids TEXT NOT NULL, metadata TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL);
                """,
                """
            CREATE TABLE IF NOT EXISTS control_transports(
              node_id TEXT NOT NULL REFERENCES control_nodes(node_id) ON DELETE CASCADE,
              transport_id TEXT NOT NULL, kind TEXT NOT NULL, access TEXT NOT NULL,
              priority INTEGER NOT NULL, tunnel TEXT, enabled INTEGER NOT NULL DEFAULT 1,
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
              PRIMARY KEY(node_id, transport_id));
                """,
                """
            CREATE TABLE IF NOT EXISTS control_endpoints(
              node_id TEXT NOT NULL, transport_id TEXT NOT NULL,
              endpoint_id TEXT NOT NULL, address TEXT NOT NULL, priority INTEGER NOT NULL,
              expires_at INTEGER, enabled INTEGER NOT NULL DEFAULT 1,
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
              PRIMARY KEY(node_id, transport_id, endpoint_id),
              FOREIGN KEY(node_id, transport_id) REFERENCES control_transports(node_id, transport_id)
                ON DELETE CASCADE);
                """,
                """
            CREATE TABLE IF NOT EXISTS control_egresses(
              node_id TEXT NOT NULL REFERENCES control_nodes(node_id) ON DELETE CASCADE,
              egress_id TEXT NOT NULL, kind TEXT NOT NULL, priority INTEGER NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL, PRIMARY KEY(node_id, egress_id));
                """,
                """
            CREATE TABLE IF NOT EXISTS control_grant_refs(
              grant_id TEXT PRIMARY KEY, node_id TEXT NOT NULL,
              egress_ids TEXT NOT NULL, transport_id TEXT, priority INTEGER NOT NULL,
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
              FOREIGN KEY(node_id) REFERENCES control_nodes(node_id) ON DELETE RESTRICT);
                """,
                """
            CREATE TABLE IF NOT EXISTS control_directory_state(
              id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
              issued_at INTEGER, expires_at INTEGER, envelope TEXT,
              envelope_digest TEXT);
                """,
                """
            CREATE TABLE IF NOT EXISTS control_audit_events(
              event_id TEXT PRIMARY KEY, action TEXT NOT NULL,
              subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
              occurred_at INTEGER NOT NULL, metadata TEXT NOT NULL);
                """,
            ]
            for statement in schema_statements:
                db.execute(statement)
            db.execute("INSERT OR IGNORE INTO control_directory_state(id,revision) VALUES(1,0)")
            guarded_tables = (
                "control_nodes", "control_transports", "control_endpoints",
                "control_egresses", "control_grant_refs", "control_directory_state",
                "control_audit_events",
            )
            for table in guarded_tables:
                for event in ("INSERT", "UPDATE", "DELETE"):
                    trigger = f"sna_control_schema_guard_{table}_{event.lower()}"
                    db.execute(f"""CREATE TRIGGER IF NOT EXISTS {trigger}
                        BEFORE {event} ON {table}
                        WHEN sna_schema_version() != (SELECT schema_version FROM sna_schema_meta WHERE id=1)
                        BEGIN SELECT RAISE(ABORT, '数据库 schema 版本不兼容'); END""")
            db.commit()

    @staticmethod
    def _now(now: int | None) -> int:
        current = int(time.time()) if now is None else now
        if not _is_int(current) or current < 0:
            raise ControlPlaneError("时间戳无效")
        return current

    def _bump_revision(self, db: sqlite3.Connection) -> int:
        row = db.execute("SELECT revision FROM control_directory_state WHERE id=1").fetchone()
        revision = int(row[0] if row else 0) + 1
        db.execute("""INSERT INTO control_directory_state(id,revision) VALUES(1,?)
            ON CONFLICT(id) DO UPDATE SET revision=excluded.revision""", (revision,))
        return revision

    def _audit_in_db(self, db: sqlite3.Connection, action: str, subject_type: str = "control_plane",
                     subject_id: str = "", metadata: dict[str, Any] | None = None,
                     *, now: int | None = None) -> dict[str, Any]:
        action = str(action).strip()
        subject_type = str(subject_type).strip()
        subject_id = str(subject_id).strip()
        if not action or len(action) > 120 or not subject_type or len(subject_type) > 80 or len(subject_id) > 200:
            raise ControlPlaneError("审计事件标识无效")
        occurred_at = self._now(now)
        subject_lower = subject_id.lower()
        if any(marker in subject_lower for marker in
               ("token", "secret", "password", "authorization", "bearer", "private_key")):
            subject_id = "<redacted>"
        event = {"event_id": "evt_" + uuid.uuid4().hex, "action": action,
                 "subject_type": subject_type, "subject_id": subject_id,
                 "occurred_at": occurred_at, "metadata": _redact_metadata(metadata or {})}
        db.execute("""INSERT INTO control_audit_events(
            event_id,action,subject_type,subject_id,occurred_at,metadata)
            VALUES(?,?,?,?,?,?)""",
                   (event["event_id"], action, subject_type, subject_id, occurred_at,
                    json.dumps(event["metadata"], ensure_ascii=False, separators=(",", ":"))))
        return event

    def audit_event(self, action: str, subject_type: str = "control_plane", subject_id: str = "",
                    metadata: dict[str, Any] | None = None, *, now: int | None = None) -> dict[str, Any]:
        with self._db() as db:
            return self._audit_in_db(db, action, subject_type, subject_id, metadata, now=now)

    def list_audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        if not _is_int(limit) or not 1 <= limit <= 1000:
            raise ControlPlaneError("审计事件数量无效")
        with self.store._connect() as db:
            rows = db.execute("SELECT * FROM control_audit_events ORDER BY occurred_at DESC,event_id DESC LIMIT ?",
                              (limit,)).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["metadata"] = strict_json_loads(value["metadata"])
            result.append(value)
        return result

    def current_revision(self) -> int:
        with self.store._connect() as db:
            row = db.execute("SELECT revision FROM control_directory_state WHERE id=1").fetchone()
        return int(row[0] if row else 0)

    def publish_directory(self, **kwargs: Any) -> dict[str, Any]:
        """Publish the current signed directory (protocol-oriented alias)."""
        return self.publisher.publish(**kwargs)

    def directory_envelope(self, *, now: int | None = None) -> dict[str, Any]:
        return self.publisher.ensure_published(now=now)

    def _node_exists(self, db: sqlite3.Connection, node_id: str) -> bool:
        return db.execute("SELECT 1 FROM control_nodes WHERE node_id=?", (node_id,)).fetchone() is not None

    @staticmethod
    def _node_fields(value: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = {**(existing or {}), **value}
        node_id = _identifier(merged.get("node_id", merged.get("id")), "逻辑节点编号")
        name = merged.get("name", node_id)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise DirectoryValidationError("逻辑节点名称无效")
        priority = _priority(merged.get("priority", 100), "逻辑节点")
        fallbacks = merged.get("fallback_node_ids", merged.get("fallbacks", []))
        if isinstance(fallbacks, str):
            fallbacks = [fallbacks]
        if not isinstance(fallbacks, list) or len(fallbacks) > MAX_FALLBACKS:
            raise DirectoryValidationError("逻辑节点备用节点列表无效")
        fallbacks = [_identifier(item, "备用逻辑节点编号") for item in fallbacks]
        if len(set(fallbacks)) != len(fallbacks) or node_id in fallbacks:
            raise DirectoryValidationError("逻辑节点备用节点列表重复或自引用")
        metadata = merged.get("metadata", {})
        if not isinstance(metadata, dict) or len(_canonical_json(metadata)) > MAX_METADATA_BYTES:
            raise DirectoryValidationError("逻辑节点元数据无效或过大")
        enabled = merged.get("enabled", True)
        if type(enabled) is not bool:
            raise DirectoryValidationError("逻辑节点启用状态无效")
        return {"node_id": node_id, "name": name.strip(), "priority": priority,
                "fallback_node_ids": fallbacks, "metadata": metadata, "enabled": enabled}

    def create_node(self, node_id: str | dict[str, Any], name: str | None = None, *, priority: int = 100,
                    fallback_node_ids: list[str] | None = None, metadata: dict[str, Any] | None = None,
                    enabled: bool = True, transports: list[dict[str, Any]] | None = None,
                    egress: list[dict[str, Any] | str] | None = None, now: int | None = None) -> dict[str, Any]:
        value = dict(node_id) if isinstance(node_id, dict) else {
            "node_id": node_id, "name": node_id if name is None else name, "priority": priority,
            "fallback_node_ids": [] if fallback_node_ids is None else fallback_node_ids,
            "metadata": {} if metadata is None else metadata, "enabled": enabled,
        }
        if transports is None:
            transports = value.get("transports")
        if egress is None:
            egress = value.get("egress", value.get("egresses"))
        fields = self._node_fields(value)
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if any(not self._node_exists(db, fallback) for fallback in fields["fallback_node_ids"]):
                raise DirectoryValidationError("逻辑节点包含未知备用节点")
            try:
                db.execute("""INSERT INTO control_nodes(
                    node_id,name,priority,fallback_node_ids,metadata,enabled,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                           (fields["node_id"], fields["name"], fields["priority"],
                            json.dumps(fields["fallback_node_ids"], ensure_ascii=False, separators=(",", ":")),
                            json.dumps(fields["metadata"], ensure_ascii=False, separators=(",", ":")),
                            int(fields["enabled"]), current, current))
            except sqlite3.IntegrityError:
                raise ControlPlaneError("逻辑节点已存在") from None
            revision = self._bump_revision(db)
            self._audit_in_db(db, "node_created", "node", fields["node_id"], {"revision": revision}, now=current)
            for transport in transports or []:
                self._create_transport_in_db(db, fields["node_id"], transport, now=current)
            for item in egress or []:
                self._create_egress_in_db(db, fields["node_id"], item, now=current)
        return self.get_node(fields["node_id"])

    def update_node(self, node_id: str, value: dict[str, Any] | None = None, *, now: int | None = None,
                    **changes: Any) -> dict[str, Any]:
        node_id = _identifier(node_id, "逻辑节点编号")
        patch = {**(value or {}), **changes, "node_id": node_id}
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM control_nodes WHERE node_id=?", (node_id,)).fetchone()
            if not row:
                raise ControlPlaneError("逻辑节点不存在")
            fields = self._node_fields(patch, existing=_node_value(row))
            if any(not self._node_exists(db, fallback) for fallback in fields["fallback_node_ids"]):
                raise DirectoryValidationError("逻辑节点包含未知备用节点")
            db.execute("UPDATE control_nodes SET name=?,priority=?,fallback_node_ids=?,metadata=?,enabled=?,updated_at=? WHERE node_id=?",
                       (fields["name"], fields["priority"], json.dumps(fields["fallback_node_ids"], ensure_ascii=False, separators=(",", ":")),
                        json.dumps(fields["metadata"], ensure_ascii=False, separators=(",", ":")), int(fields["enabled"]), current, node_id))
            revision = self._bump_revision(db)
            self._audit_in_db(db, "node_updated", "node", node_id, {"revision": revision}, now=current)
        return self.get_node(node_id)

    def delete_node(self, node_id: str, *, now: int | None = None) -> None:
        node_id = _identifier(node_id, "逻辑节点编号")
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT 1 FROM control_nodes WHERE node_id=?", (node_id,)).fetchone()
            if not row:
                raise ControlPlaneError("逻辑节点不存在")
            if db.execute("SELECT 1 FROM control_grant_refs WHERE node_id=? LIMIT 1", (node_id,)).fetchone():
                raise ControlPlaneError("逻辑节点仍被客户订阅引用")
            if db.execute("SELECT 1 FROM control_nodes WHERE fallback_node_ids LIKE ? LIMIT 1", (f'%"{node_id}"%',)).fetchone():
                raise ControlPlaneError("逻辑节点仍被备用节点引用")
            db.execute("DELETE FROM control_nodes WHERE node_id=?", (node_id,))
            revision = self._bump_revision(db)
            self._audit_in_db(db, "node_deleted", "node", node_id, {"revision": revision}, now=current)

    def _node_in_db(self, db: sqlite3.Connection, node_id: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM control_nodes WHERE node_id=?", (node_id,)).fetchone()
        if not row:
            raise ControlPlaneError("逻辑节点不存在")
        node = _node_value(row)
        transports = []
        for transport_row in db.execute("SELECT * FROM control_transports WHERE node_id=? ORDER BY priority,transport_id", (node_id,)):
            transport = dict(transport_row)
            transport["enabled"] = bool(transport["enabled"])
            transport["id"] = transport.pop("transport_id")
            endpoints = []
            for endpoint_row in db.execute("SELECT endpoint_id,address,priority,expires_at,enabled FROM control_endpoints WHERE node_id=? AND transport_id=? ORDER BY priority,address,endpoint_id", (node_id, transport["id"])):
                endpoint = dict(endpoint_row)
                endpoint["enabled"] = bool(endpoint["enabled"])
                endpoints.append(endpoint)
            transport["endpoints"] = endpoints
            transports.append(transport)
        egress = []
        for egress_row in db.execute("SELECT * FROM control_egresses WHERE node_id=? ORDER BY priority,egress_id", (node_id,)):
            row_value = dict(egress_row)
            row_value["enabled"] = bool(row_value["enabled"])
            row_value["id"] = row_value.pop("egress_id")
            egress.append(row_value)
        node["transports"] = transports
        node["egress"] = egress
        return node

    def get_node(self, node_id: str) -> dict[str, Any]:
        node_id = _identifier(node_id, "逻辑节点编号")
        with self.store._connect() as db:
            return self._node_in_db(db, node_id)

    def list_nodes(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        with self.store._connect() as db:
            rows = db.execute("SELECT node_id FROM control_nodes {} ORDER BY priority,node_id".format(
                "" if include_disabled else "WHERE enabled=1")).fetchall()
            return [self._node_in_db(db, row[0]) for row in rows]

    @staticmethod
    def _transport_fields(value: dict[str, Any], *, node_id: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = {**(existing or {}), **value}
        transport_id = _identifier(merged.get("transport_id", merged.get("id")), "节点传输编号")
        kind = merged.get("kind", merged.get("type", merged.get("transport")))
        if kind not in TRANSPORT_KINDS:
            raise DirectoryValidationError("节点传输类型无效")
        access = merged.get("access", merged.get("scope", "public"))
        if access not in ACCESS_KINDS:
            raise DirectoryValidationError("节点传输接入范围无效")
        priority = _priority(merged.get("priority", 100), "节点传输")
        tunnel = merged.get("tunnel")
        if tunnel is not None and (not isinstance(tunnel, str) or not TUNNEL_IDENTIFIER.fullmatch(tunnel)):
            raise DirectoryValidationError("节点传输隧道映射无效")
        enabled = merged.get("enabled", True)
        if type(enabled) is not bool:
            raise DirectoryValidationError("节点传输启用状态无效")
        return {"node_id": node_id, "transport_id": transport_id, "kind": kind, "access": access,
                "priority": priority, "tunnel": tunnel, "enabled": enabled}

    def _create_transport_in_db(self, db: sqlite3.Connection, node_id: str,
                                transport_id: str | dict[str, Any], *, now: int) -> None:
        value = dict(transport_id) if isinstance(transport_id, dict) else {"id": transport_id}
        fields = self._transport_fields(value, node_id=node_id)
        raw_endpoints = value.get("endpoints", value.get("addresses", value.get("endpoint")))
        if isinstance(raw_endpoints, (str, dict)):
            raw_endpoints = [raw_endpoints]
        if not isinstance(raw_endpoints, list) or not 1 <= len(raw_endpoints) <= MAX_ENDPOINTS:
            raise DirectoryValidationError("节点传输接入地址数量无效")
        try:
            db.execute("""INSERT INTO control_transports(
                node_id,transport_id,kind,access,priority,tunnel,enabled,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                       (node_id, fields["transport_id"], fields["kind"], fields["access"], fields["priority"],
                        fields["tunnel"], int(fields["enabled"]), now, now))
        except sqlite3.IntegrityError:
            raise ControlPlaneError("节点传输已存在") from None
        for index, raw in enumerate(raw_endpoints):
            self._insert_endpoint(db, node_id, fields["transport_id"], raw, index=index, now=now)
        revision = self._bump_revision(db)
        self._audit_in_db(db, "transport_created", "transport", f"{node_id}/{fields['transport_id']}",
                          {"revision": revision}, now=now)

    def _create_egress_in_db(self, db: sqlite3.Connection, node_id: str,
                             egress_id: str | dict[str, Any], *, now: int) -> None:
        value = dict(egress_id) if isinstance(egress_id, dict) else {"id": egress_id, "kind": egress_id}
        fields = self._egress_fields(value, node_id=node_id)
        try:
            db.execute("""INSERT INTO control_egresses(
                node_id,egress_id,kind,priority,enabled,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)""",
                       (node_id, fields["egress_id"], fields["kind"], fields["priority"],
                        int(fields["enabled"]), now, now))
        except sqlite3.IntegrityError:
            raise ControlPlaneError("节点出口已存在") from None
        revision = self._bump_revision(db)
        self._audit_in_db(db, "egress_created", "egress", f"{node_id}/{fields['egress_id']}",
                          {"revision": revision}, now=now)

    def create_transport(self, node_id: str, transport_id: str | dict[str, Any], *, kind: str | None = None,
                         access: str = "public", priority: int = 100, endpoints: list[Any] | Any | None = None,
                         tunnel: str | None = None, enabled: bool = True, now: int | None = None) -> dict[str, Any]:
        value = dict(transport_id) if isinstance(transport_id, dict) else {"id": transport_id, "kind": kind,
            "access": access, "priority": priority, "endpoints": endpoints, "tunnel": tunnel, "enabled": enabled}
        node_id = _identifier(node_id, "逻辑节点编号")
        self._transport_fields(value, node_id=node_id)
        raw_endpoints = value.get("endpoints", value.get("addresses", value.get("endpoint")))
        if isinstance(raw_endpoints, (str, dict)):
            raw_endpoints = [raw_endpoints]
        if not isinstance(raw_endpoints, list) or not 1 <= len(raw_endpoints) <= MAX_ENDPOINTS:
            raise DirectoryValidationError("节点传输接入地址数量无效")
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._node_exists(db, node_id):
                raise ControlPlaneError("逻辑节点不存在")
            self._create_transport_in_db(db, node_id, value, now=current)
        return self.get_node(node_id)

    def update_transport(self, node_id: str, transport_id: str, value: dict[str, Any] | None = None, *, now: int | None = None,
                         **changes: Any) -> dict[str, Any]:
        node_id, transport_id = _identifier(node_id, "逻辑节点编号"), _identifier(transport_id, "节点传输编号")
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM control_transports WHERE node_id=? AND transport_id=?", (node_id, transport_id)).fetchone()
            if not row:
                raise ControlPlaneError("节点传输不存在")
            existing = dict(row)
            existing["id"] = existing["transport_id"]
            patch = {**(value or {}), **changes, "id": transport_id}
            fields = self._transport_fields(patch, node_id=node_id, existing=existing)
            db.execute("UPDATE control_transports SET kind=?,access=?,priority=?,tunnel=?,enabled=?,updated_at=? WHERE node_id=? AND transport_id=?",
                       (fields["kind"], fields["access"], fields["priority"], fields["tunnel"], int(fields["enabled"]), current, node_id, transport_id))
            endpoint_key = next((key for key in ("endpoints", "addresses", "endpoint") if key in patch), None)
            if endpoint_key is not None:
                raw_endpoints = patch[endpoint_key]
                if isinstance(raw_endpoints, (str, dict)):
                    raw_endpoints = [raw_endpoints]
                if not isinstance(raw_endpoints, list) or not 1 <= len(raw_endpoints) <= MAX_ENDPOINTS:
                    raise DirectoryValidationError("节点传输接入地址数量无效")
                db.execute("DELETE FROM control_endpoints WHERE node_id=? AND transport_id=?", (node_id, transport_id))
                for index, raw in enumerate(raw_endpoints):
                    self._insert_endpoint(db, node_id, transport_id, raw, index=index, now=current)
            revision = self._bump_revision(db)
            self._audit_in_db(db, "transport_updated", "transport", f"{node_id}/{transport_id}", {"revision": revision}, now=current)
        return self.get_node(node_id)

    def delete_transport(self, node_id: str, transport_id: str, *, now: int | None = None) -> None:
        node_id, transport_id = _identifier(node_id, "逻辑节点编号"), _identifier(transport_id, "节点传输编号")
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM control_grant_refs WHERE node_id=? AND transport_id=? LIMIT 1",
                          (node_id, transport_id)).fetchone():
                raise ControlPlaneError("节点传输仍被客户订阅引用")
            if db.execute("DELETE FROM control_transports WHERE node_id=? AND transport_id=?", (node_id, transport_id)).rowcount == 0:
                raise ControlPlaneError("节点传输不存在")
            revision = self._bump_revision(db)
            self._audit_in_db(db, "transport_deleted", "transport", f"{node_id}/{transport_id}", {"revision": revision}, now=current)

    def _insert_endpoint(self, db: sqlite3.Connection, node_id: str, transport_id: str, value: Any, *, index: int,
                         now: int, endpoint_id: str | None = None) -> str:
        if isinstance(value, str):
            value = {"address": value}
        if not isinstance(value, dict):
            raise DirectoryValidationError("节点目录接入地址无效")
        endpoint_id = endpoint_id or value.get("id", value.get("endpoint_id", f"endpoint-{index}"))
        endpoint_id = _identifier(endpoint_id, "接入地址编号")
        address = _safe_endpoint(str(db.execute("SELECT kind FROM control_transports WHERE node_id=? AND transport_id=?", (node_id, transport_id)).fetchone()[0]), value.get("address", value.get("endpoint")))
        priority = _priority(value.get("priority", 100), "接入地址")
        expires_at = value.get("expires_at")
        if expires_at is not None and (not _is_int(expires_at) or expires_at <= 0):
            raise DirectoryValidationError("接入地址有效期无效")
        enabled = value.get("enabled", True)
        if type(enabled) is not bool:
            raise DirectoryValidationError("接入地址启用状态无效")
        try:
            db.execute("""INSERT INTO control_endpoints(
                node_id,transport_id,endpoint_id,address,priority,expires_at,enabled,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                       (node_id, transport_id, endpoint_id, address, priority, expires_at, int(enabled), now, now))
        except sqlite3.IntegrityError:
            raise ControlPlaneError("接入地址编号重复") from None
        return endpoint_id

    def create_endpoint(self, node_id: str, transport_id: str, value: str | dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
        node_id, transport_id = _identifier(node_id, "逻辑节点编号"), _identifier(transport_id, "节点传输编号")
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM control_transports WHERE node_id=? AND transport_id=?", (node_id, transport_id)).fetchone():
                raise ControlPlaneError("节点传输不存在")
            index = db.execute("SELECT COUNT(*) FROM control_endpoints WHERE node_id=? AND transport_id=?",
                               (node_id, transport_id)).fetchone()[0]
            self._insert_endpoint(db, node_id, transport_id, value, index=index, now=current)
            revision = self._bump_revision(db)
            self._audit_in_db(db, "endpoint_created", "endpoint", f"{node_id}/{transport_id}", {"revision": revision}, now=current)
        return self.get_node(node_id)

    def update_endpoint(self, node_id: str, transport_id: str, endpoint_id: str, value: str | dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
        node_id, transport_id, endpoint_id = (_identifier(node_id, "逻辑节点编号"), _identifier(transport_id, "节点传输编号"), _identifier(endpoint_id, "接入地址编号"))
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM control_endpoints WHERE node_id=? AND transport_id=? AND endpoint_id=?", (node_id, transport_id, endpoint_id)).fetchone():
                raise ControlPlaneError("接入地址不存在")
            if isinstance(value, str):
                value = {"address": value}
            if not isinstance(value, dict):
                raise DirectoryValidationError("节点目录接入地址无效")
            kind = db.execute("SELECT kind FROM control_transports WHERE node_id=? AND transport_id=?", (node_id, transport_id)).fetchone()[0]
            address = _safe_endpoint(kind, value.get("address", value.get("endpoint")))
            priority = _priority(value.get("priority", 100), "接入地址")
            expires_at = value.get("expires_at")
            if expires_at is not None and (not _is_int(expires_at) or expires_at <= 0):
                raise DirectoryValidationError("接入地址有效期无效")
            enabled = value.get("enabled", True)
            if type(enabled) is not bool:
                raise DirectoryValidationError("接入地址启用状态无效")
            db.execute("UPDATE control_endpoints SET address=?,priority=?,expires_at=?,enabled=?,updated_at=? WHERE node_id=? AND transport_id=? AND endpoint_id=?",
                       (address, priority, expires_at, int(enabled), current, node_id, transport_id, endpoint_id))
            revision = self._bump_revision(db)
            self._audit_in_db(db, "endpoint_updated", "endpoint", f"{node_id}/{transport_id}/{endpoint_id}", {"revision": revision}, now=current)
        return self.get_node(node_id)

    def delete_endpoint(self, node_id: str, transport_id: str, endpoint_id: str, *, now: int | None = None) -> None:
        node_id, transport_id, endpoint_id = (_identifier(node_id, "逻辑节点编号"),
                                               _identifier(transport_id, "节点传输编号"),
                                               _identifier(endpoint_id, "接入地址编号"))
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("DELETE FROM control_endpoints WHERE node_id=? AND transport_id=? AND endpoint_id=?", (node_id, transport_id, endpoint_id)).rowcount == 0:
                raise ControlPlaneError("接入地址不存在")
            revision = self._bump_revision(db)
            self._audit_in_db(db, "endpoint_deleted", "endpoint", f"{node_id}/{transport_id}/{endpoint_id}", {"revision": revision}, now=current)

    @staticmethod
    def _egress_fields(value: dict[str, Any], *, node_id: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = {**(existing or {}), **value}
        egress_id = _identifier(merged.get("egress_id", merged.get("id")), "出口编号")
        kind = merged.get("kind", egress_id)
        if not isinstance(kind, str) or not 1 <= len(kind) <= 64 or not IDENTIFIER.fullmatch(kind):
            raise DirectoryValidationError("节点出口类型无效")
        priority = _priority(merged.get("priority", 100), "节点出口")
        enabled = merged.get("enabled", True)
        if type(enabled) is not bool:
            raise DirectoryValidationError("节点出口启用状态无效")
        return {"node_id": node_id, "egress_id": egress_id, "kind": kind, "priority": priority, "enabled": enabled}

    def create_egress(self, node_id: str, egress_id: str | dict[str, Any], *, kind: str | None = None,
                      priority: int = 100, enabled: bool = True, now: int | None = None) -> dict[str, Any]:
        node_id = _identifier(node_id, "逻辑节点编号")
        value = dict(egress_id) if isinstance(egress_id, dict) else {"id": egress_id, "kind": kind or egress_id, "priority": priority, "enabled": enabled}
        self._egress_fields(value, node_id=node_id)
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._node_exists(db, node_id):
                raise ControlPlaneError("逻辑节点不存在")
            self._create_egress_in_db(db, node_id, value, now=current)
        return self.get_node(node_id)

    def update_egress(self, node_id: str, egress_id: str, value: dict[str, Any] | None = None, *, now: int | None = None,
                      **changes: Any) -> dict[str, Any]:
        node_id, egress_id = _identifier(node_id, "逻辑节点编号"), _identifier(egress_id, "出口编号")
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM control_egresses WHERE node_id=? AND egress_id=?", (node_id, egress_id)).fetchone()
            if not row:
                raise ControlPlaneError("节点出口不存在")
            existing = dict(row)
            existing["id"] = existing["egress_id"]
            fields = self._egress_fields({**(value or {}), **changes, "id": egress_id}, node_id=node_id, existing=existing)
            db.execute("UPDATE control_egresses SET kind=?,priority=?,enabled=?,updated_at=? WHERE node_id=? AND egress_id=?",
                       (fields["kind"], fields["priority"], int(fields["enabled"]), current, node_id, egress_id))
            revision = self._bump_revision(db)
            self._audit_in_db(db, "egress_updated", "egress", f"{node_id}/{egress_id}", {"revision": revision}, now=current)
        return self.get_node(node_id)

    def delete_egress(self, node_id: str, egress_id: str, *, now: int | None = None) -> None:
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("DELETE FROM control_egresses WHERE node_id=? AND egress_id=?", (node_id, egress_id)).rowcount == 0:
                raise ControlPlaneError("节点出口不存在")
            refs = []
            for row in db.execute("SELECT grant_id,egress_ids FROM control_grant_refs WHERE node_id=?", (node_id,)):
                if egress_id in json.loads(row[1]):
                    refs.append(row[0])
            if refs:
                raise ControlPlaneError("节点出口仍被客户订阅引用")
            revision = self._bump_revision(db)
            self._audit_in_db(db, "egress_deleted", "egress", f"{node_id}/{egress_id}", {"revision": revision}, now=current)

    def set_grant_reference(self, grant_id: str, node_id: str, egress: list[str] | str, *, transport: str | None = None,
                            priority: int = 100, now: int | None = None) -> dict[str, Any]:
        grant_id = _identifier(grant_id, "客户线路编号")
        node_id = _identifier(node_id, "逻辑节点编号")
        if isinstance(egress, str):
            egress = [egress]
        if not isinstance(egress, list) or not 1 <= len(egress) <= MAX_EGRESSES:
            raise DirectoryValidationError("客户出口授权无效")
        egress_ids = [_identifier(item, "客户出口编号") for item in egress]
        if len(set(egress_ids)) != len(egress_ids):
            raise DirectoryValidationError("客户出口授权重复")
        if transport is not None:
            transport = _identifier(transport, "客户传输编号")
        priority = _priority(priority, "客户线路")
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            node = self._node_in_db(db, node_id)
            known_egresses = {row["id"] for row in node["egress"]}
            if any(item not in known_egresses for item in egress_ids):
                raise ControlPlaneError("客户引用了不存在的节点出口")
            if transport is not None and transport not in {row["id"] for row in node["transports"]}:
                raise ControlPlaneError("客户引用了不存在的节点传输")
            if not self.store.line_grant(grant_id):
                raise ControlPlaneError("客户线路不存在")
            db.execute("""INSERT INTO control_grant_refs(grant_id,node_id,egress_ids,transport_id,priority,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(grant_id) DO UPDATE SET node_id=excluded.node_id,
                egress_ids=excluded.egress_ids,transport_id=excluded.transport_id,priority=excluded.priority,updated_at=excluded.updated_at""",
                       (grant_id, node_id, json.dumps(egress_ids, ensure_ascii=False, separators=(",", ":")), transport, priority, current, current))
            revision = self._bump_revision(db)
            self._audit_in_db(db, "grant_reference_saved", "grant", grant_id, {"node_id": node_id, "revision": revision}, now=current)
        return self.grant_reference(grant_id) or {}

    def remove_grant_reference(self, grant_id: str, *, now: int | None = None) -> None:
        current = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("DELETE FROM control_grant_refs WHERE grant_id=?", (grant_id,)).rowcount == 0:
                raise ControlPlaneError("客户线路逻辑引用不存在")
            revision = self._bump_revision(db)
            self._audit_in_db(db, "grant_reference_deleted", "grant", grant_id, {"revision": revision}, now=current)

    def grant_reference(self, grant_id: str) -> dict[str, Any] | None:
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM control_grant_refs WHERE grant_id=?", (grant_id,)).fetchone()
        if not row:
            return None
        value = dict(row)
        value["egress"] = json.loads(value.pop("egress_ids"))
        value["transport"] = value.pop("transport_id")
        return value

    def _logical_refs_for_customer(self, customer_id: str, *, now: int | None = None) -> list[dict[str, Any]]:
        now = self._now(now)
        with self.store._connect() as db:
            rows = db.execute("""SELECT g.id,g.alias,g.expires_at,g.access_mode,r.node_id,r.egress_ids,r.transport_id,r.priority
                FROM line_grants g JOIN control_grant_refs r ON r.grant_id=g.id
                WHERE g.customer_id=? AND g.enabled=1 AND (g.expires_at IS NULL OR g.expires_at>?)
                ORDER BY r.priority,g.id""", (customer_id, now)).fetchall()
        return [{"id": row["id"], "name": row["alias"], "node_id": row["node_id"],
                 "egress": json.loads(row["egress_ids"]), "transport": row["transport_id"],
                 "priority": row["priority"], "expires_at": row["expires_at"],
                 "access_mode": row["access_mode"]} for row in rows]

    def has_logical_references(self, customer_id: str, *, now: int | None = None) -> bool:
        refs = self._logical_refs_for_customer(customer_id, now=now)
        with self.store._connect() as db:
            active = db.execute("SELECT COUNT(*) FROM line_grants WHERE customer_id=? AND enabled=1 AND (expires_at IS NULL OR expires_at>?)", (customer_id, self._now(now))).fetchone()[0]
        return bool(active and len(refs) == active)

    def resolve_grant_endpoint(self, grant_id: str, *, transport_id: str | None = None,
                               now: int | None = None) -> dict[str, Any]:
        """Resolve a logical grant against current control-plane state.

        The returned endpoint is derived from the signed-directory source of
        truth, never from a client supplied address.  Lease responses use this
        value so an address rotation takes effect without trusting stale route
        data cached by a client.
        """
        grant_id = _identifier(grant_id, "客户线路编号")
        if transport_id is not None:
            transport_id = _identifier(transport_id, "客户接入入口编号")
        now = self._now(now)
        with self._db() as db:
            row = db.execute("""SELECT g.customer_id,g.enabled,g.expires_at,r.node_id,r.egress_ids,r.transport_id
                FROM line_grants g JOIN control_grant_refs r ON r.grant_id=g.id WHERE g.id=?""", (grant_id,)).fetchone()
            if not row or not row["enabled"] or (row["expires_at"] is not None and row["expires_at"] <= now):
                raise ControlPlaneError("客户线路未获授权或已经过期")
            if transport_id is not None and row["transport_id"] not in (None, transport_id):
                raise ControlPlaneError("所选入口不属于该客户授权线路")
            requested_egresses = json.loads(row["egress_ids"])
            pending = [row["node_id"]]
            seen: set[str] = set()
            while pending and len(seen) <= MAX_FALLBACKS + 1:
                node_id = pending.pop(0)
                if node_id in seen:
                    continue
                seen.add(node_id)
                node = self._node_in_db(db, node_id)
                pending.extend(node.get("fallback_node_ids", []))
                if not node.get("enabled", True):
                    continue
                egresses = {item["id"]: item for item in node.get("egress", [])
                            if item.get("enabled", True)}
                selected_egress = next((egress_id for egress_id in requested_egresses if egress_id in egresses), None)
                if selected_egress is None:
                    continue
                transports = [item for item in node.get("transports", []) if item.get("enabled", True)]
                selected_transport = transport_id or row["transport_id"]
                if selected_transport is not None:
                    transports = [item for item in transports if item["id"] == selected_transport]
                for transport in sorted(transports, key=lambda item: (item["priority"], item["id"])):
                    endpoints = [item for item in transport.get("endpoints", [])
                                 if item.get("enabled", True) and
                                 (item.get("expires_at") is None or item["expires_at"] > now)]
                    if not endpoints:
                        continue
                    endpoint = sorted(endpoints, key=lambda item: (item["priority"], item["address"]))[0]
                    return {"grant_id": grant_id, "customer_id": row["customer_id"],
                            "node_id": node_id, "transport_id": transport["id"],
                            "transport_kind": transport["kind"], "access": transport["access"],
                            "endpoint": endpoint["address"], "egress_id": selected_egress,
                            "egress_kind": egresses[selected_egress]["kind"],
                            "fallback_used": node_id != row["node_id"]}
        raise ControlPlaneError("客户线路没有可用的当前接入地址或授权出口")

    def subscription_payload(self, device: dict[str, Any], *, directory_url: str | None = None,
                             now: int | None = None) -> dict[str, Any]:
        now = self._now(now)
        customer = self.store.customer(device["customer_id"])
        refs = self._logical_refs_for_customer(customer["id"], now=now)
        if not refs or not self.has_logical_references(customer["id"], now=now):
            raise ControlPlaneError("客户线路尚未完成逻辑节点迁移")
        envelope = self.publisher.ensure_published(now=now)
        expires = now + 86400
        grant_expiries = [row["expires_at"] for row in refs if row.get("expires_at") is not None]
        if grant_expiries:
            expires = min(expires, *grant_expiries)
        if expires <= now:
            raise ControlPlaneError("客户线路已经过期")
        url = directory_url or self.directory_url
        if not url:
            raise ControlPlaneError("节点目录地址尚未配置")
        return {
            "schema_version": 1, "version": 2, "device_id": device["id"],
            "nonce": secrets.token_urlsafe(16), "issued_at": now, "expires_at": expires,
            "subscription_id": "subscription-" + customer["id"],
            "customer": {"id": customer["id"], "display_name": customer["display_name"]},
            "node_directory": {"url": url.rstrip("/"), "public_key": self.publisher.public_key,
                                "minimum_revision": envelope["payload"]["revision"]},
            "node_refs": [{key: row[key] for key in ("id", "name", "node_id", "egress", "transport", "priority", "access_mode")} for row in refs],
            "usage": self.store.usage(customer["id"], now=now), "server_time": now,
        }

    # Enrollment and device credentials ---------------------------------
    def create_enrollment_token(self, customer_id: str, *, ttl: int | None = 900,
                                now: int | None = None) -> str:
        now = self._now(now)
        try:
            token = self.store.create_enrollment_token(
                customer_id, ttl=ttl, now=now,
                audit_callback=lambda db, digest, expires_at: self._audit_in_db(
                    db, "enrollment_token_issued", "customer", customer_id,
                    {"token_digest_prefix": digest[:16], "expires_at": expires_at}, now=now))
        except (ClientStoreError, ValueError) as exc:
            raise EnrollmentError(str(exc)) from None
        return token

    def enroll_device(self, token: str, public_key: str, label: str, *, wireguard_public_key: str,
                      device_id: str | None = None, now: int | None = None) -> dict[str, Any]:
        if not isinstance(token, str) or not token or len(token) > 512:
            raise EnrollmentError("开户令牌无效、已使用或已过期")
        public_key = _ed25519_public(public_key)
        wireguard_public_key = _wireguard_public(wireguard_public_key)
        if not isinstance(label, str) or len(label) > 120:
            raise EnrollmentError("设备名称无效")
        now = self._now(now)
        try:
            device = self.store.enroll_device(
                token, public_key, label, wireguard_public_key=wireguard_public_key,
                device_id=device_id, now=now,
                audit_callback=lambda db, enrolled: self._audit_in_db(
                    db, "device_enrolled", "device", enrolled["id"],
                    {"customer_id": enrolled["customer_id"],
                     "public_key_fingerprint": hashlib.sha256(public_key.encode()).hexdigest()[:16]}, now=now))
        except (ClientStoreError, ValueError, sqlite3.IntegrityError) as exc:
            raise EnrollmentError(str(exc)) from None
        return {key: device[key] for key in ("id", "customer_id", "label", "public_key", "wireguard_public_key", "enabled", "created_at")}

    def revoke_device(self, device_id: str, *, now: int | None = None) -> None:
        device_id = _identifier(device_id, "设备编号")
        now = self._now(now)
        try:
            self.store.revoke(
                "device", device_id, now=now,
                audit_callback=lambda db: self._audit_in_db(
                    db, "device_revoked", "device", device_id, {}, now=now))
        except ClientStoreError as exc:
            raise EnrollmentError(str(exc)) from None

    def revoke_enrollment_tokens(self, customer_id: str, *, now: int | None = None) -> int:
        customer_id = _identifier(customer_id, "客户编号")
        now = self._now(now)
        try:
            return self.store.revoke_enrollment_tokens(
                customer_id, now=now,
                audit_callback=lambda db, count: self._audit_in_db(
                    db, "enrollment_tokens_revoked", "customer", customer_id,
                    {"count": count}, now=now))
        except (ClientStoreError, ValueError) as exc:
            raise EnrollmentError(str(exc)) from None

    def rotate_device_credentials(self, device_id: str, public_key: str, *, wireguard_public_key: str | None = None,
                                  now: int | None = None) -> dict[str, Any]:
        device_id = _identifier(device_id, "设备编号")
        public_key = _ed25519_public(public_key)
        if wireguard_public_key is not None:
            wireguard_public_key = _wireguard_public(wireguard_public_key)
        now = self._now(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
            if not row:
                raise EnrollmentError("设备不存在")
            if not row["enabled"]:
                raise EnrollmentError("设备已停用")
            if public_key == row["public_key"] and (wireguard_public_key is None or wireguard_public_key == row["wireguard_public_key"]):
                raise EnrollmentError("新设备凭据必须与当前凭据不同")
            try:
                db.execute("UPDATE devices SET public_key=?,wireguard_public_key=?,last_seen_at=? WHERE id=?",
                           (public_key, wireguard_public_key or row["wireguard_public_key"], now, device_id))
            except sqlite3.IntegrityError:
                raise EnrollmentError("设备凭据已被其他设备使用") from None
            revoked = db.execute("UPDATE leases SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL",
                                 (now, device_id)).rowcount
            self._audit_in_db(db, "device_credentials_rotated", "device", device_id,
                              {"public_key_fingerprint": hashlib.sha256(public_key.encode()).hexdigest()[:16],
                               "leases_revoked": revoked}, now=now)
            result = dict(db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone())
        return {key: result[key] for key in ("id", "customer_id", "label", "public_key", "wireguard_public_key", "enabled", "last_seen_at")}

    # Naming aliases used by administrative integrations during migration.
    create_logical_node = create_node
    update_logical_node = update_node
    delete_logical_node = delete_node
    list_logical_nodes = list_nodes
    create_node_transport = create_transport
    update_node_transport = update_transport
    delete_node_transport = delete_transport
    create_node_egress = create_egress
    update_node_egress = update_egress
    delete_node_egress = delete_egress
    enroll = enroll_device
    revoke = revoke_device
    revoke_tokens = revoke_enrollment_tokens
    rotate_credentials = rotate_device_credentials


class DirectoryPublisher:
    """Build, sign, fence and atomically persist the current node directory."""

    def __init__(self, service: ControlPlaneService, *, private_key: str | bytes | Ed25519PrivateKey | None = None,
                 key_file: str | Path | None = None, default_ttl: int = 86400):
        self.service = service
        self.default_ttl = default_ttl
        self.key_file = Path(key_file) if key_file is not None else None
        if private_key is not None and key_file is not None:
            raise ControlPlaneError("目录签名私钥不能同时从参数和文件提供")
        if private_key is not None:
            self._private_key = _key_from_private(private_key)
        elif self.key_file is not None:
            self._private_key = self._load_or_create_key(self.key_file)
        else:
            self._private_key = Ed25519PrivateKey.generate()
        self.public_key = b64url_encode(self._private_key.public_key().public_bytes_raw())
        self.output_path = self.service.store.path.with_name("node-directory.json")
        try:
            self.reconcile_file()
        except (ControlPlaneError, OSError):
            # A read-only or temporarily unavailable output volume must not
            # prevent the control plane from starting.  The next publication
            # retries the same DB-authoritative reconciliation under the lock.
            pass

    @staticmethod
    def _load_or_create_key(path: Path) -> Ed25519PrivateKey:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _cross_process_lock(path.with_name(path.name + ".lock")):
            try:
                raw = path.read_bytes()
                try:
                    key = _key_from_private(raw)
                except ControlPlaneError:
                    key = _key_from_private(raw.decode("ascii").strip())
                return key
            except FileNotFoundError:
                key = Ed25519PrivateKey.generate()
                temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
                try:
                    temporary.write_bytes(key.private_bytes_raw())
                    if os.name != "nt":
                        temporary.chmod(0o600)
                    with temporary.open("r+b") as stream:
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
                return key
            except (OSError, UnicodeError):
                raise ControlPlaneError("目录签名私钥读取失败") from None

    @staticmethod
    def _node_payload(node: dict[str, Any], *, now: int, expires_at: int) -> dict[str, Any]:
        if not node.get("enabled", True):
            raise DirectoryValidationError("不能发布已停用逻辑节点")
        transports = []
        for source in node.get("transports", []):
            if not source.get("enabled", True):
                continue
            endpoints = []
            for endpoint in source.get("endpoints", []):
                if not endpoint.get("enabled", True):
                    continue
                endpoint_expires = endpoint.get("expires_at") or expires_at
                if not _is_int(endpoint_expires) or endpoint_expires <= 0 or endpoint_expires > expires_at:
                    raise DirectoryValidationError("接入地址有效期必须不晚于目录有效期")
                if endpoint_expires <= now:
                    continue
                endpoints.append({"address": endpoint["address"], "priority": endpoint["priority"], "expires_at": endpoint_expires})
            if not endpoints:
                continue
            transport = {"id": source["id"], "kind": source["kind"], "access": source["access"],
                         "priority": source["priority"], "endpoints": endpoints}
            if source.get("tunnel") is not None:
                transport["tunnel"] = source["tunnel"]
            transports.append(transport)
        egress = [{"id": row["id"], "kind": row["kind"], "priority": row["priority"]}
                  for row in node.get("egress", []) if row.get("enabled", True)]
        if not transports or not egress:
            raise DirectoryValidationError("逻辑节点必须至少包含一个可用传输和出口")
        value = {"node_id": node["node_id"], "name": node["name"], "priority": node["priority"],
                 "transports": sorted(transports, key=lambda row: (row["priority"], row["id"])),
                 "egress": sorted(egress, key=lambda row: (row["priority"], row["id"])),
                 "fallback_node_ids": list(node.get("fallback_node_ids", []))}
        if node.get("metadata"):
            value["metadata"] = node["metadata"]
        return value

    def payload(self, *, now: int | None = None, expires_at: int | None = None, ttl: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        ttl = self.default_ttl if ttl is None else ttl
        if not _is_int(now) or not _is_int(ttl) or not 60 <= ttl <= MAX_DIRECTORY_TTL:
            raise ControlPlaneError("节点目录有效期无效")
        expires_at = now + ttl if expires_at is None else expires_at
        if not _is_int(expires_at) or not now < expires_at <= now + MAX_DIRECTORY_TTL:
            raise ControlPlaneError("节点目录有效期无效")
        nodes = [self._node_payload(node, now=now, expires_at=expires_at)
                 for node in self.service.list_nodes(include_disabled=False)]
        if not 1 <= len(nodes) <= MAX_NODES:
            raise DirectoryValidationError("节点目录数量无效")
        revision = self.service.current_revision()
        if revision < 1:
            # A directory made from pre-existing imported state receives its
            # first revision at publication time.
            with self.service._db() as db:
                db.execute("BEGIN IMMEDIATE")
                revision = self.service._bump_revision(db)
                self.service._audit_in_db(db, "directory_revision_initialized", "directory", "", {"revision": revision}, now=now)
        value = {"schema_version": SCHEMA_VERSION, "revision": revision, "issued_at": now,
                 "expires_at": expires_at, "nodes": sorted(nodes, key=lambda row: row["node_id"])}
        validate_directory(value, now=now, allow_expired=True)
        return value

    def sign(self, payload: dict[str, Any]) -> dict[str, Any]:
        validate_directory(payload, now=payload["issued_at"], allow_expired=True)
        signature = b64url_encode(self._private_key.sign(_canonical_json(payload)))
        return {"payload": payload, "signature": signature}

    def _stored_envelope(self) -> dict[str, Any] | None:
        with self.service.store._connect() as db:
            row = db.execute("SELECT envelope,envelope_digest FROM control_directory_state WHERE id=1").fetchone()
        if not row or not row[0]:
            return None
        try:
            envelope = strict_json_loads(row[0])
            digest = row[1]
            if not isinstance(digest, str) or not secrets.compare_digest(
                    hashlib.sha256(_canonical_json(envelope)).hexdigest(), digest):
                return None
            verify_directory_envelope(envelope, self.public_key, now=int(time.time()), allow_expired=True)
            return envelope
        except Exception:
            return None

    def _reconcile_file_locked(self, target: Path) -> bool:
        """Make the output file match the DB source of truth after a crash."""
        stored = self._stored_envelope()
        if stored is None:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            return False
        try:
            current = strict_json_loads(target.read_bytes())
            verify_directory_envelope(current, self.public_key, now=int(time.time()), allow_expired=True)
        except Exception:
            current = None
        if current == stored:
            return False
        self.write_atomic(target, stored, _lock_held=True, _allow_rollback=True)
        return True

    def reconcile_file(self, output_path: str | Path | None = None) -> bool:
        """Repair a directory file left ahead of or behind SQLite state."""
        target = Path(output_path) if output_path is not None else self.output_path
        lock_path = target.with_name("." + target.name + ".lock")
        with _cross_process_lock(lock_path):
            return self._reconcile_file_locked(target)

    def publish(self, *, now: int | None = None, expires_at: int | None = None, ttl: int | None = None,
                output_path: str | Path | None = None) -> dict[str, Any]:
        if output_path is None:
            return self._publish_unlocked(now=now, expires_at=expires_at, ttl=ttl,
                                          output_path=None, _lock_held=True)
        target = Path(output_path)
        lock_path = target.with_name("." + target.name + ".lock")
        with _cross_process_lock(lock_path):
            self._reconcile_file_locked(target)
            return self._publish_unlocked(now=now, expires_at=expires_at, ttl=ttl,
                                          output_path=target, _lock_held=True)

    def _publish_unlocked(self, *, now: int | None = None, expires_at: int | None = None,
                          ttl: int | None = None, output_path: str | Path | None = None,
                          _lock_held: bool = False) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        # A repeated GET must not rewrite the same revision with a different
        # issued time.  That would make a revision fence reject an otherwise
        # harmless refresh.  An explicit expiry/TTL asks for a new envelope;
        # callers needing a fresh envelope after expiry should first change a
        # node address (which already advances the revision).
        with self.service.store._connect() as db:
            existing_row = db.execute("SELECT envelope,envelope_digest FROM control_directory_state WHERE id=1").fetchone()
        existing_envelope = None
        if existing_row and existing_row[0]:
            try:
                existing_envelope = strict_json_loads(existing_row[0])
                if existing_row[1] and not secrets.compare_digest(
                        hashlib.sha256(_canonical_json(existing_envelope)).hexdigest(), existing_row[1]):
                    raise DirectoryRollbackError("节点目录摘要与控制面状态不一致")
                verify_directory_envelope(existing_envelope, self.public_key, now=now)
                if (expires_at is None and ttl is None and
                        existing_envelope["payload"]["revision"] == self.service.current_revision()):
                    if output_path is not None:
                        self.write_atomic(output_path, existing_envelope, _lock_held=_lock_held)
                    return existing_envelope
            except DirectoryRollbackError:
                raise
            except Exception:
                existing_envelope = None
        # Once a stored envelope exists, a deliberate re-publish (or a
        # re-publish after expiry) must receive a strictly newer revision.
        needs_new_revision = ((existing_row and existing_row[0] and existing_envelope is None) or
                              (existing_row and existing_row[0] and (expires_at is not None or ttl is not None)))
        payload = self.payload(now=now, expires_at=expires_at, ttl=ttl)
        target = Path(output_path) if output_path is not None else None
        previous_bytes = None
        had_previous = False
        if target is not None:
            try:
                previous_bytes = target.read_bytes()
                had_previous = True
            except FileNotFoundError:
                pass
        file_written = False
        try:
            with self.service._db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT revision,envelope,envelope_digest FROM control_directory_state WHERE id=1").fetchone()
                current_revision = int(row["revision"] if row else 0)
                if needs_new_revision:
                    if current_revision != payload["revision"]:
                        raise DirectoryRollbackError("节点目录状态在发布期间发生变化，请重试")
                    revision = self.service._bump_revision(db)
                    payload["revision"] = revision
                    validate_directory(payload, now=now, allow_expired=True)
                    self.service._audit_in_db(db, "directory_revision_advanced", "directory", str(revision),
                                              {"revision": revision}, now=now)
                elif payload["revision"] != current_revision:
                    raise DirectoryRollbackError("节点目录版本不能回退")
                if row and row["envelope"] and payload["revision"] == current_revision:
                    previous = strict_json_loads(row["envelope"])
                    if (previous.get("payload") or {}).get("revision") == payload["revision"] and previous.get("payload") != payload:
                        raise DirectoryRollbackError("同一节点目录版本不能替换正文")
                envelope = self.sign(payload)
                if len(_canonical_json(envelope)) > MAX_DIRECTORY_BYTES:
                    raise DirectoryValidationError("节点目录响应过大")
                digest = hashlib.sha256(_canonical_json(envelope)).hexdigest()
                db.execute("UPDATE control_directory_state SET issued_at=?,expires_at=?,envelope=?,envelope_digest=? WHERE id=1",
                           (payload["issued_at"], payload["expires_at"], json.dumps(envelope, ensure_ascii=False, separators=(",", ":")), digest))
                self.service._audit_in_db(db, "directory_published", "directory", str(payload["revision"]),
                                          {"revision": payload["revision"], "digest": digest}, now=now)
                if target is not None:
                    # os.replace() may have completed even if the wrapper or
                    # process fails before write_atomic() returns.  Treat the
                    # file as dirty before calling it so the in-process path
                    # restores the previous bytes; startup reconciliation also
                    # repairs a crash between replace and DB commit.
                    file_written = True
                    self.write_atomic(target, envelope, _lock_held=_lock_held)
                db.commit()
        except Exception:
            if file_written and target is not None:
                self._restore_file(target, previous_bytes if had_previous else None)
            raise
        return envelope

    def ensure_published(self, *, now: int | None = None) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        # The default on-disk directory is part of the publication contract.
        # Reconcile it even when the DB envelope is otherwise valid, so a
        # crash or an operator deleting the file cannot leave a stale client
        # view indefinitely.
        self.reconcile_file()
        with self.service.store._connect() as db:
            row = db.execute("SELECT envelope,envelope_digest FROM control_directory_state WHERE id=1").fetchone()
        if row and row[0]:
            try:
                envelope = strict_json_loads(row[0])
                if row[1] and not secrets.compare_digest(hashlib.sha256(_canonical_json(envelope)).hexdigest(), row[1]):
                    raise DirectoryRollbackError("节点目录摘要与控制面状态不一致")
                verify_directory_envelope(envelope, self.public_key, now=now)
                if envelope["payload"]["revision"] == self.service.current_revision():
                    return envelope
            except Exception:
                pass
        return self.publish(now=now, output_path=self.output_path)

    @staticmethod
    def write_atomic(path: str | Path, envelope: dict[str, Any], *, _lock_held: bool = False,
                     _allow_rollback: bool = False) -> None:
        target = Path(path)
        if not _lock_held:
            lock_path = target.with_name("." + target.name + ".lock")
            with _cross_process_lock(lock_path):
                return DirectoryPublisher.write_atomic(target, envelope, _lock_held=True,
                                                       _allow_rollback=_allow_rollback)
        target.parent.mkdir(parents=True, exist_ok=True)
        body = _canonical_json(envelope)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                temporary.chmod(0o644)
            if target.exists() and not _allow_rollback:
                try:
                    previous = strict_json_loads(target.read_bytes())
                    previous_revision = int(previous["payload"]["revision"])
                    current_revision = int(envelope["payload"]["revision"])
                    if current_revision < previous_revision:
                        raise DirectoryRollbackError("节点目录文件版本不能回退")
                    if current_revision == previous_revision and previous.get("payload") != envelope.get("payload"):
                        raise DirectoryRollbackError("同一节点目录版本不能替换正文")
                except DirectoryRollbackError:
                    raise
                except (OSError, ValueError, KeyError, TypeError):
                    # A corrupt old file cannot silently become trusted data.
                    raise ControlPlaneError("已有节点目录文件损坏，请先人工移除") from None
            os.replace(temporary, target)
            if os.name != "nt":
                try:
                    directory_fd = os.open(str(target.parent), os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    pass
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _restore_file(path: Path, body: bytes | None) -> None:
        """Best-effort rollback if a DB commit fails after file replacement."""
        if body is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.rollback")
        try:
            temporary.write_bytes(body)
            with temporary.open("r+b") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def cached(self, *, now: int | None = None) -> dict[str, Any]:
        with self.service.store._connect() as db:
            row = db.execute("SELECT envelope,envelope_digest FROM control_directory_state WHERE id=1").fetchone()
        if not row or not row[0]:
            raise ControlPlaneError("节点目录尚未发布")
        envelope = strict_json_loads(row[0])
        if row[1] and not secrets.compare_digest(hashlib.sha256(_canonical_json(envelope)).hexdigest(), row[1]):
            raise DirectoryRollbackError("节点目录摘要与控制面状态不一致")
        payload = verify_directory_envelope(envelope, self.public_key, now=now)
        if payload["revision"] != self.service.current_revision():
            raise DirectoryRollbackError("节点目录缓存版本不是当前控制面版本")
        return payload


def validate_directory(value: object, *, now: int | None = None, allow_expired: bool = False) -> dict[str, Any]:
    """Validate a payload with the same bounds used by the client resolver."""
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise DirectoryValidationError("节点目录协议版本无效")
    now = int(time.time()) if now is None else now
    revision, issued_at, expires_at = value.get("revision"), value.get("issued_at"), value.get("expires_at")
    if not _is_int(revision) or revision < 1:
        raise DirectoryValidationError("节点目录版本号无效")
    if not _is_int(issued_at) or not _is_int(expires_at) or expires_at <= issued_at:
        raise DirectoryValidationError("节点目录有效期无效")
    if issued_at > now + 300 or expires_at - issued_at > MAX_DIRECTORY_TTL:
        raise DirectoryValidationError("节点目录尚未生效或有效期过长")
    if expires_at <= now and not allow_expired:
        raise DirectoryValidationError("节点目录已经过期")
    rows = value.get("nodes")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_NODES:
        raise DirectoryValidationError("节点目录数量无效")
    ids: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for node in rows:
        if not isinstance(node, dict):
            raise DirectoryValidationError("节点目录条目无效")
        node_id = _identifier(node.get("node_id", node.get("id")), "逻辑节点编号")
        if node_id in ids:
            raise DirectoryValidationError("节点目录存在重复逻辑节点")
        ids.add(node_id)
        name = node.get("name", node_id)
        if not isinstance(name, str) or not 1 <= len(name) <= 120:
            raise DirectoryValidationError("逻辑节点名称无效")
        transports_raw = node.get("transports", node.get("transport"))
        if isinstance(transports_raw, dict):
            transports_raw = [transports_raw]
        if not isinstance(transports_raw, list) or not 1 <= len(transports_raw) <= MAX_TRANSPORTS:
            raise DirectoryValidationError("逻辑节点传输数量无效")
        transports = []
        transport_ids: set[str] = set()
        for index, transport in enumerate(transports_raw):
            if not isinstance(transport, dict):
                raise DirectoryValidationError("节点传输信息无效")
            transport_id = _identifier(transport.get("id", f"transport-{index}"), "节点传输编号")
            if transport_id in transport_ids:
                raise DirectoryValidationError("逻辑节点存在重复传输编号")
            transport_ids.add(transport_id)
            kind = transport.get("kind", transport.get("type", transport.get("transport")))
            if kind not in TRANSPORT_KINDS:
                raise DirectoryValidationError("节点传输类型无效")
            access = transport.get("access", transport.get("scope", "public"))
            if access not in ACCESS_KINDS:
                raise DirectoryValidationError("节点传输接入范围无效")
            endpoints_raw = transport.get("endpoints", transport.get("addresses", transport.get("endpoint")))
            if isinstance(endpoints_raw, (str, dict)):
                endpoints_raw = [endpoints_raw]
            if not isinstance(endpoints_raw, list) or not 1 <= len(endpoints_raw) <= MAX_ENDPOINTS:
                raise DirectoryValidationError("节点传输接入地址数量无效")
            endpoints = []
            for endpoint in endpoints_raw:
                item = {"address": endpoint} if isinstance(endpoint, str) else endpoint
                if not isinstance(item, dict):
                    raise DirectoryValidationError("节点目录接入地址无效")
                address = _safe_endpoint(kind, item.get("address", item.get("endpoint")))
                ep_expires = item.get("expires_at", expires_at)
                if not _is_int(ep_expires) or ep_expires <= 0 or ep_expires > expires_at:
                    raise DirectoryValidationError("接入地址有效期无效")
                endpoints.append({"address": address, "priority": _priority(item.get("priority", 100), "接入地址"), "expires_at": ep_expires})
            transport_value = {"id": transport_id, "kind": kind, "access": access,
                               "priority": _priority(transport.get("priority", 100), "节点传输"),
                               "endpoints": sorted(endpoints, key=lambda row: (row["priority"], row["address"]))}
            if "tunnel" in transport:
                tunnel = transport["tunnel"]
                if not isinstance(tunnel, str) or not TUNNEL_IDENTIFIER.fullmatch(tunnel):
                    raise DirectoryValidationError("节点传输隧道映射无效")
                transport_value["tunnel"] = tunnel
            transports.append(transport_value)
        egress_raw = node.get("egress", node.get("egresses", node.get("egress_modes")))
        if isinstance(egress_raw, (str, dict)):
            egress_raw = [egress_raw]
        if not isinstance(egress_raw, list) or not 1 <= len(egress_raw) <= MAX_EGRESSES:
            raise DirectoryValidationError("逻辑节点出口数量无效")
        egress: list[dict[str, Any]] = []
        egress_ids: set[str] = set()
        for index, raw in enumerate(egress_raw):
            item = {"id": raw, "kind": raw} if isinstance(raw, str) else raw
            if not isinstance(item, dict):
                raise DirectoryValidationError("节点出口授权无效")
            egress_id = _identifier(item.get("id", item.get("egress_id", f"egress-{index}")), "出口编号")
            if egress_id in egress_ids:
                raise DirectoryValidationError("逻辑节点存在重复出口编号")
            egress_ids.add(egress_id)
            kind = item.get("kind", egress_id)
            if not isinstance(kind, str) or not 1 <= len(kind) <= 64 or not IDENTIFIER.fullmatch(kind):
                raise DirectoryValidationError("节点出口类型无效")
            egress.append({"id": egress_id, "kind": kind, "priority": _priority(item.get("priority", 100), "节点出口")})
        fallbacks = node.get("fallback_node_ids", node.get("fallbacks", []))
        if isinstance(fallbacks, str):
            fallbacks = [fallbacks]
        if not isinstance(fallbacks, list) or len(fallbacks) > MAX_FALLBACKS:
            raise DirectoryValidationError("逻辑节点备用节点列表无效")
        fallback_ids = [_identifier(item, "备用逻辑节点编号") for item in fallbacks]
        if len(set(fallback_ids)) != len(fallback_ids) or node_id in fallback_ids:
            raise DirectoryValidationError("逻辑节点备用节点列表重复或自引用")
        cleaned_node = {"node_id": node_id, "name": name, "priority": _priority(node.get("priority", 100), "逻辑节点"),
                        "transports": sorted(transports, key=lambda row: (row["priority"], row["id"])),
                        "egress": sorted(egress, key=lambda row: (row["priority"], row["id"])),
                        "fallback_node_ids": fallback_ids}
        if "metadata" in node:
            if not isinstance(node["metadata"], dict) or len(_canonical_json(node["metadata"])) > MAX_METADATA_BYTES:
                raise DirectoryValidationError("逻辑节点元数据无效或过大")
            cleaned_node["metadata"] = node["metadata"]
        cleaned.append(cleaned_node)
    for node in cleaned:
        if any(fallback not in ids for fallback in node["fallback_node_ids"]):
            raise DirectoryValidationError("节点目录包含未知备用节点")
    return {"schema_version": SCHEMA_VERSION, "revision": revision, "issued_at": issued_at,
            "expires_at": expires_at, "nodes": sorted(cleaned, key=lambda row: row["node_id"])}


def verify_directory_envelope(envelope: object, public_key: str | bytes | Ed25519PublicKey, *, now: int | None = None,
                              minimum_revision: int = 0, allow_expired: bool = False) -> dict[str, Any]:
    if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), dict) or not isinstance(envelope.get("signature"), str):
        raise ControlPlaneError("节点目录签名响应格式无效")
    try:
        if len(_canonical_json(envelope)) > MAX_DIRECTORY_BYTES:
            raise ControlPlaneError("节点目录响应过大")
    except DirectoryValidationError:
        raise ControlPlaneError("节点目录响应格式无效") from None
    try:
        _key_from_public(public_key).verify(b64url_decode(envelope["signature"]), _canonical_json(envelope["payload"]))
    except (ValueError, TypeError, InvalidSignature, DirectoryValidationError):
        raise ControlPlaneError("节点目录签名验证失败") from None
    payload = validate_directory(envelope["payload"], now=now, allow_expired=allow_expired)
    if not _is_int(minimum_revision) or minimum_revision < 0 or payload["revision"] < minimum_revision:
        raise DirectoryRollbackError("节点目录版本低于订阅要求")
    return payload


def sign_directory(private_key: str | bytes | Ed25519PrivateKey, payload: dict[str, Any]) -> dict[str, Any]:
    key = _key_from_private(private_key)
    validate_directory(payload, now=payload.get("issued_at", int(time.time())), allow_expired=True)
    return {"payload": payload, "signature": b64url_encode(key.sign(_canonical_json(payload)))}


# Public aliases make the service easy to discover from integrations that use
# either the product name or the protocol name.
NodeDirectoryPublisher = DirectoryPublisher
NodeDirectoryService = ControlPlaneService
EnrollmentService = ControlPlaneService
NodeDirectoryStore = ControlPlaneService
