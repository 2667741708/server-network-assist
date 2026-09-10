from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .auth import token_digest


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back a context block, then always release the file handle."""

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS members (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL,
                    public_key TEXT NOT NULL UNIQUE,
                    address TEXT NOT NULL UNIQUE,
                    allowed_ips TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    managed INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    expires_at TEXT,
                    notes TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    csrf_token TEXT NOT NULL,
                    username TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    remote_ip TEXT NOT NULL,
                    user_agent TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    details TEXT NOT NULL,
                    remote_ip TEXT NOT NULL
                );
                """
            )

    def create_session(
        self,
        token: str,
        csrf_token: str,
        username: str,
        expires_at: int,
        remote_ip: str,
        user_agent: str,
    ) -> None:
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    token_digest(token),
                    csrf_token,
                    username,
                    now,
                    expires_at,
                    remote_ip,
                    user_agent[:300],
                ),
            )

    def get_session(self, token: str) -> dict[str, Any] | None:
        now = int(time.time())
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE token_hash = ? AND expires_at >= ?",
                (token_digest(token), now),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token),))

    def add_member(self, payload: dict[str, Any]) -> dict[str, Any]:
        member_id = payload.get("id") or str(uuid.uuid4())
        now = int(time.time())
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO members
                (id, name, owner, role, public_key, address, allowed_ips, enabled,
                 managed, created_at, expires_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    member_id,
                    payload["name"],
                    payload.get("owner", ""),
                    payload.get("role", "member"),
                    payload["public_key"],
                    payload["address"],
                    payload.get("allowed_ips", "10.0.0.0/24"),
                    1,
                    1 if payload.get("managed", True) else 0,
                    now,
                    payload.get("expires_at") or None,
                    payload.get("notes", ""),
                ),
            )
        return self.get_member(member_id) or {}

    def get_member(self, member_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        return dict(row) if row else None

    def member_by_public_key(self, public_key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM members WHERE public_key = ?", (public_key,)
            ).fetchone()
        return dict(row) if row else None

    def list_members(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM members ORDER BY enabled DESC, created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_member_enabled(self, member_id: str, enabled: bool) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE members SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, member_id),
            )

    def used_addresses(self) -> set[str]:
        with self._connect() as db:
            rows = db.execute("SELECT address FROM members").fetchall()
        return {str(row["address"]).split("/", 1)[0] for row in rows}

    def add_audit(
        self,
        actor: str,
        action: str,
        target: str,
        details: dict[str, Any],
        remote_ip: str,
    ) -> None:
        safe_details = {
            key: value
            for key, value in details.items()
            if key.lower() not in {"private_key", "password", "configuration", "config"}
        }
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO audit_events
                (created_at, actor, action, target, details, remote_ip)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(time.time()),
                    actor,
                    action,
                    target,
                    json.dumps(safe_details, ensure_ascii=False),
                    remote_ip,
                ),
            )

    def list_audit(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item["details"])
            result.append(item)
        return result
