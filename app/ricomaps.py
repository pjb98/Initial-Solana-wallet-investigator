"""RicoMaps API client and report normalization helpers."""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

import requests

from .config import SETTINGS


RICOMAPS_BASE_URL = "https://ricomaps.fun/api/v1"
_PUBKEY_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_pubkey(value: str | None) -> bool:
    return bool(value and _PUBKEY_RE.match(value))


def _first(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _unwrap_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if payload.get("success") is False:
        raise RuntimeError(payload.get("error") or "RicoMaps request failed")
    for key in ("data", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload


def _extract_graph(body: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    graph = body.get("data") if isinstance(body.get("data"), dict) else body
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    links = graph.get("links") if isinstance(graph.get("links"), list) else []
    return nodes, links


def _link_kind(label: str | None) -> str:
    low = (label or "").lower()
    if "sale_proceed" in low or "proceed" in low or "cashout" in low or "cash out" in low:
        return "sale_proceeds"
    if "sell" in low or "swap out" in low:
        return "sell"
    if "buy" in low or "swap in" in low:
        return "buy"
    if "fund" in low or "funding" in low:
        return "funding"
    if "transfer in" in low:
        return "transfer_in"
    if "transfer out" in low:
        return "transfer_out"
    if "transfer" in low:
        return "transfer"
    return "connection"


class RicoMapsClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or SETTINGS.ricomaps_api_key
        self.base_url = (base_url or SETTINGS.ricomaps_base_url or RICOMAPS_BASE_URL).rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json", "User-Agent": "solana-wallet-investigator/0.1"})
        self._counter = 0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        max_retries: int = 5,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        timeout = timeout or SETTINGS.request_timeout
        payload = json_body or {}
        for attempt in range(max_retries):
            self._counter += 1
            request_kwargs: dict[str, Any] = {"params": params, "timeout": timeout}
            if method.upper() in {"POST", "PUT", "PATCH"}:
                request_kwargs["json"] = payload
            resp = self._session.request(method, url, **request_kwargs)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 0) or 0)
                wait = retry_after if retry_after > 0 else min(8.0, 0.5 * (2**attempt))
                time.sleep(wait)
                continue
            resp.raise_for_status()
            try:
                body = resp.json()
            except Exception as exc:  # pragma: no cover - defensive
                raise RuntimeError(f"RicoMaps {path} returned invalid JSON") from exc
            if isinstance(body, dict) and body.get("success") is False:
                raise RuntimeError(body.get("error") or f"RicoMaps {path} failed")
            return body if isinstance(body, dict) else {}
        raise RuntimeError(f"RicoMaps {path} rate-limited after {max_retries} retries")

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/status")

    def quick_scan(self, address: str) -> dict[str, Any]:
        return self._request("POST", "/quick-scan", json_body={"address": address})

    def analyze(self, mint: str) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("RICOMAPS_API_KEY is not configured")
        return self._request("POST", "/analyze", json_body={"apiKey": self.api_key, "mint": mint})

    def x_account(self, handle: str) -> dict[str, Any]:
        return self._request("GET", "/x-account", params={"handle": handle})


def build_ricomaps_report(
    *,
    request: dict[str, Any],
    payload: dict[str, Any],
    research: dict[str, Any] | None = None,
    token_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = _unwrap_payload(payload)
    nodes, links = _extract_graph(body)
    summary_source = {}
    for candidate in (body.get("summary"), body.get("stats"), body.get("metrics"), body.get("overview")):
        if isinstance(candidate, dict):
            summary_source = candidate
            break
    token_meta = body.get("tokenMetadata") if isinstance(body.get("tokenMetadata"), dict) else {}
    deployer_info = body.get("deployerInfo") if isinstance(body.get("deployerInfo"), dict) else {}
    token_security = body.get("tokenSecurity") if isinstance(body.get("tokenSecurity"), dict) else {}
    signals = body.get("signals") if isinstance(body.get("signals"), list) else []

    if token_metadata and not token_meta:
        token_meta = token_metadata

    developer_wallet = request.get("developer_wallet")
    token_mint = request.get("token_mint")
    requested_scope = token_mint or developer_wallet
    launch_time = (
        body.get("launch_time")
        or body.get("launchTime")
        or deployer_info.get("launchTime")
        or deployer_info.get("launch_time")
        or token_meta.get("launchTime")
        or token_meta.get("launch_time")
    )

    deployer_wallet = deployer_info.get("address") or deployer_info.get("wallet") or deployer_info.get("pubkey")
    matched_developer = bool(developer_wallet and deployer_wallet and developer_wallet == deployer_wallet)
    attribution_status = "verified" if matched_developer else "not_found" if deployer_wallet else "unknown"
    attribution_confidence = (
        str(summary_source.get("confidence") or deployer_info.get("confidence") or token_security.get("riskLevel") or "low").lower()
    )

    raw_nodes: list[dict[str, Any]] = []
    for node in nodes:
        if isinstance(node, dict):
            raw_nodes.append(node)

    wallet_records: list[dict[str, Any]] = []
    wallet_index: dict[str, dict[str, Any]] = {}
    for node in raw_nodes:
        wallet = _first(node, "address", "wallet", "pubkey", "id", "node")
        if not wallet:
            continue
        record = {
            "wallet": wallet,
            "level": int(_num(_first(node, "level", default=0), 0)),
            "funder": _first(node, "funder", "fundedBy", "funded_by"),
            "funder_evidence": _first(node, "funderEvidence", "funder_evidence"),
            "first_seen": _first(node, "firstSeen", "first_seen"),
            "tx_count": int(_num(_first(node, "txCount", "tx_count", "count", default=0), 0)),
            "received_from_developer": bool(_first(node, "receivedFromDeveloper", "received_from_developer", default=False)),
            "sold_token": bool(_first(node, "soldToken", "sold_token", default=False)),
        }
        wallet_records.append(record)
        wallet_index[wallet] = record

    if developer_wallet and developer_wallet not in wallet_index:
        wallet_records.insert(
            0,
            {
                "wallet": developer_wallet,
                "level": 0,
                "funder": deployer_info.get("funder"),
                "funder_evidence": deployer_info.get("funderEvidence"),
                "first_seen": launch_time,
                "tx_count": int(_num(summary_source.get("analyzedHolders") or summary_source.get("totalHolders"), 0)),
                "received_from_developer": False,
                "sold_token": False,
            },
        )

    normalized_links: list[dict[str, Any]] = []
    side_wallets: set[str] = set()
    proceeds_wallets: set[str] = set()
    funding_wallets: list[dict[str, Any]] = []

    for link in links:
        if not isinstance(link, dict):
            continue
        source = _first(link, "source", "from", "fromWallet", "from_wallet", "walletFrom")
        target = _first(link, "target", "to", "toWallet", "to_wallet", "walletTo")
        label = _first(link, "label", "type", "relation", "relationship", default="connection")
        kind = _link_kind(label)
        amount = _num(_first(link, "amount", "value", "tokenAmount", "token_amount", "solAmount", "sol_amount", "weight"))
        sig = _first(link, "signature", "txSignature", "tx_signature", "evidence")
        timestamp = _first(link, "timestamp", "time", "createdAt", "created_at")
        confidence = str(_first(link, "confidence", default="medium")).lower()
        normalized = {
            "signature": sig,
            "timestamp": timestamp,
            "asset": _first(link, "asset", default="token"),
            "amount": amount,
            "direction": kind,
            "from_wallet": source,
            "to_wallet": target,
            "reason": label,
            "confidence": confidence,
        }
        normalized_links.append(normalized)

        if kind == "funding" and source and target:
            funding_wallets.append(
                {
                    "wallet": target,
                    "funder": source,
                    "funder_evidence": sig,
                    "confidence": confidence,
                }
            )
        if developer_wallet and source == developer_wallet and target:
            if kind in {"transfer", "transfer_out", "funding"}:
                side_wallets.add(target)
        if developer_wallet and target == developer_wallet and source:
            if kind in {"transfer", "transfer_in", "funding"}:
                side_wallets.add(source)
        if kind in {"sell", "sale_proceeds"}:
            if target:
                proceeds_wallets.add(target)
            elif source:
                proceeds_wallets.add(source)

    summary = {
        "token_bought": _num(_first(summary_source, "tokenBought", "token_bought")),
        "token_sent_to_side_wallets": _num(_first(summary_source, "tokenSentToSideWallets", "token_sent_to_side_wallets")),
        "token_sold_by_side_wallets": _num(_first(summary_source, "tokenSoldBySideWallets", "token_sold_by_side_wallets")),
        "sol_spent_on_buys": _num(_first(summary_source, "solSpentOnBuys", "sol_spent_on_buys")),
        "sol_received_by_side_wallets": _num(_first(summary_source, "solReceivedBySideWallets", "sol_received_by_side_wallets")),
        "net_cluster_token_change": _num(_first(summary_source, "netClusterTokenChange", "net_cluster_token_change")),
        "total_holders": _num(_first(summary_source, "totalHolders", "total_holders")),
        "analyzed_holders": _num(_first(summary_source, "analyzedHolders", "analyzed_holders")),
        "confidence": _first(summary_source, "confidence", default=None),
        "holder_coverage_pct": _num(_first(summary_source, "holderCoveragePct", "holder_coverage_pct")),
        "cabal_count": _num(_first(summary_source, "cabalCount", "cabal_count")),
        "risk_score": _num(_first(summary_source, "riskScore", "rugScore", "risk_score")),
        "snipers_detected": _num(_first(summary_source, "snipersDetected", "snipers_detected")),
        "bundle_clusters_detected": _num(_first(summary_source, "bundleClustersDetected", "bundle_clusters_detected")),
        "holder_quality": _first(summary_source, "holderQuality", "holder_quality", default=None),
        "analysis_incomplete": bool(_first(summary_source, "analysisIncomplete", "analysis_incomplete", default=False)),
        "bot_activity_score": _num(_first(summary_source, "botActivityScore", "bot_activity_score")),
        "supply_concentration": _num(_first(summary_source, "supplyConcentration", "supply_concentration")),
        "risk_level": _first(token_security, "riskLevel", "risk_level", default=None),
    }

    risk_score = int(summary["risk_score"] or 0)
    if matched_developer and (side_wallets or proceeds_wallets or normalized_links):
        conclusion = "Likely"
        confidence = "medium" if risk_score < 70 else "high"
    elif risk_score >= 70:
        conclusion = "Possible"
        confidence = "medium"
    elif risk_score >= 40 or normalized_links:
        conclusion = "Review"
        confidence = "low"
    else:
        conclusion = "Unknown"
        confidence = "low"

    transactions: list[dict[str, Any]] = []
    for link in normalized_links:
        transactions.append(
            {
                "wallet": link.get("from_wallet") or developer_wallet or requested_scope,
                "mint": token_mint or requested_scope,
                "signature": link.get("signature") or "",
                "kind": link.get("direction") or "connection",
                "timestamp": link.get("timestamp"),
                "token_amount": link.get("amount") if link.get("direction") in {"buy", "sell", "transfer", "transfer_in", "transfer_out", "funding"} else 0.0,
                "sol_amount": link.get("amount") if link.get("direction") in {"sale_proceeds"} else 0.0,
                "asset": link.get("asset"),
                "counterparty": link.get("to_wallet"),
                "proceeds_destination": link.get("to_wallet") if link.get("direction") == "sale_proceeds" else None,
                "relationship_confidence": link.get("confidence") or "medium",
            }
        )

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sales_by_wallet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for conn in normalized_links:
        if conn.get("from_wallet"):
            by_source[str(conn["from_wallet"])].append(conn)
        if conn.get("direction") == "sell" and conn.get("from_wallet"):
            sales_by_wallet[str(conn["from_wallet"])].append(conn)

    sequences: list[dict[str, Any]] = []
    for conn in normalized_links:
        if conn.get("from_wallet") == developer_wallet and conn.get("to_wallet") and conn.get("direction") in {"transfer", "transfer_out", "funding"}:
            for sale in sales_by_wallet.get(str(conn["to_wallet"]), []):
                sequences.append(
                    {
                        "developer_wallet": developer_wallet,
                        "recipient_wallet": conn.get("to_wallet"),
                        "transfer_signature": conn.get("signature"),
                        "sale_signature": sale.get("signature"),
                        "tokens_transferred": conn.get("amount"),
                        "tokens_sold": sale.get("amount"),
                        "sale_proceeds": sale.get("amount") if sale.get("direction") == "sale_proceeds" else None,
                    }
                )

    common_funder = None
    common_funder_count = 0
    funder_counter = Counter(item.get("funder") for item in funding_wallets if item.get("funder"))
    if funder_counter:
        common_funder, common_funder_count = funder_counter.most_common(1)[0]

    developer_cluster = {
        "deployer_wallet": deployer_wallet or developer_wallet,
        "funding_wallets": funding_wallets,
        "side_wallets": sorted(side_wallets),
        "proceeds_wallets": sorted(proceeds_wallets),
        "connections": normalized_links,
    }

    cluster = {
        "wallet_count": len(wallet_records),
        "wallets": [item["wallet"] for item in wallet_records if item.get("wallet")],
        "common_funder": common_funder,
        "common_funder_count": common_funder_count,
        "truncated": bool(summary["analysis_incomplete"]),
    }

    developer_assessment_notes = [
        "RicoMaps analysis is token-centric; developer-wallet attribution is derived from the returned deployer info and graph links.",
    ]
    if token_security:
        developer_assessment_notes.append(
            f"Token security risk level: {token_security.get('riskLevel') or token_security.get('risk_level') or 'unknown'}"
        )

    report = {
        "generated_at": _now(),
        "source": "ricomaps",
        "request": {
            "developer_wallet": developer_wallet,
            "token_mint": token_mint,
            "max_side_wallet_depth": request.get("max_side_wallet_depth"),
        },
        "mint": token_mint or requested_scope or "",
        "developer_wallet": developer_wallet or deployer_wallet or "",
        "developer_attribution": {
            "status": attribution_status,
            "confidence": attribution_confidence,
            "token_mint": token_mint,
            "launch_time": launch_time,
            "requested_wallet": developer_wallet,
            "deployer_wallet": deployer_wallet,
            "source": deployer_info.get("source"),
            "notes": developer_assessment_notes,
        },
        "assessment": {
            "conclusion": conclusion,
            "confidence": confidence,
            "wallet_risk": {
                "score": risk_score,
                "label": "high" if risk_score >= 70 else "medium" if risk_score >= 40 else "low",
            },
            "analysis_confidence": {
                "score": int(_num(_first(summary_source, "confidenceScore", "analysisConfidence", "analysis_confidence"), 0)) or risk_score,
                "label": str(summary.get("confidence") or confidence),
            },
            "notes": [
                "Behavioral patterns are evidence, not a fraud verdict.",
                "Transfers are not treated as sales unless the source data indicates a sale or proceeds event.",
            ],
        },
        "summary": summary,
        "cluster": cluster,
        "developer_cluster": developer_cluster,
        "wallets": wallet_records,
        "sequences": sequences,
        "sale_proceeds": [conn for conn in normalized_links if conn.get("direction") == "sale_proceeds"],
        "truncated": bool(summary["analysis_incomplete"]),
        "launch_time": launch_time,
        "evidence_labeled_assessment": {
            "status": conclusion,
            "confidence": confidence,
            "evidence_count": len(transactions),
        },
        "risk_summary": {
            "wallet_risk_score": risk_score,
            "wallet_risk_label": "high" if risk_score >= 70 else "medium" if risk_score >= 40 else "low",
            "analysis_confidence_score": int(_num(_first(summary_source, "confidenceScore", "analysisConfidence", "analysis_confidence"), 0)) or risk_score,
            "analysis_confidence_label": str(summary.get("confidence") or confidence),
        },
        "transactions": transactions,
        "token_security": token_security,
        "token_metadata": token_meta,
        "signals": signals,
        "graph": {
            "wallets": [item["wallet"] for item in wallet_records if item.get("wallet")],
            "edges": normalized_links,
        },
    }
    if research:
        report["project_research"] = research
    return report
