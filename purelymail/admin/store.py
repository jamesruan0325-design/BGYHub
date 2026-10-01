"""Local SQLite store for the mailbox admin.

Mailbox passwords are encrypted (admin/crypto.py: HMAC-SHA256 encrypt-then-MAC,
standard library only) with a random 256-bit data key. The data key itself is
only stored wrapped (encrypted) with a key derived from the admin password
(PBKDF2-HMAC-SHA256, 600k iterations), so the database file alone reveals nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from admin import crypto

KEY_AAD = b"bgyhub-data-key"
MIN_ADMIN_PASSWORD = 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mailboxes (
    email TEXT PRIMARY KEY,
    source TEXT NOT NULL,            -- created | imported
    status TEXT NOT NULL,            -- pending | active | failed | unknown
    password_enc BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS excel_pending (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    kind TEXT NOT NULL,              -- create | import | reset
    date TEXT,
    queued_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary TEXT NOT NULL            -- JSON, never contains passwords
);
CREATE TABLE IF NOT EXISTS batch_items (
    batch_id TEXT NOT NULL,
    email TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL,
    PRIMARY KEY (batch_id, email)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
        os.chmod(self.db_path, 0o600)
        self._check_format()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ meta / settings

    def _meta(self, key: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_setting(self, key: str, default=None):
        raw = self._meta("setting:" + key)
        return json.loads(raw) if raw is not None else default

    def set_setting(self, key: str, value) -> None:
        self._set_meta("setting:" + key, json.dumps(value))

    # ------------------------------------------------------------------ admin password / data key

    def is_initialized(self) -> bool:
        return self._meta("wrapped_key_v2") is not None

    def _check_format(self) -> None:
        if self._meta("wrapped_key") is not None and not self.is_initialized():
            raise RuntimeError(f"{self.db_path} was created by an older test version of this app. "
                               "Move it out of the data folder and start again.")

    def initialize(self, password: str) -> bytes:
        if self.is_initialized():
            raise RuntimeError("already initialized")
        if len(password) < MIN_ADMIN_PASSWORD:
            raise ValueError(f"admin password must be at least {MIN_ADMIN_PASSWORD} characters")
        salt, data_key = os.urandom(16), crypto.new_key()
        iterations = crypto.PBKDF2_ITERATIONS
        wrapped = crypto.encrypt(crypto.derive_key(password, salt, iterations), data_key, KEY_AAD)
        self._set_meta("kdf_salt_v2", salt.hex())
        self._set_meta("kdf_iterations_v2", str(iterations))
        self._set_meta("wrapped_key_v2", wrapped.hex())
        return data_key

    def unlock(self, password: str) -> bytes | None:
        """Return the data key, or None if the password is wrong."""
        salt, blob = self._meta("kdf_salt_v2"), self._meta("wrapped_key_v2")
        if not salt or not blob:
            return None
        iterations = int(self._meta("kdf_iterations_v2") or crypto.PBKDF2_ITERATIONS)
        try:
            return crypto.decrypt(crypto.derive_key(password, bytes.fromhex(salt), iterations),
                                  bytes.fromhex(blob), KEY_AAD)
        except crypto.DecryptionError:
            return None

    @staticmethod
    def _encrypt(key: bytes, email: str, password: str) -> bytes:
        return crypto.encrypt(key, password.encode(), email.encode())

    @staticmethod
    def _decrypt(key: bytes, email: str, blob: bytes) -> str:
        return crypto.decrypt(key, blob, email.encode()).decode()

    # ------------------------------------------------------------------ mailboxes

    def save_mailbox(self, key: bytes, email: str, source: str, status: str, password: str,
                     created_at: str | None = None) -> None:
        ts = now()
        enc = self._encrypt(key, email, password)
        with self._conn() as c:
            c.execute(
                "INSERT INTO mailboxes(email, source, status, password_enc, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET source=excluded.source, "
                "status=excluded.status, password_enc=excluded.password_enc, updated_at=excluded.updated_at",
                (email, source, status, enc, created_at or ts, ts),
            )

    def set_mailbox_status(self, email: str, status: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE mailboxes SET status=?, updated_at=? WHERE email=?", (status, now(), email))

    def set_mailbox_password(self, key: bytes, email: str, password: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE mailboxes SET password_enc=?, updated_at=? WHERE email=?",
                      (self._encrypt(key, email, password), now(), email))

    def get_mailbox(self, email: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT email, source, status, created_at, updated_at FROM mailboxes WHERE email=?",
                            (email,)).fetchone()
        return dict(row) if row else None

    def list_mailboxes(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT email, source, status, created_at, updated_at FROM mailboxes "
                             "ORDER BY email").fetchall()
        return [dict(r) for r in rows]

    def get_password(self, key: bytes, email: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT password_enc FROM mailboxes WHERE email=?", (email,)).fetchone()
        if not row or row["password_enc"] is None:
            return None
        return self._decrypt(key, email, row["password_enc"])

    # ------------------------------------------------------------------ Excel sync queue

    def queue_excel(self, email: str, kind: str, date: str | None) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO excel_pending(email, kind, date, queued_at) VALUES(?,?,?,?)",
                      (email, kind, date, now()))

    def pending_excel(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM excel_pending ORDER BY id").fetchall()]

    def clear_excel(self, ids: list[int]) -> None:
        with self._conn() as c:
            c.executemany("DELETE FROM excel_pending WHERE id=?", [(i,) for i in ids])

    # ------------------------------------------------------------------ batches

    def save_batch(self, batch_id: str, started_at: str, finished_at: str | None, summary: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO batches(id, started_at, finished_at, summary) VALUES(?,?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET finished_at=excluded.finished_at, summary=excluded.summary",
                      (batch_id, started_at, finished_at, json.dumps(summary)))

    def save_batch_item(self, batch_id: str, email: str, status: str, detail: str = "") -> None:
        with self._conn() as c:
            c.execute("INSERT INTO batch_items(batch_id, email, status, detail, at) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(batch_id, email) DO UPDATE SET status=excluded.status, "
                      "detail=excluded.detail, at=excluded.at", (batch_id, email, status, detail, now()))

    def batch_items(self, batch_id: str) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT email, status, detail, at FROM batch_items WHERE batch_id=? ORDER BY email",
                (batch_id,)).fetchall()]

    def get_batch(self, batch_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "started_at": row["started_at"], "finished_at": row["finished_at"],
                **json.loads(row["summary"])}

    # ------------------------------------------------------------------ audit

    def audit(self, action: str, result: str, email: str = "", detail: str = "") -> None:
        with self._conn() as c:
            c.execute("INSERT INTO audit(at, action, email, result, detail) VALUES(?,?,?,?,?)",
                      (now(), action, email, result, detail))

    def recent_audit(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT at, action, email, result, detail FROM audit ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()]
