"""FastAPI application for developer wallet tracing."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status

from .cache import CacheStore
from .config import SETTINGS
from .helius import HeliusClient
from .models import AnalyzeRequest, HealthResponse, InvestigationCreateResponse, InvestigationRecord
from .security import require_action_secret
from .tracer import ACTION_VERSION, TraceEngine


app = FastAPI(
    title="Solana Developer Wallet Investigator",
    version=ACTION_VERSION,
    description="Deterministic Solana wallet tracing for GPT Actions.",
)

cache = CacheStore(SETTINGS.cache_path)
helius = HeliusClient()
engine = TraceEngine(helius)


def _run_analysis(payload: AnalyzeRequest) -> dict[str, Any]:
    request_dict = payload.model_dump()
    cache_key = cache.cache_key(request_dict, ACTION_VERSION)
    cached = cache.get_analysis(cache_key)
    if cached is not None:
        return cached
    result = engine.analyze(request_dict)
    cache.put_analysis(cache_key, request_dict, result)
    return result


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="solana-developer-wallet-investigator", helius_configured=helius.configured)


@app.post("/analyze-developer-wallet", dependencies=[Depends(require_action_secret)])
async def analyze_developer_wallet(payload: AnalyzeRequest) -> dict[str, Any]:
    if not helius.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HELIUS_API_KEY is not configured",
        )
    return await asyncio.to_thread(_run_analysis, payload)


@app.post("/investigations", response_model=InvestigationCreateResponse, dependencies=[Depends(require_action_secret)])
async def create_investigation(payload: AnalyzeRequest) -> InvestigationCreateResponse:
    if not helius.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HELIUS_API_KEY is not configured",
        )
    investigation_id = str(uuid.uuid4())
    cache.create_investigation(investigation_id, payload.model_dump())
    cache.update_investigation(investigation_id, status="running")

    def _worker() -> None:
        try:
            result = _run_analysis(payload)
            cache.update_investigation(investigation_id, status="completed", result=result)
        except Exception as exc:  # pragma: no cover - background safety net
            cache.update_investigation(investigation_id, status="failed", error=str(exc))

    asyncio.get_running_loop().run_in_executor(None, _worker)
    return InvestigationCreateResponse(investigation_id=investigation_id, status="running")


@app.get("/investigations/{investigation_id}", response_model=InvestigationRecord, dependencies=[Depends(require_action_secret)])
async def get_investigation(investigation_id: str) -> InvestigationRecord:
    record = cache.get_investigation(investigation_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="investigation not found")
    return InvestigationRecord(
        investigation_id=record["investigation_id"],
        status=record["status"],
        request=AnalyzeRequest(**record["request"]),
        result=record["result"],
        error=record["error"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )

