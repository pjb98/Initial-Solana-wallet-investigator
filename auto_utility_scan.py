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
from urllib.parse import quote
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False

import websockets
import requests

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analyze_wallet import write_csvs, write_markdown  # noqa: E402
from app.config import BASE_DIR, SETTINGS, pumpportal_ws_url  # noqa: E402
from app.project_research import build_project_research, fetch_json_metadata  # noqa: E402
from app.ricomaps import RicoMapsClient, build_ricomaps_report  # noqa: E402


WATCH_DB = BASE_DIR / "data" / "utility_watch.sqlite"
REPORTS_DIR = BASE_DIR / "reports"
_HTTP = requests.Session()
_HTTP.headers.update({"User-Agent": "solana-wallet-investigator/0.1"})

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
    ricomaps: RicoMapsClient,
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
        launch_time=token.created_at.isoformat() if token.created_at else None,
    )

    v2_signal = (research.score_breakdown or {}).get("v2") or {}
    v2_eligible = bool(v2_signal.get("eligible"))
    main_eligible = research.verdict in {"utility_candidate", "infra_candidate"}

    if not research.contract_found or not (main_eligible or v2_eligible):
        return {
            "token": token,
            "research": research,
            "report": None,
            "main_eligible": main_eligible,
            "v2_eligible": v2_eligible,
        }

    if not ricomaps.configured:
        raise RuntimeError("RICOMAPS_API_KEY is not configured")

    report = build_ricomaps_report(
        request={
            "developer_wallet": token.creator,
            "token_mint": token.mint,
            "max_side_wallet_depth": SETTINGS.utility_analysis_depth,
        },
        payload=ricomaps.analyze(token.mint),
        research=research.as_dict(),
        token_metadata=metadata,
    )
    breakdown = research.score_breakdown or {}
    report["project_research"] = research.as_dict()
    report["automation"] = {
        "trigger": "utility_score_threshold" if main_eligible else "v2_contract_docs_github_tweet",
        "score_threshold": SETTINGS.utility_score_threshold,
        "score": research.score,
        "verdict": research.verdict,
        "alert_tier": _alert_tier(research),
        "score_breakdown": breakdown,
        "v2": v2_signal,
        "utility_signals": research.utility_signals,
        "infra_signals": research.infra_signals,
        "meme_signals": research.meme_signals,
        "contract_found": research.contract_found,
        "contract_evidence": research.contract_evidence,
        "generated_at": _now(),
    }
    return {
        "token": token,
        "research": research,
        "report": report,
        "main_eligible": main_eligible,
        "v2_eligible": v2_eligible,
    }


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


def _discord_color(verdict: str, tier: str | None = None) -> int:
    if tier == "Urgent Risk":
        return 0xED4245
    if tier == "Review":
        return 0x1F8B4C
    if tier == "Watch":
        return 0xFEE75C
    if verdict == "infra_candidate":
        return 0x57F287
    if verdict == "utility_candidate":
        return 0x1F8B4C
    if verdict == "possible_utility":
        return 0xFEE75C
    return 0x5865F2


def _alert_tier(research: Any) -> str:
    breakdown = getattr(research, "score_breakdown", {}) or {}
    tier = breakdown.get("alert_tier")
    if isinstance(tier, str) and tier:
        return tier
    verdict = str(getattr(research, "verdict", "") or "")
    if verdict in {"utility_candidate", "infra_candidate"}:
        return "Watch"
    return "Watch"


def _reason_summary(reasons: list[str], limit: int = 4, max_chars: int = 900) -> str:
    if not reasons:
        return "No detailed reasons were recorded."
    selected: list[str] = []
    total = 0
    for reason in reasons:
        piece = f"• {reason}"
        if selected and len(selected) >= limit:
            break
        if total + len(piece) > max_chars:
            break
        selected.append(piece)
        total += len(piece) + 1
    return "\n".join(selected)


