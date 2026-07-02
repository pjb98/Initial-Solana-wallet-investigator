"""Deterministic wallet tracing and clustering."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import SETTINGS
from .helius import HeliusClient, SignatureInfo


TOKEN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}

IGNORED_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
}

TRANSFER_OK_PROGRAMS = TOKEN_PROGRAMS | {
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "MemoSq4gq4Sxq4gq4qSxq4gq4Sxq4gq4qSxq4gq4q",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
}

ACTION_VERSION = "1.0.0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_pubkey(value: str | None) -> bool:
    if not value:
        return False
    import re

    return bool(re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", value))


def _program_ids(tx: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for ix in (((tx.get("transaction") or {}).get("message") or {}).get("instructions") or []):
        pid = ix.get("programId")
        if pid:
            out.add(pid)
    for inner in ((tx.get("meta") or {}).get("innerInstructions") or []):
        for ix in inner.get("instructions") or []:
            pid = ix.get("programId")
            if pid:
                out.add(pid)
    return out


def _account_keys(tx: dict[str, Any]) -> list[str]:
    keys = []
    for key in (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []):
        if isinstance(key, dict):
            pubkey = key.get("pubkey")
        else:
            pubkey = key
        if pubkey:
            keys.append(pubkey)
    return keys


def _owner_deltas(tx: dict[str, Any], mint: str) -> dict[str, float]:
    meta = tx.get("meta") or {}
    pre: dict[str, float] = {}
    post: dict[str, float] = {}
    for bucket, target in ((meta.get("preTokenBalances") or [], pre), (meta.get("postTokenBalances") or [], post)):
        for row in bucket:
            if row.get("mint") != mint:
                continue
            owner = row.get("owner")
            amt = ((row.get("uiTokenAmount") or {}).get("uiAmount"))
            if owner is not None:
                target[owner] = target.get(owner, 0.0) + float(amt or 0)
    owners = set(pre) | set(post)
    return {owner: post.get(owner, 0.0) - pre.get(owner, 0.0) for owner in owners}


def _sol_deltas(tx: dict[str, Any]) -> dict[str, float]:
    keys = _account_keys(tx)
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    out: dict[str, float] = {}
    for idx, key in enumerate(keys):
        if idx >= len(pre) or idx >= len(post):
            continue
        out[key] = (post[idx] - pre[idx]) / 1_000_000_000
    return out


def _tx_time(tx: dict[str, Any]) -> datetime | None:
    block_time = tx.get("blockTime")
    return datetime.fromtimestamp(block_time, tz=timezone.utc) if block_time else None


def _classify_transfer_only(tx: dict[str, Any]) -> bool:
    return _program_ids(tx).issubset(TRANSFER_OK_PROGRAMS)


def _top_positive_owner(deltas: dict[str, float]) -> tuple[str | None, float]:
    positives = [(owner, delta) for owner, delta in deltas.items() if delta > 0]
    if not positives:
        return None, 0.0
    return max(positives, key=lambda pair: pair[1])


def _top_negative_owner(deltas: dict[str, float]) -> tuple[str | None, float]:
    negatives = [(owner, delta) for owner, delta in deltas.items() if delta < 0]
    if not negatives:
        return None, 0.0
    return min(negatives, key=lambda pair: pair[1])


@dataclass(slots=True)
class WalletAnalysis:
    wallet: str
    signatures: list[SignatureInfo]
    truncated: bool
    events: list[dict[str, Any]]


class TraceEngine:
    def __init__(self, helius: HeliusClient) -> None:
        self.helius = helius

    def analyze(self, request: dict[str, Any]) -> dict[str, Any]:
        developer_wallet = request["developer_wallet"]
        token_mint = request.get("token_mint")
        max_depth = int(request.get("max_side_wallet_depth") or 2)

        launch_time = None
        inferred = False
        if not token_mint:
            token_mint = self._infer_token_mint(developer_wallet)
            inferred = True
        if not token_mint:
            return {
                "request": request,
                "developer_attribution": {
                    "status": "not_found",
                    "confidence": "low",
                    "token_mint": None,
                    "launch_time": None,
                    "notes": ["no dominant mint could be inferred from the developer wallet"],
                },
                "summary": {
                    "token_bought": 0.0,
                    "token_sent_to_side_wallets": 0.0,
                    "token_sold_by_side_wallets": 0.0,
                    "sol_spent_on_buys": 0.0,
                    "sol_received_by_side_wallets": 0.0,
                    "net_cluster_token_change": 0.0,
                },
                "cluster": {
                    "wallet_count": 1,
                    "wallets": [developer_wallet],
                    "truncated": False,
                },
                "wallets": [],
                "graph": {
                    "wallets": [developer_wallet],
                    "edges": [],
                },
                "suspected_cycles": [],
                "evidence": [],
                "truncation": {
                    "truncated": False,
                    "signature_cap": SETTINGS.truncation_signature_cap,
                    "max_pages": SETTINGS.max_pages,
                    "launch_window_hours": SETTINGS.launch_window_hours,
                },
                "meta": {
                    "version": ACTION_VERSION,
                    "generated_at": _now().isoformat(),
                },
            }
        if token_mint:
            launch_time = self._estimate_launch_time(token_mint)

        # Mint-scoped investigations are typically for older tokens, so scan much
        # deeper than the lightweight default used for blind wallet inference.
        scan_pages = max(SETTINGS.max_pages, 12) if token_mint else SETTINGS.max_pages
        scan_limit = 1000 if token_mint else SETTINGS.page_limit

        cutoff = None
        if launch_time:
            cutoff = int((launch_time - timedelta(hours=SETTINGS.launch_window_hours)).timestamp())

        cluster = {developer_wallet}
        edges: list[dict[str, Any]] = []
        wallet_results: dict[str, dict[str, Any]] = {}
        target_events: list[dict[str, Any]] = []
        truncated = False
        wallet_analyses: dict[str, WalletAnalysis] = {}
        tx_cache: dict[str, dict[str, Any] | None] = {}

        def get_analysis(wallet: str) -> WalletAnalysis:
            cached = wallet_analyses.get(wallet)
            if cached is not None:
                return cached
            is_seed = wallet == developer_wallet
            analysis = self._analyze_wallet(
                wallet,
                token_mint=token_mint,
                cutoff=cutoff,
                max_pages=scan_pages if is_seed else min(scan_pages, 4),
                page_limit=scan_limit,
                tx_cache=tx_cache,
            )
            wallet_analyses[wallet] = analysis
            return analysis

        frontier = [developer_wallet]
        for depth in range(max_depth):
            next_frontier: list[str] = []
            for wallet in frontier:
                analysis = get_analysis(wallet)
                wallet_results[wallet] = self._wallet_summary(analysis, token_mint)
                target_events.extend(analysis.events)
                truncated = truncated or analysis.truncated
                for event in analysis.events:
                    if event.get("kind") in {"transfer_out", "transfer_in"} and event.get("other_wallet"):
                        other = event["other_wallet"]
                        if other not in cluster and _is_pubkey(other):
                            cluster.add(other)
                            next_frontier.append(other)
                            edges.append(
                                {
                                    "from_wallet": event.get("owner"),
                                    "to_wallet": other,
                                    "mint": token_mint,
                                    "signature": event.get("signature"),
                                    "kind": event.get("kind"),
                                    "relationship_confidence": "medium" if depth == 0 else "low",
                                }
                            )
            frontier = next_frontier
            if not frontier:
                break

        cluster_events = self._analyze_cluster_transactions(
            cluster,
            token_mint,
            cutoff=cutoff,
            analyses=wallet_analyses,
            max_pages=scan_pages,
            page_limit=scan_limit,
            tx_cache=tx_cache,
        )
        target_events.extend(cluster_events["events"])
        truncated = truncated or cluster_events["truncated"]
        wallet_results.update(cluster_events["wallet_results"])
        target_events = self._dedupe_events(target_events)

        buys = [e for e in target_events if e.get("kind") == "buy"]
        sells = [e for e in target_events if e.get("kind") == "sell"]
        transfers_out = [e for e in target_events if e.get("kind") == "transfer_out"]
        transfers_in = [e for e in target_events if e.get("kind") == "transfer_in"]

        summary = {
            "token_bought": round(sum(e.get("token_amount", 0.0) for e in buys), 6),
            "token_sent_to_side_wallets": round(sum(abs(e.get("token_amount", 0.0)) for e in transfers_out), 6),
            "token_sold_by_side_wallets": round(sum(abs(e.get("token_amount", 0.0)) for e in sells), 6),
            "sol_spent_on_buys": round(sum(abs(e.get("sol_amount", 0.0)) for e in buys), 6),
            "sol_received_by_side_wallets": round(sum(e.get("sol_amount", 0.0) for e in sells), 6),
            "net_cluster_token_change": round(
                sum(e.get("token_amount", 0.0) for e in buys)
                + sum(e.get("token_amount", 0.0) for e in transfers_in)
                + sum(e.get("token_amount", 0.0) for e in transfers_out)
                + sum(e.get("token_amount", 0.0) for e in sells),
                6,
            ),
        }

        suspected_cycles = self._find_cycles(target_events, token_mint)

        attribution_status = "user_supplied" if not inferred else "inferred"
        confidence = "high" if token_mint and not inferred else "low"
        if not target_events:
            confidence = "low"
            attribution_status = "not_found" if token_mint else attribution_status

        result = {
            "request": request,
            "developer_attribution": {
                "status": attribution_status,
                "confidence": confidence,
                "token_mint": token_mint,
                "launch_time": launch_time.isoformat() if launch_time else None,
                "notes": [
                    "token mint was inferred from wallet activity" if inferred else "token mint was user supplied",
                    "history was truncated" if truncated else "history fit within configured limits",
                ],
            },
            "summary": summary,
            "cluster": {
                "wallet_count": len(cluster),
                "wallets": sorted(cluster),
                "truncated": truncated,
            },
            "wallets": [
                {
                    "wallet": wallet,
                    **payload,
                }
                for wallet, payload in sorted(wallet_results.items())
            ],
            "graph": {
                "wallets": sorted(cluster),
                "edges": edges,
            },
            "suspected_cycles": suspected_cycles,
            "evidence": self._build_evidence(target_events, suspected_cycles),
            "truncation": {
                "truncated": truncated,
                "signature_cap": SETTINGS.truncation_signature_cap,
                "max_pages": SETTINGS.max_pages,
                "launch_window_hours": SETTINGS.launch_window_hours,
            },
            "meta": {
                "version": ACTION_VERSION,
                "generated_at": _now().isoformat(),
            },
        }
        return result

    def _analyze_wallet(
        self,
        wallet: str,
        *,
        token_mint: str | None,
        cutoff: int | None,
        max_pages: int | None = None,
        page_limit: int | None = None,
        tx_cache: dict[str, dict[str, Any] | None] | None = None,
    ) -> WalletAnalysis:
        sigs, truncated = self.helius.paginate_signatures(
            wallet,
            until_block_time=cutoff,
            max_pages=max_pages or SETTINGS.max_pages,
            limit=page_limit or SETTINGS.page_limit,
        )
        events: list[dict[str, Any]] = []
        for sig in sigs:
            if sig.err:
                continue
            if tx_cache is not None and sig.signature in tx_cache:
                tx = tx_cache[sig.signature]
            else:
                tx = self.helius.get_transaction(sig.signature)
                if tx_cache is not None:
                    tx_cache[sig.signature] = tx
            if not tx or not tx.get("meta"):
                continue
            if token_mint:
                events.extend(self._extract_events_for_mint(wallet, token_mint, tx, sig.signature))
            else:
                inferred = self._extract_events_for_any_mint(wallet, tx, sig.signature)
                events.extend(inferred)
        return WalletAnalysis(wallet=wallet, signatures=sigs, truncated=truncated, events=events)

    def _extract_events_for_any_mint(self, wallet: str, tx: dict[str, Any], signature: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        meta = tx.get("meta") or {}
        for row in (meta.get("postTokenBalances") or []):
            mint = row.get("mint")
            if mint and _is_pubkey(mint):
                events.extend(self._extract_events_for_mint(wallet, mint, tx, signature))
        return events

    def _extract_events_for_mint(self, wallet: str, mint: str, tx: dict[str, Any], signature: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        owner_deltas = _owner_deltas(tx, mint)
        if not owner_deltas:
            return events
        sol_deltas = _sol_deltas(tx)
        transfer_only = _classify_transfer_only(tx)
        tx_time = _tx_time(tx)
        top_pos_owner, top_pos_delta = _top_positive_owner(owner_deltas)
        top_neg_owner, top_neg_delta = _top_negative_owner(owner_deltas)

        if wallet in owner_deltas:
            token_delta = owner_deltas[wallet]
            sol_delta = sol_deltas.get(wallet, 0.0)
            if token_delta > 0 and sol_delta < 0:
                events.append(
                    {
                        "kind": "buy",
                        "wallet": wallet,
                        "mint": mint,
                        "signature": signature,
                        "timestamp": tx_time.isoformat() if tx_time else None,
                        "token_amount": token_delta,
                        "sol_amount": abs(sol_delta),
                        "relationship_confidence": "high" if top_pos_owner == wallet else "medium",
                    }
                )
            elif token_delta < 0 and sol_delta > 0:
                events.append(
                    {
                        "kind": "sell",
                        "wallet": wallet,
                        "mint": mint,
                        "signature": signature,
                        "timestamp": tx_time.isoformat() if tx_time else None,
                        "token_amount": token_delta,
                        "sol_amount": sol_delta,
                        "relationship_confidence": "high" if top_neg_owner == wallet else "medium",
                    }
                )
            elif transfer_only and token_delta != 0:
                other = top_pos_owner if token_delta < 0 else top_neg_owner
                events.append(
                    {
                        "kind": "transfer_out" if token_delta < 0 else "transfer_in",
                        "wallet": wallet,
                        "owner": wallet,
                        "other_wallet": other,
                        "mint": mint,
                        "signature": signature,
                        "timestamp": tx_time.isoformat() if tx_time else None,
                        "token_amount": token_delta,
                        "sol_amount": sol_delta,
                        "relationship_confidence": "high" if other and other != wallet else "low",
                    }
                )
        # Track direct wallet-to-wallet transfers even if the target wallet is not the signer.
        if transfer_only:
            for owner, delta in owner_deltas.items():
                if owner == wallet or delta == 0:
                    continue
                if delta > 0:
                    events.append(
                        {
                            "kind": "transfer_in",
                            "wallet": owner,
                            "owner": owner,
                            "other_wallet": wallet if wallet != owner else None,
                            "mint": mint,
                            "signature": signature,
                            "timestamp": tx_time.isoformat() if tx_time else None,
                            "token_amount": delta,
                            "sol_amount": sol_deltas.get(owner, 0.0),
                            "relationship_confidence": "medium",
                        }
                    )
        return events

    def _wallet_summary(self, analysis: WalletAnalysis, token_mint: str | None) -> dict[str, Any]:
        buys = [e for e in analysis.events if e.get("kind") == "buy"]
        sells = [e for e in analysis.events if e.get("kind") == "sell"]
        transfers_out = [e for e in analysis.events if e.get("kind") == "transfer_out"]
        transfers_in = [e for e in analysis.events if e.get("kind") == "transfer_in"]
        return {
            "tx_count": len(analysis.signatures),
            "truncated": analysis.truncated,
            "events_found": len(analysis.events),
            "token_mint": token_mint,
            "buy_token_amount": round(sum(e.get("token_amount", 0.0) for e in buys), 6),
            "sell_token_amount": round(sum(abs(e.get("token_amount", 0.0)) for e in sells), 6),
            "transfer_in_token_amount": round(sum(e.get("token_amount", 0.0) for e in transfers_in), 6),
            "transfer_out_token_amount": round(sum(abs(e.get("token_amount", 0.0)) for e in transfers_out), 6),
            "sol_net": round(
                sum((e.get("sol_amount", 0.0) if e.get("kind") == "sell" else -e.get("sol_amount", 0.0)) for e in buys + sells),
                6,
            ),
        }

    def _analyze_cluster_transactions(
        self,
        cluster: set[str],
        token_mint: str | None,
        *,
        cutoff: int | None,
        analyses: dict[str, WalletAnalysis] | None = None,
        max_pages: int | None = None,
        page_limit: int | None = None,
        tx_cache: dict[str, dict[str, Any] | None] | None = None,
    ) -> dict[str, Any]:
        wallet_results: dict[str, dict[str, Any]] = {}
        events: list[dict[str, Any]] = []
        truncated = False
        for wallet in sorted(cluster):
            analysis = analyses.get(wallet) if analyses else None
            if analysis is None:
                analysis = self._analyze_wallet(
                    wallet,
                    token_mint=token_mint,
                    cutoff=cutoff,
                    max_pages=max_pages,
                    page_limit=page_limit,
                    tx_cache=tx_cache,
                )
                if analyses is not None:
                    analyses[wallet] = analysis
            wallet_results[wallet] = self._wallet_summary(analysis, token_mint)
            events.extend(analysis.events)
            truncated = truncated or analysis.truncated
        return {"wallet_results": wallet_results, "events": events, "truncated": truncated}

    def _find_cycles(self, events: list[dict[str, Any]], token_mint: str | None) -> list[dict[str, Any]]:
        if not token_mint:
            return []
        by_wallet: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            wallet = event.get("wallet")
            if wallet:
                by_wallet[wallet].append(event)
        cycles: list[dict[str, Any]] = []
        for wallet, wallet_events in by_wallet.items():
            buys = [e for e in wallet_events if e.get("kind") == "buy"]
            transfers = [e for e in wallet_events if e.get("kind") == "transfer_out"]
            sells = [e for e in wallet_events if e.get("kind") == "sell"]
            if not buys or not sells:
                continue
            for buy in buys:
                for transfer in transfers:
                    if transfer.get("timestamp") and buy.get("timestamp") and transfer["timestamp"] < buy["timestamp"]:
                        continue
                    for sell in sells:
                        if sell.get("timestamp") and transfer.get("timestamp") and sell["timestamp"] < transfer["timestamp"]:
                            continue
                        cycles.append(
                            {
                                "buy_signature": buy.get("signature"),
                                "transfer_signature": transfer.get("signature"),
                                "sale_signature": sell.get("signature"),
                                "recipient_wallet": transfer.get("other_wallet"),
                                "delay_seconds": self._delay_seconds(buy, sell),
                                "tokens_bought": buy.get("token_amount"),
                                "tokens_transferred": abs(transfer.get("token_amount", 0.0)),
                                "tokens_sold": abs(sell.get("token_amount", 0.0)),
                                "sol_received": sell.get("sol_amount"),
                                "proceeds_destination": transfer.get("other_wallet"),
                                "relationship_confidence": "high",
                            }
                        )
                        if len(cycles) >= 25:
                            return cycles
        return cycles

    @staticmethod
    def _delay_seconds(start: dict[str, Any], end: dict[str, Any]) -> int | None:
        try:
            if not start.get("timestamp") or not end.get("timestamp"):
                return None
            s = datetime.fromisoformat(start["timestamp"])
            e = datetime.fromisoformat(end["timestamp"])
            return int((e - s).total_seconds())
        except Exception:
            return None

    def _build_evidence(self, events: list[dict[str, Any]], cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for event in events[:50]:
            evidence.append(
                {
                    "type": event.get("kind"),
                    "wallet": event.get("wallet"),
                    "mint": event.get("mint"),
                    "signature": event.get("signature"),
                    "token_amount": event.get("token_amount"),
                    "sol_amount": event.get("sol_amount"),
                    "confidence": event.get("relationship_confidence"),
                }
            )
        for cycle in cycles[:10]:
            evidence.append(
                {
                    "type": "cycle",
                    "buy_signature": cycle.get("buy_signature"),
                    "transfer_signature": cycle.get("transfer_signature"),
                    "sale_signature": cycle.get("sale_signature"),
                    "confidence": cycle.get("relationship_confidence"),
                }
            )
        return evidence

    @staticmethod
    def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[Any, ...]] = set()
        out: list[dict[str, Any]] = []
        for event in events:
            key = (
                event.get("kind"),
                event.get("wallet"),
                event.get("mint"),
                event.get("signature"),
                event.get("other_wallet"),
                round(float(event.get("token_amount", 0.0) or 0.0), 9),
                round(float(event.get("sol_amount", 0.0) or 0.0), 9),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(event)
        return out

    def _infer_token_mint(self, developer_wallet: str) -> str | None:
        sigs, _ = self.helius.paginate_signatures(
            developer_wallet, max_pages=min(3, SETTINGS.max_pages), limit=SETTINGS.page_limit
        )
        scores: Counter[str] = Counter()
        for sig in sigs:
            if sig.err:
                continue
            tx = self.helius.get_transaction(sig.signature)
            if not tx or not tx.get("meta"):
                continue
            for row in (tx.get("meta") or {}).get("postTokenBalances") or []:
                mint = row.get("mint")
                owner = row.get("owner")
                if mint in IGNORED_MINTS:
                    continue
                if mint and owner == developer_wallet:
                    scores[mint] += 3
                elif mint:
                    scores[mint] += 1
        for ignored in IGNORED_MINTS:
            scores.pop(ignored, None)
        return scores.most_common(1)[0][0] if scores else None

    def _estimate_launch_time(self, mint: str) -> datetime | None:
        # Approximate launch time using the earliest signature that touches the mint.
        sigs, _ = self.helius.paginate_signatures(
            mint, max_pages=min(2, SETTINGS.max_pages), limit=SETTINGS.page_limit
        )
        times = [datetime.fromtimestamp(s.block_time, tz=timezone.utc) for s in sigs if s.block_time]
        return min(times) if times else None
