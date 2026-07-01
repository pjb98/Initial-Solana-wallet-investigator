"""Action authentication helpers."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from .config import SETTINGS


def require_action_secret(authorization: str | None = Header(default=None)) -> None:
    if not SETTINGS.action_secret:
        return
    expected = f"Bearer {SETTINGS.action_secret}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

