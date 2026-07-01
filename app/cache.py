"""SQLite cache and investigation store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    cache_key TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS investigations (
    investigation_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class CacheStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._lock:
            con = self._conn()
            try:
                con.executescript(SCHEMA)
            finally:
                con.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def cache_key(request_payload: dict[str, Any], version: str) -> str:
        raw = json.dumps({"version": version, "request": request_payload}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_analysis(self, cache_key: str) -> dict[str, Any] | None:
        with self._lock:
            con = self._conn()
            try:
                row = con.execute("SELECT result_json, created_at FROM analyses WHERE cache_key=?", (cache_key,)).fetchone()
                if not row:
                    return None
                created_at = datetime.fromisoformat(row["created_at"])
                if (datetime.now(timezone.utc) - created_at).total_seconds() > SETTINGS.cache_ttl_seconds:
                    con.execute("DELETE FROM analyses WHERE cache_key=?", (cache_key,))
                    return None
                return json.loads(row["result_json"])
            finally:
                con.close()

    def put_analysis(self, cache_key: str, request_payload: dict[str, Any], result: dict[str, Any]) -> None:
        with self._lock:
            con = self._conn()
            try:
                now = self._now()
                con.execute(
                    """
                    INSERT INTO analyses(cache_key, request_json, result_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        request_json=excluded.request_json,
                        result_json=excluded.result_json,
                        updated_at=excluded.updated_at
                    """,
                    (cache_key, json.dumps(request_payload), json.dumps(result), now, now),
                )
            finally:
                con.close()

    def create_investigation(self, investigation_id: str, request_payload: dict[str, Any]) -> None:
        with self._lock:
            con = self._conn()
            try:
                now = self._now()
                con.execute(
                    """
                    INSERT INTO investigations(investigation_id, status, request_json, result_json, error, created_at, updated_at)
                    VALUES (?, 'queued', ?, NULL, NULL, ?, ?)
                    """,
                    (investigation_id, json.dumps(request_payload), now, now),
                )
            finally:
                con.close()

    def update_investigation(
        self,
        investigation_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            con = self._conn()
            try:
                now = self._now()
                con.execute(
                    """
                    UPDATE investigations
                    SET status=?, result_json=?, error=?, updated_at=?
                    WHERE investigation_id=?
                    """,
                    (status, json.dumps(result) if result is not None else None, error, now, investigation_id),
                )
            finally:
                con.close()

    def get_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        with self._lock:
            con = self._conn()
            try:
                row = con.execute(
                    "SELECT * FROM investigations WHERE investigation_id=?", (investigation_id,)
                ).fetchone()
                if not row:
                    return None
                request = json.loads(row["request_json"])
                result = json.loads(row["result_json"]) if row["result_json"] else None
                return {
                    "investigation_id": row["investigation_id"],
                    "status": row["status"],
                    "request": request,
                    "result": result,
                    "error": row["error"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            finally:
                con.close()
