"""Read-only dashboard helpers for scraped tokens."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import BASE_DIR

WATCH_DB = BASE_DIR / "data" / "utility_watch.sqlite"
REPORTS_DIR = BASE_DIR / "reports"


def _connect() -> sqlite3.Connection | None:
    if not WATCH_DB.exists():
        return None
    con = sqlite3.connect(str(WATCH_DB), timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _safe_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        obj = json.loads(value)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def list_tokens(limit: int = 200) -> list[dict[str, Any]]:
    con = _connect()
    if con is None:
        return []
    try:
        rows = con.execute(
            """
            SELECT mint, name, symbol, creator, uri, discovered_at, score, verdict,
                   report_path, json_path, status, completed_at, metadata_json, research_json, analysis_json, last_error
            FROM utility_tokens
            ORDER BY discovered_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        tokens: list[dict[str, Any]] = []
        for row in rows:
            research = _safe_json(row["research_json"])
            analysis = _safe_json(row["analysis_json"])
            socials = research.get("socials") or {}
            report_name = Path(row["report_path"]).name if row["report_path"] else None
            tokens.append(
                {
                    "mint": row["mint"],
                    "name": row["name"],
                    "symbol": row["symbol"],
                    "creator": row["creator"],
                    "uri": row["uri"],
                    "discovered_at": row["discovered_at"],
                    "score": row["score"],
                    "verdict": row["verdict"],
                    "status": row["status"],
                    "completed_at": row["completed_at"],
                    "report_path": row["report_path"],
                    "report_url": f"/reports/{report_name}" if report_name else None,
                    "json_path": row["json_path"],
                    "last_error": row["last_error"],
                    "website": socials.get("website"),
                    "twitter": socials.get("twitter"),
                    "telegram": socials.get("telegram"),
                    "useful_links": research.get("useful_links") or [],
                    "automation": analysis.get("automation") or {},
                    "summary": analysis.get("summary") or {},
                }
            )
        return tokens
    finally:
        con.close()


def get_token(mint: str) -> dict[str, Any] | None:
    con = _connect()
    if con is None:
        return None
    try:
        row = con.execute(
            """
            SELECT mint, name, symbol, creator, uri, discovered_at, score, verdict,
                   report_path, json_path, status, completed_at, metadata_json, research_json, analysis_json, last_error
            FROM utility_tokens
            WHERE mint=?
            """,
            (mint,),
        ).fetchone()
        if not row:
            return None
        research = _safe_json(row["research_json"])
        analysis = _safe_json(row["analysis_json"])
        metadata = _safe_json(row["metadata_json"])
        report_text = None
        report_path = row["report_path"]
        report_name = Path(report_path).name if report_path else None
        if report_path:
            path = Path(report_path)
            if not path.is_absolute():
                path = BASE_DIR / report_path
            if path.exists():
                report_text = path.read_text(encoding="utf-8")
        return {
            "mint": row["mint"],
            "name": row["name"],
            "symbol": row["symbol"],
            "creator": row["creator"],
            "uri": row["uri"],
            "discovered_at": row["discovered_at"],
            "score": row["score"],
            "verdict": row["verdict"],
            "status": row["status"],
            "completed_at": row["completed_at"],
            "report_path": row["report_path"],
            "report_url": f"/reports/{report_name}" if report_name else None,
            "json_path": row["json_path"],
            "last_error": row["last_error"],
            "metadata": metadata,
            "research": research,
            "analysis": analysis,
            "report_text": report_text,
        }
    finally:
        con.close()
