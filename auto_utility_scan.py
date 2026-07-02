#!/usr/bin/env python3
"""Watch new pump.fun tokens, score utility projects, and generate full reports."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False

import websockets

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analyze_wallet import build_report, collect, write_csvs, write_markdown  # noqa: E402
from app.config import BASE_DIR, SETTINGS, pumpportal_ws_url  # noqa: E402
from app.helius import HeliusClient  # noqa: E402
from app.project_research import build_project_research, fetch_json_metadata  # noqa: E402


WATCH_DB = BASE_DIR / "data" / "utility_watch.sqlite"
REPORTS_DIR = BASE_DIR / "reports"

SCHEMA = """
CREATE TABLE IF NOT EXISTS utility_tokens (
    mint TEXT PRIMARY KEY,
    name TEXT,
    symbol TEXT,
    creator TEXT,
    uri TEXT,
    discovered_at TEXT NOT NULL,
    score INTEGER,
    verdict TEXT,
    report_path TEXT,
    json_path TEXT,
    status TEXT NOT NULL,
    completed_at TEXT,
    metadata_json TEXT,
    research_json TEXT,
    analysis_json TEXT,
    last_error TEXT
);
"""


@dataclass(slots=True)
class ObservedToken:
    mint: str
    name: str | None
    symbol: str | None
    uri: str | None
    creator: str | None
    created_at: datetime | None
    create_signature: str | None
    initial_buy_sol: float | None
    market_cap_sol: float | None
    pool: str | None
    bonding_curve_key: str | None
    source: str = "pumpportal"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str | None) -> str:
    if not value:
        return "unknown"
    out = []
    for ch in value.lower():
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("-")
    return "".join(out).strip("-") or "unknown"


def _ensure_dirs() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    WATCH_DB.parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(WATCH_DB), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def _init_db() -> None:
    con = _connect()
    try:
        con.executescript(SCHEMA)
        cols = {row["name"] for row in con.execute("PRAGMA table_info(utility_tokens)").fetchall()}
        if "completed_at" not in cols:
            con.execute("ALTER TABLE utility_tokens ADD COLUMN completed_at TEXT")
    finally:
        con.close()


def _upsert_token(con: sqlite3.Connection, token: ObservedToken, *, status: str, score: int | None = None,
                  verdict: str | None = None, report_path: str | None = None, json_path: str | None = None,
                  metadata_json: str | None = None, research_json: str | None = None,
                  analysis_json: str | None = None, last_error: str | None = None,
                  completed_at: str | None = None) -> None:
    completed_at = completed_at or _now()
    con.execute(
        """
        INSERT INTO utility_tokens(
            mint, name, symbol, creator, uri, discovered_at, score, verdict, report_path, json_path,
            status, completed_at, metadata_json, research_json, analysis_json, last_error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mint) DO UPDATE SET
            name=excluded.name,
            symbol=excluded.symbol,
            creator=excluded.creator,
            uri=excluded.uri,
            score=COALESCE(excluded.score, utility_tokens.score),
            verdict=COALESCE(excluded.verdict, utility_tokens.verdict),
            report_path=COALESCE(excluded.report_path, utility_tokens.report_path),
            json_path=COALESCE(excluded.json_path, utility_tokens.json_path),
            status=excluded.status,
            completed_at=COALESCE(excluded.completed_at, utility_tokens.completed_at),
            metadata_json=COALESCE(excluded.metadata_json, utility_tokens.metadata_json),
            research_json=COALESCE(excluded.research_json, utility_tokens.research_json),
            analysis_json=COALESCE(excluded.analysis_json, utility_tokens.analysis_json),
            last_error=excluded.last_error
        """,
        (
            token.mint,
            token.name,
            token.symbol,
            token.creator,
            token.uri,
            _now(),
            score,
            verdict,
            report_path,
            json_path,
            status,
            completed_at,
            metadata_json,
            research_json,
            analysis_json,
            last_error,
        ),
    )


def _already_seen(con: sqlite3.Connection, mint: str) -> bool:
    row = con.execute("SELECT 1 FROM utility_tokens WHERE mint=?", (mint,)).fetchone()
    return row is not None


def _parse_new_token(msg: dict[str, Any]) -> ObservedToken | None:
    mint = msg.get("mint")
    if not mint:
        return None
    return ObservedToken(
        mint=mint,
        name=msg.get("name"),
        symbol=msg.get("symbol"),
        uri=msg.get("uri"),
        creator=msg.get("traderPublicKey"),
        created_at=datetime.now(timezone.utc),
        create_signature=msg.get("signature"),
        initial_buy_sol=msg.get("solAmount"),
        market_cap_sol=msg.get("marketCapSol"),
        pool=msg.get("pool"),
        bonding_curve_key=msg.get("bondingCurveKey"),
    )


def _token_metadata(token: ObservedToken) -> dict[str, Any]:
    meta = fetch_json_metadata(token.uri)
    if meta:
        return meta
    fallback: dict[str, Any] = {}
    if token.name:
        fallback["name"] = token.name
    if token.symbol:
        fallback["symbol"] = token.symbol
    return fallback


def _analysis_for_token(
    helius: HeliusClient,
    token: ObservedToken,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    research = build_project_research(
        mint=token.mint,
        name=token.name,
        symbol=token.symbol,
        uri=token.uri,
        creator=token.creator,
        token_metadata=metadata,
    )

    if not research.contract_found or research.verdict not in {"utility_candidate", "infra_candidate"}:
        return {
            "token": token,
            "research": research,
            "report": None,
        }

    if not token.creator:
        raise RuntimeError("utility candidate found but creator wallet is missing")

    events, profiles, truncated, developer_evidence, launch_time = collect(
        helius,
        token.creator,
        token.mint,
        max_depth=SETTINGS.utility_analysis_depth,
        max_pages=SETTINGS.utility_analysis_pages,
        page_limit=1000,
    )
    report = build_report(
        mint=token.mint,
        developer=token.creator,
        events=events,
        profiles=profiles,
        truncated=truncated,
        developer_evidence=developer_evidence,
        launch_time=launch_time,
    )
    report["project_research"] = research.as_dict()
    report["automation"] = {
        "trigger": "utility_score_threshold",
        "score_threshold": SETTINGS.utility_score_threshold,
        "score": research.score,
        "verdict": research.verdict,
        "utility_signals": research.utility_signals,
        "infra_signals": research.infra_signals,
        "meme_signals": research.meme_signals,
        "contract_found": research.contract_found,
        "contract_evidence": research.contract_evidence,
        "generated_at": _now(),
    }
    return {"token": token, "research": research, "report": report}


def _write_report(token: ObservedToken, report: dict[str, Any], research: Any) -> tuple[Path, Path]:
    stem = f"{_slug(token.symbol or token.name or token.mint[:8])}_{token.mint[:8]}"
    md_path = REPORTS_DIR / f"{stem}.md"
    json_path = REPORTS_DIR / f"{stem}.json"
    write_markdown(report, md_path)
    write_csvs(report, REPORTS_DIR)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (REPORTS_DIR / "latest.md").write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    (REPORTS_DIR / "latest.json").write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(
        f"[report] {token.symbol or token.name or token.mint[:8]} "
        f"score={research.score} verdict={research.verdict} -> {md_path.name}"
    )
    return md_path, json_path


async def _handle_token(helius: HeliusClient, con: sqlite3.Connection, token: ObservedToken) -> None:
    if _already_seen(con, token.mint):
        return
    metadata = _token_metadata(token)
    try:
        result = await asyncio.to_thread(_analysis_for_token, helius, token, metadata)
        research = result["research"]
        report = result["report"]
        if report is None:
            print(
                f"[skip] {token.symbol or token.name or token.mint[:8]} "
                f"score={research.score} verdict={research.verdict}"
            )
            return
        md_path, json_path = _write_report(token, report, research)
        _upsert_token(
            con,
            token,
            status="reported",
            score=research.score,
            verdict=research.verdict,
            report_path=str(md_path),
            json_path=str(json_path),
            metadata_json=json.dumps(metadata),
            research_json=json.dumps(research.as_dict()),
            analysis_json=json.dumps(report),
            completed_at=_now(),
        )
    except Exception as exc:
        _upsert_token(
            con,
            token,
            status="failed",
            metadata_json=json.dumps(metadata),
            last_error=str(exc),
            completed_at=_now(),
        )
        print(f"[error] {token.mint}: {str(exc)[:140]}")


async def _stream_new_tokens(helius: HeliusClient, con: sqlite3.Connection) -> None:
    uri = pumpportal_ws_url()
    print(f"[watch] connecting to {uri}")
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("txType") != "create":
                        continue
                    token = _parse_new_token(msg)
                    if not token:
                        continue
                    await _handle_token(helius, con, token)
        except Exception as exc:
            print(f"[watch] reconnect after error: {str(exc)[:120]}")
            await asyncio.sleep(3.0)


def main() -> int:
    load_dotenv(BASE_DIR / ".env")
    _ensure_dirs()
    _init_db()

    parser = argparse.ArgumentParser(description="Watch new tokens and auto-generate utility-project reports.")
    parser.add_argument("--watch", action="store_true", help="Keep watching PumpPortal for new launches.")
    args = parser.parse_args()

    helius = HeliusClient()
    if not helius.configured:
        raise SystemExit("HELIUS_API_KEY is not configured")

    con = _connect()
    try:
        if not args.watch:
            args.watch = True
        asyncio.run(_stream_new_tokens(helius, con))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
