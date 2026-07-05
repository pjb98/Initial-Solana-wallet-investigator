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

PUMPPORTAL_WS_URL = "wss://pumpportal.fun/api/data"


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
    ricomaps_api_key: str | None = _env("RICOMAPS_API_KEY")
    action_secret: str | None = _env("ACTION_SECRET")
    discord_webhook_url: str | None = _env("DISCORD_WEBHOOK_URL")
    discord_v1_alerts_enabled: bool = _bool("DISCORD_V1_ALERTS_ENABLED", True)
    discord_v2_alerts_enabled: bool = _bool("DISCORD_V2_ALERTS_ENABLED", True)
    trojan_terminal_url_template: str | None = _env(
        "TROJAN_TERMINAL_URL_TEMPLATE",
        "https://trojan.com/terminal?token={mint}",
    )
    pumpportal_api_key: str | None = _env("PUMPPORTAL_API_KEY")
    ricomaps_base_url: str | None = _env("RICOMAPS_BASE_URL", "https://ricomaps.fun/api/v1")
    cache_path: Path = Path(_env("INVESTIGATOR_CACHE_PATH", "/tmp/solana-investigator-cache.sqlite") or "/tmp/solana-investigator-cache.sqlite")
    request_timeout: float = float(_env("INVESTIGATOR_REQUEST_TIMEOUT", "20") or 20)
    max_pages: int = int(_env("INVESTIGATOR_MAX_PAGES", "3") or 3)
    page_limit: int = int(_env("INVESTIGATOR_PAGE_LIMIT", "250") or 250)
    launch_window_hours: int = int(_env("INVESTIGATOR_LAUNCH_WINDOW_HOURS", "24") or 24)
    truncation_signature_cap: int = int(_env("INVESTIGATOR_SIGNATURE_CAP", "6000") or 6000)
    cache_ttl_seconds: int = int(_env("INVESTIGATOR_CACHE_TTL_SECONDS", "86400") or 86400)
    allow_public_openapi: bool = _bool("INVESTIGATOR_ALLOW_PUBLIC_OPENAPI", True)
    utility_score_threshold: int = int(_env("UTILITY_SCORE_THRESHOLD", "6") or 6)
    utility_crawl_pages: int = int(_env("UTILITY_CRAWL_PAGES", "8") or 8)
    utility_crawl_depth: int = int(_env("UTILITY_CRAWL_DEPTH", "1") or 1)
    utility_analysis_depth: int = int(_env("UTILITY_ANALYSIS_DEPTH", "3") or 3)
    utility_analysis_pages: int = int(_env("UTILITY_ANALYSIS_PAGES", "12") or 12)


SETTINGS = Settings()
def pumpportal_ws_url() -> str:
    if SETTINGS.pumpportal_api_key:
        return f"{PUMPPORTAL_WS_URL}?api-key={SETTINGS.pumpportal_api_key}"
    return PUMPPORTAL_WS_URL


HELIUS_RPC_URL = (
    f"https://mainnet.helius-rpc.com/?api-key={SETTINGS.helius_api_key}"
    if SETTINGS.helius_api_key
    else None
)


BASE58_PUBLIC_KEY = r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"
