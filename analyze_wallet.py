#!/usr/bin/env python3
"""Deterministic Solana developer-wallet investigation CLI."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.helius import HeliusClient, SignatureInfo  # noqa: E402
from app.config import SETTINGS  # noqa: E402
from app.ricomaps import RicoMapsClient, build_ricomaps_report  # noqa: E402


PUBKEY_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
USDC_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
}
SOL_MINT = "So11111111111111111111111111111111111111112"
TRANSFER_OK_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "MemoSq4gq4Sxq4gq4qSxq4gq4Sxq4gq4qg4qg4q",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
}

VAULT_PROGRAM_HINTS = (
    "vest",
    "vault",
    "lock",
    "locker",
    "escrow",
    "stream",
    "cliff",
)


def is_pubkey(value: str | None) -> bool:
    return bool(value and PUBKEY_RE.match(value))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    (ROOT / "reports").mkdir(parents=True, exist_ok=True)
    (ROOT / "inputs").mkdir(parents=True, exist_ok=True)


def account_keys(tx: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key in (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []):
        if isinstance(key, dict):
            pk = key.get("pubkey")
        else:
            pk = key
        if pk:
            keys.append(pk)
    return keys


def program_ids(tx: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    msg = (tx.get("transaction") or {}).get("message") or {}
    for ix in msg.get("instructions") or []:
        pid = ix.get("programId")
        if pid:
            ids.add(pid)
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        for ix in inner.get("instructions") or []:
            pid = ix.get("programId")
            if pid:
                ids.add(pid)
    return ids


def transfer_only(tx: dict[str, Any]) -> bool:
    return program_ids(tx).issubset(TRANSFER_OK_PROGRAMS)


def vault_programs(tx: dict[str, Any]) -> set[str]:
    hits: set[str] = set()
    for pid in program_ids(tx):
        low = pid.lower()
        if any(hint in low for hint in VAULT_PROGRAM_HINTS):
            hits.add(pid)
    return hits


def looks_like_vault_deposit(tx: dict[str, Any], transfers: list[dict[str, Any]], owner_map: dict[str, str], mint: str) -> bool:
    if vault_programs(tx):
        return True
    if not transfers:
        return False
    for transfer in transfers:
        if transfer["kind"] != "spl_transfer" or transfer["mint"] != mint:
            continue
        destination = transfer.get("destination")
        if not destination:
            continue
        owner = owner_map.get(destination)
        if not owner:
            return True
        if not is_pubkey(owner):
            return True
        if owner.startswith("111111") or owner.startswith("AToken"):
            return True
    return False


def parsed_token_transfers(tx: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    msg = ((tx.get("transaction") or {}).get("message") or {})
    for ix in msg.get("instructions") or []:
        parsed = ix.get("parsed")
        if not isinstance(parsed, dict):
            continue
        info = parsed.get("info") or {}
        itype = parsed.get("type")
        if itype in {"transfer", "transferChecked"}:
            mint = info.get("mint")
            if mint:
                out.append(
                    {
                        "kind": "spl_transfer",
                        "mint": mint,
                        "source": info.get("source"),
                        "destination": info.get("destination"),
                        "authority": info.get("authority"),
                        "amount": float(info.get("tokenAmount", {}).get("uiAmount") or info.get("amount") or 0),
                    }
                )
        if itype in {"transfer", "transferChecked"} and info.get("lamports") is not None:
            out.append(
                {
                    "kind": "sol_transfer",
                    "mint": SOL_MINT,
                    "source": info.get("source"),
                    "destination": info.get("destination"),
                    "authority": info.get("authority"),
                    "amount": float(info.get("lamports") or 0) / 1_000_000_000,
                }
            )
    return out


def token_account_owner_map(tx: dict[str, Any], mint: str) -> dict[str, str]:
    keys = account_keys(tx)
    owners: dict[str, str] = {}
    meta = tx.get("meta") or {}
    for bucket in (meta.get("preTokenBalances") or [], meta.get("postTokenBalances") or []):
        for row in bucket:
            if row.get("mint") != mint:
                continue
            owner = row.get("owner")
            if not owner:
                continue
            account = row.get("tokenAccount") or row.get("address")
            idx = row.get("accountIndex")
            if not account and isinstance(idx, int) and 0 <= idx < len(keys):
                account = keys[idx]
            if account:
                owners[str(account)] = str(owner)
    return owners


def tx_time(tx: dict[str, Any]) -> datetime | None:
    bt = tx.get("blockTime")
    return datetime.fromtimestamp(bt, tz=timezone.utc) if bt else None


def owner_token_deltas(tx: dict[str, Any], mint: str) -> dict[str, float]:
    meta = tx.get("meta") or {}
    pre: dict[str, float] = defaultdict(float)
    post: dict[str, float] = defaultdict(float)
    for bucket, target in ((meta.get("preTokenBalances") or [], pre), (meta.get("postTokenBalances") or [], post)):
        for row in bucket:
            if row.get("mint") != mint:
                continue
            owner = row.get("owner")
            if not owner:
                continue
            amt = ((row.get("uiTokenAmount") or {}).get("uiAmount"))
            target[owner] += float(amt or 0.0)
    owners = set(pre) | set(post)
    return {owner: post.get(owner, 0.0) - pre.get(owner, 0.0) for owner in owners}


def token_mints_in_tx(tx: dict[str, Any]) -> set[str]:
    mints: set[str] = set()
    meta = tx.get("meta") or {}
    for row in (meta.get("postTokenBalances") or []) + (meta.get("preTokenBalances") or []):
        mint = row.get("mint")
        if mint:
            mints.add(mint)
    return mints


def native_sol_deltas(tx: dict[str, Any]) -> dict[str, float]:
    keys = account_keys(tx)
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    out: dict[str, float] = {}
    for i, key in enumerate(keys):
        if i < len(pre) and i < len(post):
            out[key] = (post[i] - pre[i]) / 1_000_000_000
    return out


def parsed_token_transfers(tx: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    msg = ((tx.get("transaction") or {}).get("message") or {})
    for ix in msg.get("instructions") or []:
        parsed = ix.get("parsed")
        if not isinstance(parsed, dict):
            continue
        info = parsed.get("info") or {}
        itype = parsed.get("type")
        if itype in {"transfer", "transferChecked"}:
            mint = info.get("mint")
            if mint:
                out.append(
                    {
                        "kind": "spl_transfer",
                        "mint": mint,
                        "source": info.get("source"),
                        "destination": info.get("destination"),
                        "authority": info.get("authority"),
                        "amount": float(info.get("tokenAmount", {}).get("uiAmount") or info.get("amount") or 0),
                    }
                )
        if itype in {"transfer", "transferChecked"} and info.get("lamports") is not None:
            out.append(
                {
                    "kind": "sol_transfer",
                    "mint": SOL_MINT,
                    "source": info.get("source"),
                    "destination": info.get("destination"),
                    "authority": info.get("authority"),
                    "amount": float(info.get("lamports") or 0) / 1_000_000_000,
                }
            )
    return out


def first_sender_funder(tx: dict[str, Any], wallet: str) -> str | None:
    msg = ((tx.get("transaction") or {}).get("message") or {})
    meta = tx.get("meta") or {}
    for ix in msg.get("instructions") or []:
        parsed = ix.get("parsed")
        if isinstance(parsed, dict) and parsed.get("type") in {"transfer", "transferChecked"}:
            info = parsed.get("info") or {}
            if info.get("destination") == wallet and info.get("source"):
                return info["source"]
    keys = account_keys(tx)
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if wallet in keys:
        idx = keys.index(wallet)
        if idx < len(pre) and idx < len(post):
            sender = None
            largest_out = 0
            for i, key in enumerate(keys):
                if key == wallet or i >= len(pre) or i >= len(post):
                    continue
                delta = pre[i] - post[i]
                if delta > largest_out:
                    largest_out = delta
                    sender = key
            return sender
    return None


def transfer_counterparty(wallet: str, token_delta: float, transfers: list[dict[str, Any]], mint: str, owner_map: dict[str, str]) -> str | None:
    if token_delta < 0:
        for transfer in transfers:
            if transfer["kind"] == "spl_transfer" and transfer["mint"] == mint:
                destination = transfer.get("destination")
                if destination and destination != wallet:
                    return owner_map.get(destination, destination)
    elif token_delta > 0:
        for transfer in transfers:
            if transfer["kind"] == "spl_transfer" and transfer["mint"] == mint:
                source = transfer.get("source")
                if source and source != wallet:
                    return owner_map.get(source, source)
    return None


@dataclass
class Event:
    wallet: str
    mint: str
    signature: str
    kind: str
    timestamp: str | None
    token_amount: float = 0.0
    sol_amount: float = 0.0
    asset: str | None = None
    counterparty: str | None = None
    proceeds_destination: str | None = None
    relationship_confidence: str = "low"


@dataclass
class WalletProfile:
    wallet: str
    level: int
    funder: str | None = None
    funder_evidence: str | None = None
    first_seen: str | None = None
    tx_count: int = 0
    received_from_developer: bool = False
    sold_token: bool = False


def paginate_history(helius: HeliusClient, address: str, max_pages: int | None = None, page_limit: int | None = None) -> tuple[list[SignatureInfo], bool]:
    pages = max_pages or SETTINGS.max_pages
    limit = page_limit or SETTINGS.page_limit
    all_sigs: list[SignatureInfo] = []
    before: str | None = None
    truncated = False
    for _ in range(pages):
        batch = helius.get_signatures_for_address(address, before=before, limit=limit)
        if not batch:
            break
        all_sigs.extend(batch)
        before = batch[-1].signature
        if len(batch) < limit:
            break
    else:
        truncated = True
    if len(all_sigs) >= SETTINGS.truncation_signature_cap:
        truncated = True
    return all_sigs, truncated


def load_tx(helius: HeliusClient, signature: str) -> dict[str, Any] | None:
    return helius.get_transaction(signature)


def trace_wallet(
    helius: HeliusClient,
    wallet: str,
    mint: str,
    *,
    level: int,
    max_pages: int,
    page_limit: int,
    stop_at_time: int | None = None,
) -> tuple[list[Event], list[str], bool, WalletProfile]:
    sigs, truncated = paginate_history(helius, wallet, max_pages=max_pages, page_limit=page_limit)
    events: list[Event] = []
    discovered_wallets: list[str] = []
    profile = WalletProfile(wallet=wallet, level=level)

    for sig in sigs:
        if sig.err:
            continue
        tx = load_tx(helius, sig.signature)
        if not tx or not tx.get("meta"):
            continue
        if stop_at_time is not None and tx.get("blockTime") is not None and tx["blockTime"] < stop_at_time:
            continue
        tstamp = tx_time(tx)
        tstamp_iso = tstamp.isoformat() if tstamp else None

        # Funding evidence from the wallet's first observed transaction.
        if profile.first_seen is None and tstamp_iso:
            profile.first_seen = tstamp_iso
            profile.funder = first_sender_funder(tx, wallet)
            if profile.funder:
                profile.funder_evidence = sig.signature

        deltas = owner_token_deltas(tx, mint)
        if wallet not in deltas:
            continue
        sol_deltas = native_sol_deltas(tx)
        token_delta = deltas[wallet]
        sol_delta = sol_deltas.get(wallet, 0.0)
        parsed_transfers = parsed_token_transfers(tx)

        if token_delta > 0 and sol_delta < 0:
            events.append(
                Event(
                    wallet=wallet,
                    mint=mint,
                    signature=sig.signature,
                    kind="buy",
                    timestamp=tstamp_iso,
                    token_amount=token_delta,
                    sol_amount=abs(sol_delta),
                    relationship_confidence="high" if wallet == profile.wallet else "medium",
                )
            )
        elif token_delta < 0 and sol_delta > 0:
            events.append(
                Event(
                    wallet=wallet,
                    mint=mint,
                    signature=sig.signature,
                    kind="sell",
                    timestamp=tstamp_iso,
                    token_amount=abs(token_delta),
                    sol_amount=sol_delta,
                    asset="SOL",
                    relationship_confidence="high",
                )
            )
            profile.sold_token = True
        elif token_delta < 0 and transfer_only(tx):
            recipient = None
            for transfer in parsed_transfers:
                if transfer["kind"] == "spl_transfer" and transfer["mint"] == mint and transfer.get("source"):
                    recipient = transfer.get("destination")
                    if recipient and recipient != wallet:
                        discovered_wallets.append(recipient)
            events.append(
                Event(
                    wallet=wallet,
                    mint=mint,
                    signature=sig.signature,
                    kind="transfer_out",
                    timestamp=tstamp_iso,
                    token_amount=abs(token_delta),
                    counterparty=recipient,
                    relationship_confidence="high" if recipient else "medium",
                )
            )
            if recipient and recipient != wallet:
                discovered_wallets.append(recipient)
        elif token_delta > 0 and transfer_only(tx):
            sender = None
            for transfer in parsed_transfers:
                if transfer["kind"] == "spl_transfer" and transfer["mint"] == mint and transfer.get("destination"):
                    sender = transfer.get("source")
                    if sender and sender != wallet:
                        discovered_wallets.append(sender)
            events.append(
                Event(
                    wallet=wallet,
                    mint=mint,
                    signature=sig.signature,
                    kind="transfer_in",
                    timestamp=tstamp_iso,
                    token_amount=token_delta,
                    counterparty=sender,
                    relationship_confidence="high" if sender else "medium",
                )
            )
            if sender and sender != wallet:
                discovered_wallets.append(sender)

        # Track proceeds moving after a sale.
        if sol_delta > 0 and token_delta < 0:
            for transfer in parsed_transfers:
                if transfer["kind"] == "sol_transfer" and transfer.get("source") == wallet:
                    events.append(
                        Event(
                            wallet=wallet,
                            mint=mint,
                            signature=sig.signature,
                            kind="sale_proceeds",
                            timestamp=tstamp_iso,
                            sol_amount=transfer.get("amount", 0.0),
                            asset="SOL",
                            counterparty=transfer.get("destination"),
                            proceeds_destination=transfer.get("destination"),
                            relationship_confidence="medium",
                        )
                    )

    if sigs:
        profile.tx_count = len(sigs)
    return events, discovered_wallets, truncated, profile


def infer_launch_window(helius: HeliusClient, mint: str) -> int | None:
    sigs, _ = paginate_history(helius, mint, max_pages=min(2, SETTINGS.max_pages), page_limit=SETTINGS.page_limit)
    times = [s.block_time for s in sigs if s.block_time]
    return min(times) if times else None


def find_developer_create_evidence(helius: HeliusClient, developer: str, mint: str) -> dict[str, Any]:
    sigs, _ = paginate_history(helius, developer, max_pages=min(4, SETTINGS.max_pages), page_limit=SETTINGS.page_limit)
    for sig in sigs:
        tx = load_tx(helius, sig.signature)
        if not tx or not tx.get("meta"):
            continue
        for row in (tx.get("meta") or {}).get("postTokenBalances") or []:
            if row.get("mint") == mint and row.get("owner") == developer:
                return {
                    "status": "Likely",
                    "evidence": "developer wallet appears in mint-related token balances",
                    "alternative_explanations": [
                        "wallet may have acquired the token after creation rather than creating it",
                        "mint-related balances alone do not prove creator attribution",
                    ],
                    "signature": sig.signature,
                }
    return {
        "status": "Unknown",
        "evidence": "no direct creator metadata found in wallet history",
        "alternative_explanations": [
            "creator metadata may be absent from the accessible transaction history",
            "the wallet may still be related without appearing in the sampled transactions",
        ],
        "signature": None,
    }


def build_developer_cluster(
    developer: str,
    events: list[Event],
    profiles: dict[str, WalletProfile],
) -> dict[str, Any]:
    connections: list[dict[str, Any]] = []
    side_wallets: set[str] = set()
    proceeds_wallets: set[str] = set()
    funding_wallets: list[dict[str, Any]] = []

    for profile in profiles.values():
        if profile.wallet != developer and profile.funder:
            funding_wallets.append(
                {
                    "wallet": profile.wallet,
                    "funder": profile.funder,
                    "funder_evidence": profile.funder_evidence,
                    "confidence": "high" if profile.funder else "unknown",
                }
            )

    for event in events:
        direction = None
        reason = None
        if event.kind == "transfer_out" and event.wallet == developer and event.counterparty:
            side_wallets.add(event.counterparty)
            direction = "developer_to_side_wallet"
            reason = "developer sent token to another wallet"
        elif event.kind == "transfer_in" and event.counterparty and event.counterparty == developer:
            side_wallets.add(event.wallet)
            direction = "side_wallet_to_developer"
            reason = "wallet received token from the developer cluster"
        elif event.kind == "sale_proceeds" and event.proceeds_destination:
            proceeds_wallets.add(event.proceeds_destination)
            direction = "sale_proceeds"
            reason = "sale proceeds left a selling wallet"
        elif event.kind == "vault_deposit":
            direction = "vault_deposit"
            reason = "tokens were deposited into a vault-like destination"

        if direction:
            connections.append(
                {
                    "signature": event.signature,
                    "timestamp": event.timestamp,
                    "asset": event.asset or "token",
                    "amount": round(event.token_amount or event.sol_amount or 0.0, 6),
                    "direction": direction,
                    "from_wallet": event.wallet,
                    "to_wallet": event.counterparty or event.proceeds_destination,
                    "reason": reason,
                    "confidence": event.relationship_confidence,
                }
            )

    if developer not in side_wallets:
        side_wallets.discard(developer)
    return {
        "deployer_wallet": developer,
        "funding_wallets": funding_wallets,
        "side_wallets": sorted(side_wallets),
        "proceeds_wallets": sorted(proceeds_wallets),
        "connections": connections,
    }


def score_wallet_risk(
    developer: str,
    events: list[Event],
    profiles: dict[str, WalletProfile],
    developer_evidence: dict[str, Any],
    truncated: bool,
) -> tuple[int, str, int, str]:
    risk = 0
    side_wallets = {e.counterparty for e in events if e.kind == "transfer_out" and e.wallet == developer and e.counterparty}
    sale_wallets = {e.wallet for e in events if e.kind == "sell" and e.wallet != developer}
    proceeds_wallets = {e.proceeds_destination for e in events if e.kind == "sale_proceeds" and e.proceeds_destination}
    common_funders = Counter(profile.funder for profile in profiles.values() if profile.funder)
    common_funder_count = common_funders.most_common(1)[0][1] if common_funders else 0

    if side_wallets:
        risk += min(35, len(side_wallets) * 15)
    if sale_wallets:
        risk += min(35, len(sale_wallets) * 20)
    if proceeds_wallets:
        risk += min(20, len(proceeds_wallets) * 10)
    if common_funder_count > 1:
        risk += 15
    if truncated:
        risk += 5

    if risk >= 60:
        label = "high"
    elif risk >= 25:
        label = "medium"
    else:
        label = "low"

    confidence_score = 0
    status = developer_evidence.get("status")
    if status == "Likely":
        confidence_score += 35
    elif status == "Unknown":
        confidence_score += 10
    if side_wallets:
        confidence_score += 20
    if proceeds_wallets:
        confidence_score += 20
    if not truncated:
        confidence_score += 15
    if any(profile.sold_token for profile in profiles.values()):
        confidence_score += 10
    if confidence_score >= 70:
        confidence = "high"
    elif confidence_score >= 35:
        confidence = "medium"
    else:
        confidence = "low"
    return risk, label, confidence_score, confidence


def build_report(
    *,
    mint: str,
    developer: str,
    events: list[Event],
    profiles: dict[str, WalletProfile],
    truncated: bool,
    developer_evidence: dict[str, Any],
    launch_time: int | None,
) -> dict[str, Any]:
    all_events = [asdict(e) for e in events]
    buys = [e for e in all_events if e["kind"] == "buy"]
    sells = [e for e in all_events if e["kind"] == "sell"]
    transfers_out = [e for e in all_events if e["kind"] == "transfer_out"]
    sale_proceeds = [e for e in all_events if e["kind"] == "sale_proceeds"]
    developer_cluster = build_developer_cluster(developer, events, profiles)
    wallet_risk_score, wallet_risk_label, analysis_confidence_score, analysis_confidence = score_wallet_risk(
        developer,
        events,
        profiles,
        developer_evidence,
        truncated,
    )
    wallet_count = len(profiles)
    net_tokens = round(
        sum(e.get("token_amount", 0.0) for e in buys)
        - sum(e.get("token_amount", 0.0) for e in sells)
        - sum(e.get("token_amount", 0.0) for e in transfers_out),
        6,
    )

    clusters = Counter()
    for profile in profiles.values():
        if profile.funder:
            clusters[profile.funder] += 1
    common_funder, common_funder_count = clusters.most_common(1)[0] if clusters else (None, 0)

    cluster = {
        "wallet_count": wallet_count,
        "common_funder": common_funder,
        "common_funder_count": common_funder_count,
        "truncated": truncated,
    }

    conclusion = "Unknown"
    confidence = "low"
    if developer_evidence["status"] == "Likely" and any(e["kind"] == "buy" for e in all_events):
        conclusion = "Likely"
        confidence = "medium"
    if any(p.sold_token for p in profiles.values()) and any(e["kind"] == "sale_proceeds" for e in all_events):
        confidence = "medium"
        if conclusion == "Unknown":
            conclusion = "Possible"

    sequences = []
    transfers_by_src = defaultdict(list)
    sales_by_wallet = defaultdict(list)
    for e in all_events:
        if e["kind"] == "transfer_out" and e.get("counterparty"):
            transfers_by_src[e["wallet"]].append(e)
        if e["kind"] == "sell":
            sales_by_wallet[e["wallet"]].append(e)
    for wallet, transfer_events in transfers_by_src.items():
        for transfer in transfer_events:
            related_sales = sales_by_wallet.get(transfer.get("counterparty"), [])
            for sale in related_sales:
                sequences.append(
                    {
                        "developer_wallet": wallet,
                        "recipient_wallet": transfer.get("counterparty"),
                        "transfer_signature": transfer.get("signature"),
                        "sale_signature": sale.get("signature"),
                        "tokens_transferred": transfer.get("token_amount"),
                        "tokens_sold": sale.get("token_amount"),
                        "sale_proceeds": sale.get("sol_amount"),
                    }
                )

    report = {
        "generated_at": now_iso(),
        "mint": mint,
        "developer_wallet": developer,
        "developer_attribution": {
            "status": developer_evidence["status"],
            "confidence": confidence,
            "evidence": developer_evidence["evidence"],
            "alternative_explanations": developer_evidence["alternative_explanations"],
            "signature": developer_evidence["signature"],
        },
        "assessment": {
            "conclusion": conclusion,
            "confidence": confidence,
            "wallet_risk": {
                "score": wallet_risk_score,
                "label": wallet_risk_label,
            },
            "analysis_confidence": {
                "score": analysis_confidence_score,
                "label": analysis_confidence,
            },
            "notes": [
                "Behavioral evidence is not treated as fraud by itself.",
                "Token transfer is not classified as a sale unless a swap or exchange-style proceeds event is shown.",
            ],
        },
        "summary": {
            "token_bought": round(sum(e.get("token_amount", 0.0) for e in buys), 6),
            "token_sent_to_side_wallets": round(sum(e.get("token_amount", 0.0) for e in transfers_out), 6),
            "token_sold_by_side_wallets": round(sum(e.get("token_amount", 0.0) for e in sells), 6),
            "sol_spent_on_buys": round(sum(e.get("sol_amount", 0.0) for e in buys), 6),
            "sol_received_by_side_wallets": round(sum(e.get("sol_amount", 0.0) for e in sells), 6),
            "net_cluster_token_change": net_tokens,
        },
        "cluster": cluster,
        "developer_cluster": developer_cluster,
        "wallets": [
            {
                **asdict(profile),
                "funder_confidence": "high" if profile.funder else "unknown",
            }
            for profile in profiles.values()
        ],
        "sequences": sequences,
        "sale_proceeds": sale_proceeds,
        "truncated": truncated,
        "launch_time": datetime.fromtimestamp(launch_time, tz=timezone.utc).isoformat() if launch_time else None,
        "evidence_labeled_assessment": {
            "status": conclusion,
            "confidence": confidence,
            "evidence_count": len(all_events),
        },
        "risk_summary": {
            "wallet_risk_score": wallet_risk_score,
            "wallet_risk_label": wallet_risk_label,
            "analysis_confidence_score": analysis_confidence_score,
            "analysis_confidence_label": analysis_confidence,
        },
        "transactions": all_events,
    }
    return report


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        f"# Solana developer-wallet investigation",
        "",
        f"- Mint: `{report['mint']}`",
        f"- Developer: `{report['developer_wallet']}`",
        f"- Assessment: **{report['assessment']['conclusion']}**",
        f"- Confidence: `{report['assessment']['confidence']}`",
        f"- Wallet risk: `{report['assessment'].get('wallet_risk', {}).get('label')}`",
        f"- Analysis confidence: `{report['assessment'].get('analysis_confidence', {}).get('label')}`",
        f"- Generated: `{report['generated_at']}`",
        "",
        "## Developer Attribution",
        f"- Status: `{report['developer_attribution']['status']}`",
        f"- Evidence: {report['developer_attribution']['evidence']}",
        "",
        "## Summary",
    ]
    for key, value in report["summary"].items():
        lines.append(f"- {key}: `{value}`")
    lines += [
        "",
        "## Notable Sequences",
    ]
    if report["sequences"]:
        for seq in report["sequences"][:25]:
            lines.append(
                f"- `{seq['developer_wallet']}` -> `{seq['recipient_wallet']}` -> "
                f"sold `{seq['tokens_sold']}` tokens, received `{seq['sale_proceeds']}` SOL"
            )
    else:
        lines.append("- No high-confidence buy/transfer/sale sequence reconstructed from the available history.")

    cluster = report.get("developer_cluster") or {}
    if cluster:
        lines += [
            "",
            "## Developer Cluster",
            f"- Deployer wallet: `{cluster.get('deployer_wallet')}`",
        ]
        funding_wallets = cluster.get("funding_wallets") or []
        if funding_wallets:
            lines.append("- Funding wallets:")
            for item in funding_wallets[:10]:
                lines.append(f"  - `{item.get('wallet')}` funded by `{item.get('funder')}`")
        side_wallets = cluster.get("side_wallets") or []
        if side_wallets:
            lines.append("- Side wallets:")
            for wallet in side_wallets[:10]:
                lines.append(f"  - `{wallet}`")
        proceeds_wallets = cluster.get("proceeds_wallets") or []
        if proceeds_wallets:
            lines.append("- Proceeds wallets:")
            for wallet in proceeds_wallets[:10]:
                lines.append(f"  - `{wallet}`")
        connections = cluster.get("connections") or []
        if connections:
            lines.append("- Connections:")
            for item in connections[:12]:
                lines.append(
                    f"  - `{item.get('from_wallet')}` -> `{item.get('to_wallet')}` "
                    f"({item.get('direction')}, `{item.get('confidence')}`)"
                )

    project = report.get("project_research")
    if project:
        lines += [
            "",
            "## Project Research",
            f"- Utility score: `{project.get('score')}`",
            f"- Verdict: `{project.get('verdict')}`",
        ]
        v2 = (project.get("score_breakdown") or {}).get("v2") or {}
        if v2:
            if v2.get("label"):
                lines.append(f"- V2 label: `{v2.get('label')}`")
            lines.append(f"- V2 eligible: `{v2.get('eligible')}`")
            if v2.get("alert_tier"):
                lines.append(f"- V2 alert tier: `{v2.get('alert_tier')}`")
            if v2.get("evidence_sources"):
                lines.append(f"- V2 evidence sources: `{', '.join(v2.get('evidence_sources') or [])}`")
        deployment = (project.get("score_breakdown") or {}).get("deployment_verification") or {}
        if deployment:
            lines.append("- Deployment verification:")
            lines.append(f"  - claimed: `{deployment.get('claimed')}`")
            lines.append(f"  - verified: `{deployment.get('verified')}`")
            if deployment.get("program_count") is not None:
                lines.append(f"  - program_count: `{deployment.get('program_count')}`")
            verified_programs = deployment.get("verified_programs") or []
            for program in verified_programs[:6]:
                lines.append(
                    f"  - `{program.get('address')}` executable `{program.get('executable')}` owner `{program.get('owner')}`"
                )
                for note in program.get("notes") or []:
                    lines.append(f"    - {note}")
        github_repos = project.get("github_repos") or []
        if github_repos:
            lines.append("- GitHub repositories:")
            for repo in github_repos[:8]:
                repo_line = f"  - `{repo.get('owner')}/{repo.get('repo')}`"
                if repo.get("created_at"):
                    repo_line += f" created `{repo.get('created_at')}`"
                if repo.get("first_commit_at"):
                    repo_line += f", first commit `{repo.get('first_commit_at')}`"
                if repo.get("commit_count") is not None:
                    repo_line += f", commits `{repo.get('commit_count')}`"
                if repo.get("predates_launch") is not None:
                    repo_line += f", predates launch `{repo.get('predates_launch')}`"
                lines.append(repo_line)
                if repo.get("notes"):
                    for note in repo.get("notes")[:4]:
                        lines.append(f"    - {note}")
        program_snaps = project.get("program_snapshots") or []
        if program_snaps and not deployment:
            lines.append("- Program snapshots:")
            for program in program_snaps[:6]:
                lines.append(
                    f"  - `{program.get('address')}` claimed `{program.get('claimed')}` verified `{program.get('verified')}`"
                )
                for note in program.get("notes") or []:
                    lines.append(f"    - {note}")
        score_breakdown = project.get("score_breakdown") or {}
        if score_breakdown:
            lines.append("- Score breakdown:")
            for key in ("project_relevance", "evidence_quality", "execution_score", "market_risk", "analysis_confidence", "alert_tier"):
                if key in score_breakdown:
                    lines.append(f"  - {key}: `{score_breakdown.get(key)}`")
        socials = project.get("socials") or {}
        if socials:
            lines.append("- Socials:")
            for key in ("website", "twitter", "telegram"):
                if socials.get(key):
                    lines.append(f"  - {key}: `{socials[key]}`")
        useful_links = project.get("useful_links") or []
        if useful_links:
            lines.append("- Useful links:")
            for link in useful_links[:10]:
                lines.append(f"  - `{link}`")
        reasons = project.get("reasons") or []
        if reasons:
            lines.append("- Signals:")
            for reason in reasons[:12]:
                lines.append(f"  - {reason}")
    lines += [
        "",
        "## Notes",
        "- A transfer alone is not treated as a sale.",
        "- Behavioral patterns are evidence, not a fraud verdict.",
        "- Wallet risk and analysis confidence are tracked separately.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csvs(report: dict[str, Any], out_dir: Path) -> None:
    with (out_dir / "transactions.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "wallet",
                "mint",
                "signature",
                "kind",
                "timestamp",
                "token_amount",
                "sol_amount",
                "asset",
                "counterparty",
                "proceeds_destination",
                "relationship_confidence",
            ],
        )
        writer.writeheader()
        for row in report["transactions"]:
            writer.writerow({k: row.get(k) for k in writer.fieldnames})

    with (out_dir / "wallet_graph.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["from_wallet", "to_wallet", "relationship", "evidence", "confidence"],
        )
        writer.writeheader()
        developer = report["developer_wallet"]
        for wallet in report["wallets"]:
            if wallet.get("funder"):
                writer.writerow(
                    {
                        "from_wallet": wallet["funder"],
                        "to_wallet": wallet["wallet"],
                        "relationship": "funding",
                        "evidence": wallet.get("funder_evidence") or "first funding transaction",
                        "confidence": wallet.get("funder_confidence", "unknown"),
                    }
                )
        for seq in report["sequences"]:
            writer.writerow(
                {
                    "from_wallet": developer,
                    "to_wallet": seq.get("recipient_wallet"),
                    "relationship": "token_transfer_then_sale",
                    "evidence": seq.get("transfer_signature"),
                    "confidence": "medium",
                }
            )
        for conn in (report.get("developer_cluster") or {}).get("connections") or []:
            writer.writerow(
                {
                    "from_wallet": conn.get("from_wallet"),
                    "to_wallet": conn.get("to_wallet"),
                    "relationship": conn.get("direction"),
                    "evidence": conn.get("signature"),
                    "confidence": conn.get("confidence", "unknown"),
                }
            )


def collect(
    helius: HeliusClient,
    developer: str,
    mint: str,
    *,
    max_depth: int = 2,
    max_pages: int | None = None,
    page_limit: int | None = None,
) -> tuple[list[Event], dict[str, WalletProfile], bool, dict[str, Any], int | None]:
    launch_time = infer_launch_window(helius, mint)
    stop_at_time = None
    if launch_time:
        stop_at_time = launch_time - (72 * 3600)

    events: list[Event] = []
    profiles: dict[str, WalletProfile] = {}
    truncated = False
    seen: set[str] = {developer}
    frontier: deque[tuple[str, int]] = deque([(developer, 0)])
    wallet_events: dict[str, list[Event]] = defaultdict(list)
    wallet_history: dict[str, list[SignatureInfo]] = {}

    while frontier:
        wallet, level = frontier.popleft()
        if level > max_depth:
            continue
        if wallet not in profiles:
            profiles[wallet] = WalletProfile(wallet=wallet, level=level)
        sigs, wallet_truncated = paginate_history(helius, wallet, max_pages=max_pages, page_limit=page_limit)
        wallet_history[wallet] = sigs
        truncated = truncated or wallet_truncated

        for sig in sigs:
            if sig.err:
                continue
            tx = load_tx(helius, sig.signature)
            if not tx or not tx.get("meta"):
                continue
            if stop_at_time and tx.get("blockTime") and tx["blockTime"] < stop_at_time:
                continue
            tstamp = tx_time(tx)
            tstamp_iso = tstamp.isoformat() if tstamp else None
            if profiles[wallet].first_seen is None and tstamp_iso:
                profiles[wallet].first_seen = tstamp_iso
                funder = first_sender_funder(tx, wallet)
                if funder and is_pubkey(funder):
                    profiles[wallet].funder = funder
                    profiles[wallet].funder_evidence = sig.signature

            if mint not in token_mints_in_tx(tx):
                continue
            deltas = owner_token_deltas(tx, mint)
            if wallet not in deltas:
                continue
            sol_deltas = native_sol_deltas(tx)
            token_delta = deltas[wallet]
            sol_delta = sol_deltas.get(wallet, 0.0)
            parsed_transfers = parsed_token_transfers(tx)
            owner_map = token_account_owner_map(tx, mint)
            has_spl_transfer = any(t["kind"] == "spl_transfer" and t["mint"] == mint for t in parsed_transfers)
            vault_deposit = looks_like_vault_deposit(tx, parsed_transfers, owner_map, mint)

            if token_delta > 0 and sol_delta < 0:
                e = Event(wallet, mint, sig.signature, "buy", tstamp_iso, token_amount=token_delta, sol_amount=abs(sol_delta), relationship_confidence="high")
                events.append(e)
                wallet_events[wallet].append(e)
            elif token_delta < 0 and sol_delta > 0:
                e = Event(wallet, mint, sig.signature, "sell", tstamp_iso, token_amount=abs(token_delta), sol_amount=sol_delta, asset="SOL", relationship_confidence="high")
                events.append(e)
                wallet_events[wallet].append(e)
                profiles[wallet].sold_token = True
            elif token_delta < 0 and (transfer_only(tx) or has_spl_transfer) and not vault_deposit:
                recipient = transfer_counterparty(wallet, token_delta, parsed_transfers, mint, owner_map)
                e = Event(wallet, mint, sig.signature, "transfer_out", tstamp_iso, token_amount=abs(token_delta), counterparty=recipient, relationship_confidence="high" if recipient else "medium")
                events.append(e)
                wallet_events[wallet].append(e)
                if recipient and is_pubkey(recipient) and recipient not in seen:
                    seen.add(recipient)
                    frontier.append((recipient, level + 1))
                    profiles[recipient] = WalletProfile(wallet=recipient, level=level + 1, received_from_developer=(wallet == developer))
            elif token_delta > 0 and (transfer_only(tx) or has_spl_transfer) and not vault_deposit:
                sender = transfer_counterparty(wallet, token_delta, parsed_transfers, mint, owner_map)
                e = Event(wallet, mint, sig.signature, "transfer_in", tstamp_iso, token_amount=token_delta, counterparty=sender, relationship_confidence="high" if sender else "medium")
                events.append(e)
                wallet_events[wallet].append(e)
                if sender and is_pubkey(sender) and sender not in seen:
                    seen.add(sender)
                    frontier.append((sender, level + 1))
                    profiles[sender] = WalletProfile(wallet=sender, level=level + 1, received_from_developer=False)

            # track proceeds movements from this wallet after a sale
            if token_delta < 0 and sol_delta > 0:
                for transfer in parsed_transfers:
                    if transfer["kind"] == "sol_transfer" and transfer.get("source") == wallet:
                        e = Event(
                            wallet,
                            mint,
                            sig.signature,
                            "sale_proceeds",
                            tstamp_iso,
                            sol_amount=transfer.get("amount", 0.0),
                            asset="SOL",
                            counterparty=transfer.get("destination"),
                            proceeds_destination=transfer.get("destination"),
                            relationship_confidence="medium",
                        )
                        events.append(e)
                        wallet_events[wallet].append(e)
                        if transfer.get("destination") and is_pubkey(transfer["destination"]) and transfer["destination"] not in seen:
                            seen.add(transfer["destination"])
                            frontier.append((transfer["destination"], level + 1))
                            profiles[transfer["destination"]] = WalletProfile(wallet=transfer["destination"], level=level + 1)

            if vault_deposit and token_delta < 0:
                e = Event(
                    wallet,
                    mint,
                    sig.signature,
                    "vault_deposit",
                    tstamp_iso,
                    token_amount=abs(token_delta),
                    counterparty=transfer_counterparty(wallet, token_delta, parsed_transfers, mint, owner_map),
                    relationship_confidence="medium",
                )
                events.append(e)
                wallet_events[wallet].append(e)

    developer_evidence = find_developer_create_evidence(helius, developer, mint)
    return events, profiles, truncated, developer_evidence, launch_time


def main() -> int:
    load_dotenv(ROOT / ".env")
    ensure_dirs()

    parser = argparse.ArgumentParser(description="Analyze a Solana developer wallet and token mint.")
    parser.add_argument("--mint", required=True)
    parser.add_argument("--developer", required=True)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=SETTINGS.max_pages)
    parser.add_argument("--page-limit", type=int, default=SETTINGS.page_limit)
    args = parser.parse_args()

    if not is_pubkey(args.mint):
        raise SystemExit("invalid --mint public key")
    if not is_pubkey(args.developer):
        raise SystemExit("invalid --developer public key")

    ricomaps = RicoMapsClient()
    if not ricomaps.configured:
        raise SystemExit("RICOMAPS_API_KEY is not configured")

    report = build_ricomaps_report(
        request={
            "developer_wallet": args.developer,
            "token_mint": args.mint,
            "max_side_wallet_depth": args.max_depth,
        },
        payload=ricomaps.analyze(args.mint),
    )

    out_dir = ROOT / "reports"
    (out_dir / "latest.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(report, out_dir / "latest.md")
    write_csvs(report, out_dir)

    print(json.dumps(
        {
            "mint": args.mint,
            "developer": args.developer,
            "assessment": report["assessment"]["conclusion"],
            "confidence": report["assessment"]["confidence"],
            "reports": {
                "markdown": str(out_dir / "latest.md"),
                "json": str(out_dir / "latest.json"),
                "transactions": str(out_dir / "transactions.csv"),
                "wallet_graph": str(out_dir / "wallet_graph.csv"),
            },
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
