"""Pydantic models used by the API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .config import BASE58_PUBLIC_KEY


class AnalyzeRequest(BaseModel):
    developer_wallet: str = Field(..., min_length=32, max_length=44)
    token_mint: str | None = Field(default=None, min_length=32, max_length=44)
    max_side_wallet_depth: int = Field(default=2, ge=1, le=3)

    @field_validator("developer_wallet", "token_mint")
    @classmethod
    def validate_pubkey(cls, value: str | None) -> str | None:
        import re

        if value is None:
            return value
        if not re.match(BASE58_PUBLIC_KEY, value):
            raise ValueError("must be a valid Solana public key")
        return value


class InvestigationCreateResponse(BaseModel):
    investigation_id: str
    status: Literal["queued", "running", "completed", "failed"]


class InvestigationRecord(BaseModel):
    investigation_id: str
    status: Literal["queued", "running", "completed", "failed"]
    request: AnalyzeRequest
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class HealthResponse(BaseModel):
    status: str
    service: str
    ricomaps_configured: bool
    helius_configured: bool | None = None
