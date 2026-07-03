"""Minimal Helius JSON-RPC client with pagination."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from .config import HELIUS_RPC_URL, SETTINGS


@dataclass(slots=True)
class SignatureInfo:
    signature: str
    slot: int | None
    block_time: int | None
    err: Any


class HeliusClient:
    def __init__(self, rpc_url: str | None = None) -> None:
        self.rpc_url = rpc_url or HELIUS_RPC_URL
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._counter = 0

    @property
    def configured(self) -> bool:
        return self.rpc_url is not None

    def rpc(self, method: str, params: list[Any], timeout: float | None = None, max_retries: int = 5) -> dict[str, Any]:
        if not self.rpc_url:
            raise RuntimeError("HELIUS_API_KEY is not configured")
        payload = {"jsonrpc": "2.0", "id": self._counter + 1, "method": method, "params": params}
        timeout = timeout or SETTINGS.request_timeout
        for attempt in range(max_retries):
            self._counter += 1
            resp = self._session.post(self.rpc_url, json=payload, timeout=timeout)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 0) or 0)
                wait = retry_after if retry_after > 0 else min(8.0, 0.5 * (2**attempt))
                time.sleep(wait)
                continue
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                raise RuntimeError(f"Helius RPC {method} failed: {body['error']}")
            return body.get("result") or {}
        raise RuntimeError(f"Helius RPC {method} rate-limited after {max_retries} retries")

    def get_signatures_for_address(
        self,
        address: str,
        *,
        before: str | None = None,
        limit: int = 1000,
    ) -> list[SignatureInfo]:
        params: list[Any] = [address, {"limit": limit}]
        if before:
            params[1]["before"] = before
        result = self.rpc("getSignaturesForAddress", params)
        out: list[SignatureInfo] = []
        for item in result or []:
            sig = item.get("signature")
            if not sig:
                continue
            out.append(
                SignatureInfo(
                    signature=sig,
                    slot=item.get("slot"),
                    block_time=item.get("blockTime"),
                    err=item.get("err"),
                )
            )
        return out

    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        result = self.rpc(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        )
        return result or None

    def get_account_info(self, address: str) -> dict[str, Any] | None:
        result = self.rpc(
            "getAccountInfo",
            [address, {"encoding": "jsonParsed"}],
        )
        return result or None

    def paginate_signatures(
        self,
        address: str,
        *,
        before: str | None = None,
        until_block_time: int | None = None,
        max_pages: int | None = None,
        limit: int | None = None,
    ) -> tuple[list[SignatureInfo], bool]:
        pages = max_pages or SETTINGS.max_pages
        page_limit = limit or SETTINGS.page_limit
        seen: list[SignatureInfo] = []
        current_before = before
        truncated = False
        for _ in range(pages):
            batch = self.get_signatures_for_address(address, before=current_before, limit=page_limit)
            if not batch:
                break
            seen.extend(batch)
            current_before = batch[-1].signature
            if len(batch) < page_limit:
                break
            if until_block_time is not None and batch[-1].block_time is not None and batch[-1].block_time < until_block_time:
                break
        else:
            truncated = True
        if len(seen) >= SETTINGS.truncation_signature_cap:
            truncated = True
        return seen, truncated