def _send_discord_alert(
    token: ObservedToken,
    research: Any,
    report: dict[str, Any],
    md_path: Path,
    *,
    version_label: str | None = None,
) -> None:
    webhook_url = SETTINGS.discord_webhook_url
    if not webhook_url:
        return

    reasons = list(getattr(research, "reasons", []) or [])
    breakdown = getattr(research, "score_breakdown", {}) or {}
    tier = _alert_tier(research)
    socials = (research.as_dict() if hasattr(research, "as_dict") else {}).get("socials", {})
    title_prefix = f"{version_label} " if version_label else ""
    fields = [
        {"name": "Mint", "value": token.mint, "inline": False},
        {"name": "Verdict", "value": str(getattr(research, "verdict", "unknown")), "inline": True},
        {"name": "Score", "value": str(getattr(research, "score", 0)), "inline": True},
        {"name": "Alert Tier", "value": tier, "inline": True},
        {"name": "Why It Passed", "value": _reason_summary(reasons), "inline": False},
    ]
    if breakdown:
        fields.extend(
            [
                {"name": "Project Relevance", "value": str(breakdown.get("project_relevance", "")), "inline": True},
                {"name": "Evidence Quality", "value": str(breakdown.get("evidence_quality", "")), "inline": True},
                {"name": "Execution", "value": str(breakdown.get("execution_score", "")), "inline": True},
                {"name": "Market Risk", "value": str(breakdown.get("market_risk", "")), "inline": True},
                {"name": "Analysis Confidence", "value": str(breakdown.get("analysis_confidence", "")), "inline": True},
            ]
        )
    contract_evidence = getattr(research, "contract_evidence", None)
    if contract_evidence:
        fields.append({"name": "Contract Evidence", "value": str(contract_evidence), "inline": False})
    v2_signal = breakdown.get("v2") or {}
    if version_label or v2_signal.get("eligible"):
        fields.append({"name": "Classification Version", "value": version_label or "current", "inline": True})
    if v2_signal.get("eligible"):
        if v2_signal.get("label"):
            fields.append({"name": "V2 Label", "value": str(v2_signal.get("label")), "inline": False})
        fields.append({"name": "V2 Evidence", "value": ", ".join(v2_signal.get("evidence_sources") or []) or "present", "inline": True})
    for label, key in (("Website", "website"), ("Twitter", "twitter"), ("Telegram", "telegram")):
        value = socials.get(key) if isinstance(socials, dict) else None
        if value:
            fields.append({"name": label, "value": str(value), "inline": False})
    trojan_template = SETTINGS.trojan_terminal_url_template
    if trojan_template:
        trojan_url = trojan_template.format(mint=quote(token.mint, safe=""))
        fields.append({"name": "Trojan", "value": f"[Open Trojan]({trojan_url})", "inline": False})
    fields.append({"name": "Report", "value": md_path.name, "inline": False})

    payload = {
        "username": "Solana Investigator",
        "embeds": [
            {
                "title": f"{title_prefix}{tier} - {token.symbol or token.name or token.mint[:8]} alert",
                "description": f"Completed {getattr(research, 'verdict', 'unknown')} analysis for a qualifying token.",
                "color": _discord_color(str(getattr(research, "verdict", "")), tier=tier),
                "fields": fields,
                "footer": {"text": "Solana wallet investigator"},
            }
        ],
    }
    try:
        resp = _HTTP.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[discord] alert failed for {token.mint}: {str(exc)[:180]}")


def _is_expected_url_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        needle in message
        for needle in (
            "invalid ipv6 url",
            "invalid url",
            "missing schema",
            "no connection adapters",
            "failed to parse",
        )
    )


async def _handle_token(ricomaps: RicoMapsClient, con: sqlite3.Connection, token: ObservedToken) -> None:
    if _already_seen(con, token.mint):
        return
    metadata = _token_metadata(token)
    try:
        result = await asyncio.to_thread(_analysis_for_token, ricomaps, token, metadata)
        research = result["research"]
        report = result["report"]
        main_eligible = bool(result.get("main_eligible"))
        v2_eligible = bool(result.get("v2_eligible"))
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
        if main_eligible:
            _send_discord_alert(token, research, report, md_path)
        if v2_eligible:
            _send_discord_alert(token, research, report, md_path, version_label="v2")
    except Exception as exc:
        if _is_expected_url_error(exc):
            print(f"[skip] {token.symbol or token.name or token.mint[:8]} invalid project url: {str(exc)[:140]}")
            return
        _upsert_token(
            con,
            token,
            status="failed",
            metadata_json=json.dumps(metadata),
            last_error=str(exc),
            completed_at=_now(),
        )
        print(f"[error] {token.mint}: {str(exc)[:140]}")


async def _stream_new_tokens(ricomaps: RicoMapsClient, con: sqlite3.Connection) -> None:
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
                    await _handle_token(ricomaps, con, token)
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

    ricomaps = RicoMapsClient()
    if not ricomaps.configured:
        raise SystemExit("RICOMAPS_API_KEY is not configured")

    con = _connect()
    try:
        if not args.watch:
            args.watch = True
        asyncio.run(_stream_new_tokens(ricomaps, con))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
