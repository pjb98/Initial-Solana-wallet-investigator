"""Runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    helius_api_key: str | None = _env("HELIUS_API_KEY")
    action_secret: str | None = _env("ACTION_SECRET")
    cache_path: Path = Path(_env("INVESTIGATOR_CACHE_PATH", "/tmp/solana-investigator-cache.sqlite") or "/tmp/solana-investigator-cache.sqlite")
    request_timeout: float = float(_env("INVESTIGATOR_REQUEST_TIMEOUT", "20") or 20)
    max_pages: int = int(_env("INVESTIGATOR_MAX_PAGES", "8") or 8)
    page_limit: int = int(_env("INVESTIGATOR_PAGE_LIMIT", "1000") or 1000)
    launch_window_hours: int = int(_env("INVESTIGATOR_LAUNCH_WINDOW_HOURS", "72") or 72)
    truncation_signature_cap: int = int(_env("INVESTIGATOR_SIGNATURE_CAP", "6000") or 6000)
    cache_ttl_seconds: int = int(_env("INVESTIGATOR_CACHE_TTL_SECONDS", "86400") or 86400)
    allow_public_openapi: bool = _bool("INVESTIGATOR_ALLOW_PUBLIC_OPENAPI", True)


SETTINGS = Settings()


HELIUS_RPC_URL = (
    f"https://mainnet.helius-rpc.com/?api-key={SETTINGS.helius_api_key}"
    if SETTINGS.helius_api_key
    else None
)


BASE58_PUBLIC_KEY = r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"
