"""Action authentication helpers."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from .config import SETTINGS


def require_action_secret(authorization: str | None = Header(default=None)) -> None:
    if not SETTINGS.action_secret:
        return
    expected = f"Bearer {SETTINGS.action_secret}"
    if authorization == expected or authorization == SETTINGS.action_secret:
        return
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token == SETTINGS.action_secret:
            return
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
