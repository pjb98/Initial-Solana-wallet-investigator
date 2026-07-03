"""Token metadata and project-link research helpers."""

from __future__ import annotations

import html
import json
import re
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from .helius import HeliusClient
from .config import SETTINGS

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "solana-wallet-investigator/0.1"})
_GITHUB_SESSION = requests.Session()
_GITHUB_SESSION.headers.update({"User-Agent": "solana-wallet-investigator/0.1", "Accept": "application/vnd.github+json"})

UTILITY_KEYWORDS = {
    "ai",
    "agent",
    "agents",
    "builder",
    "marketplace",
    "compute",
    "deploy",
    "deployment",
    "deployed",
    "webhook",
    "workflow",
    "workflows",
    "mcp",
    "managed agents",
    "x402",
    "zk",
    "zks",
    "zero knowledge",
    "zero-knowledge",
    "protocol",
    "platform",
    "sdk",
    "api",
    "infra",
    "infrastructure",
    "tooling",
    "devtool",
    "devtools",
    "automation",
    "indexer",
    "oracle",
    "payments",
    "wallet",
    "bridge",
    "staking",
    "publish",
    "published",
    "plain language",
    "no-code",
    "nocode",
    "run",
    "running",
    "rewards",
    "points",
    "launch",
    "docs",
    "whitepaper",
    "litepaper",
}

INFRA_KEYWORDS = {
    "ai",
    "agent",
    "agents",
    "builder",
    "marketplace",
    "compute",
    "deploy",
    "deployment",
    "deployed",
    "webhook",
    "workflow",
    "workflows",
    "mcp",
    "managed agents",
    "x402",
    "zk",
    "zks",
    "zero knowledge",
    "zero-knowledge",
    "sdk",
    "api",
    "infra",
    "infrastructure",
    "tooling",
    "devtool",
    "devtools",
    "automation",
    "indexer",
    "oracle",
    "plain language",
    "no-code",
    "nocode",
}

MEME_KEYWORDS = {
    "pepe",
    "doge",
    "inu",
    "wojak",
    "moon",
    "cat",
    "frog",
    "meme",
    "shib",
    "hamster",
    "hamsters",
    "bonk",
    "chad",
    "degen",
    "rizz",
    "alpha",
    "sigma",
    "snoop",
    "elon",
    "vibez",
    "vibes",
    "joke",
    "funny",
    "comedy",
    "viral",
    "trend",
    "trending",
    "cute",
    "animal",
    "animals",
    "dog",
    "dogs",
    "shark",
    "sharks",
    "bear",
    "bears",
    "bull",
    "bulls",
    "monkey",
    "monkeys",
    "rabbit",
    "rabbits",
    "hamster",
    "hamsters",
    "pop culture",
    "celeb",
    "celebrity",
    "celebrities",
}

RESEARCH_LINK_HINTS = {
    "github.com": "github",
    "docs.": "docs",
    "/docs": "docs",
    "/whitepaper": "whitepaper",
    "/litepaper": "litepaper",
    "api.": "api",
    "/api": "api",
}

GITHUB_HOSTS = {"github.com", "www.github.com"}
SOCIAL_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
NITTER_MIRRORS = (
    "https://nitter.net",
    "https://xcancel.com",
)
PROGRAM_CLAIM_PATTERNS = (
    r"\bprogram id\b",
    r"\bsolana program\b",
    r"\bon[- ]chain\b",
    r"\bdeployed on solana\b",
    r"\bmainnet\b",
    r"\bverified program\b",
    r"\bsmart contract\b",
    r"\bcontract deployed\b",
)
PUBKEY_PATTERN = r"[1-9A-HJ-NP-Za-km-z]{32,44}"

TIKTOK_DOMAINS = (
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
)

BLOG_PATH_HINTS = (
    r"/\d{4}/\d{2}/\d{2}/",
    r"/blog/",
    r"/posts?/",
    r"/news/",
    r"/article/",
)

BLOG_TEXT_HINTS = {
    "blog",
    "article",
    "opinion",
    "essay",
    "post",
    "free speech",
    "satire",
    "joke",
    "humor",
    "memes",
    "hamster",
    "hamsters",
    "pop culture",
}


@dataclass(slots=True)
class PageSnapshot:
    url: str
    title: str | None = None
    description: str | None = None
    text: str | None = None
    links: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GithubRepoSnapshot:
    url: str
    owner: str
    repo: str
    created_at: str | None = None
    pushed_at: str | None = None
    default_branch: str | None = None
    language: str | None = None
    archived: bool | None = None
    commit_count: int | None = None
    first_commit_at: str | None = None
    contributor_count: int | None = None
    repository_age_days: int | None = None
    predates_launch: bool | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProgramSnapshot:
    address: str
    claimed: bool = False
    verified: bool | None = None
    executable: bool | None = None
    owner: str | None = None
    lamports: int | None = None
    deployment_signature: str | None = None
    deployment_time: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProjectResearch:
    mint: str
    symbol: str | None = None
    name: str | None = None
    uri: str | None = None
    creator: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    socials: dict[str, str | None] = field(default_factory=dict)
    seed_urls: list[str] = field(default_factory=list)
    crawled_pages: list[PageSnapshot] = field(default_factory=list)
    useful_links: list[str] = field(default_factory=list)
    score: int = 0
    verdict: str = "unclear"
    contract_found: bool = False
    contract_evidence: str | None = None
    utility_signals: int = 0
    infra_signals: int = 0
    meme_signals: int = 0
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    github_repos: list[GithubRepoSnapshot] = field(default_factory=list)
    program_snapshots: list[ProgramSnapshot] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mint": self.mint,
            "symbol": self.symbol,
            "name": self.name,
            "uri": self.uri,
            "creator": self.creator,
            "metadata": self.metadata,
            "socials": self.socials,
            "seed_urls": self.seed_urls,
            "crawled_pages": [asdict(page) for page in self.crawled_pages],
            "useful_links": self.useful_links,
            "score": self.score,
            "verdict": self.verdict,
            "contract_found": self.contract_found,
            "contract_evidence": self.contract_evidence,
            "utility_signals": self.utility_signals,
            "infra_signals": self.infra_signals,
            "meme_signals": self.meme_signals,
            "score_breakdown": self.score_breakdown,
            "github_repos": [asdict(repo) for repo in self.github_repos],
            "program_snapshots": [asdict(program) for program in self.program_snapshots],
            "reasons": self.reasons,
        }


class _HTMLLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: list[str] = []
        self.text: list[str] = []
        self.links: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() == "a":
            for k, v in attrs:
                if k.lower() == "href" and v:
                    self.links.append(html.unescape(v))
        elif tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str):
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str):
        txt = data.strip()
        if not txt:
            return
        if self._in_title:
            self.title.append(txt)
        self.text.append(txt)


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []

    def handle_data(self, data: str):
        txt = data.strip()
        if txt:
            self.text.append(txt)


def _normalize_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    if url.startswith("www."):
        url = "https://" + url
    if not re.match(r"^https?://", url, re.I):
        if re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", url, re.I):
            url = "https://" + url
        else:
            return None
    return url


def _is_tiktok_url(url: str | None) -> bool:
    if not url:
        return False
    low = url.lower()
    return any(domain in low for domain in TIKTOK_DOMAINS)


def _has_tiktok_signal(metadata: dict[str, Any], socials: dict[str, str | None]) -> bool:
    for value in socials.values():
        if _is_tiktok_url(value):
            return True
    for value in metadata.values():
        if isinstance(value, str) and ("tiktok.com" in value.lower() or "@tiktok" in value.lower()):
            return True
    return False


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    return parsed.netloc.lower() if parsed.netloc else None


def _is_social_host(url: str | None) -> bool:
    host = _host_of(url)
    return bool(host and host in SOCIAL_HOSTS)


def _twitter_handle_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in SOCIAL_HOSTS:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    handle = parts[0]
    handle = handle.lstrip("@")
    handle = re.sub(r"[?#].*$", "", handle)
    if handle.lower() in {"intent", "share", "home", "search", "i"}:
        return None
    return handle or None


def _github_repo_from_url(url: str | None) -> tuple[str, str] | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in GITHUB_HOSTS:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    repo = repo.removesuffix(".git")
    repo = repo.split("#", 1)[0].split("?", 1)[0]
    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        return None
    if repo in {"tree", "blob", "issues", "pull", "pulls"} and len(parts) >= 3:
        repo = parts[1]
    return owner, repo


def _find_program_claims(metadata: dict[str, Any], crawled_pages: list[PageSnapshot]) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    blobs: list[tuple[str, str]] = []
    for source, value in [("metadata", metadata.get("description") or ""), ("metadata", metadata.get("summary") or ""), ("metadata", metadata.get("about") or "")]:
        if isinstance(value, str) and value:
            blobs.append((source, value))
    for page in crawled_pages:
        blob = " ".join(filter(None, [page.title or "", page.description or "", page.text or ""]))
        if blob:
            blobs.append((page.url, blob))
    for source, blob in blobs:
        low = blob.lower()
        if not any(re.search(pattern, low, flags=re.I) for pattern in PROGRAM_CLAIM_PATTERNS):
            continue
        if re.search(rf"\b{PUBKEY_PATTERN}\b", blob):
            claims.append((source, blob))
    return claims


def _extract_program_addresses(text: str) -> list[str]:
    matches: list[str] = []
    patterns = [
        r"program id[:\s=]*([1-9A-HJ-NP-Za-km-z]{32,44})",
        r"contract address[:\s=]*([1-9A-HJ-NP-Za-km-z]{32,44})",
        r"contract[:\s=]*([1-9A-HJ-NP-Za-km-z]{32,44})",
        r"program[:\s=]*([1-9A-HJ-NP-Za-km-z]{32,44})",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text, flags=re.I):
            addr = m.group(1)
            if addr and addr not in matches:
                matches.append(addr)
    return matches


def _is_blog_style_url(url: str | None) -> bool:
    if not url:
        return False
    low = url.lower()
    return any(re.search(pattern, low) for pattern in BLOG_PATH_HINTS)


def _find_contract_evidence(
    mint: str,
    seed_urls: list[str],
    crawled_pages: list[PageSnapshot],
) -> tuple[bool, str | None]:
    official_hosts = set()
    for url in seed_urls:
        host = _host_of(url)
        if host:
            official_hosts.add(host)
    for page in crawled_pages:
        page_host = _host_of(page.url)
        if official_hosts and page_host not in official_hosts and page_host not in {"github.com", "www.github.com"}:
            continue
        blob = " ".join(filter(None, [page.title or "", page.description or "", page.text or "", " ".join(page.links)]))
        if mint in blob:
            return True, page.url
        if re.search(rf"\b(?:contract|ca|mint)\b[:\s#-]*{re.escape(mint)}\b", blob, flags=re.I):
            return True, page.url
    return False, None


def _extract_from_metadata(metadata: dict[str, Any]) -> dict[str, str | None]:
    low = {str(k).lower(): v for k, v in metadata.items() if isinstance(k, str)}
    socials: dict[str, str | None] = {
        "twitter": _normalize_url(low.get("twitter") or low.get("x") or low.get("twitter_url")),
        "telegram": _normalize_url(low.get("telegram") or low.get("telegram_url")),
        "website": _normalize_url(low.get("website") or low.get("url") or low.get("homepage")),
    }
    for v in metadata.values():
        if not isinstance(v, str):
            continue
        text = v.lower()
        if not socials["twitter"] and ("x.com/" in text or "twitter.com/" in text):
            socials["twitter"] = _normalize_url(v)
        if not socials["telegram"] and "t.me/" in text:
            socials["telegram"] = _normalize_url(v)
        if not socials["website"] and text.startswith("http") and not any(
            bad in text for bad in ("twitter", "x.com", "t.me", "pump.fun", "ipfs", "arweave")
        ):
            socials["website"] = _normalize_url(v)
    return socials


def fetch_json_metadata(uri: str | None, timeout: float = 10.0) -> dict[str, Any]:
    if not uri:
        return {}
    if uri.startswith("ipfs://"):
        uri = "https://ipfs.io/ipfs/" + uri.removeprefix("ipfs://")
    elif uri.startswith("ar://"):
        uri = "https://arweave.net/" + uri.removeprefix("ar://")
    try:
        resp = _SESSION.get(uri, timeout=timeout)
        resp.raise_for_status()
        return resp.json() if isinstance(resp.json(), dict) else {}
    except Exception:
        return {}


def fetch_dexscreener_metadata(mint: str, timeout: float = 12.0) -> dict[str, str | None]:
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        resp = _SESSION.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception:
        return {}
    pairs = data.get("pairs") or []
    if not pairs:
        return {}
    pairs.sort(key=lambda p: ((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
    best = pairs[0]
    info = best.get("info") or {}
    socials: dict[str, str | None] = {"twitter": None, "telegram": None, "website": None}
    for item in info.get("socials") or []:
        t = (item.get("type") or "").lower()
        u = item.get("url")
        if t == "twitter" and not socials["twitter"]:
            socials["twitter"] = _normalize_url(u)
        elif t == "telegram" and not socials["telegram"]:
            socials["telegram"] = _normalize_url(u)
    websites = info.get("websites") or []
    if websites:
        first = websites[0]
        socials["website"] = _normalize_url(first.get("url") if isinstance(first, dict) else first)
    return socials


def _github_last_page_number(link_header: str | None) -> int | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="last"' not in part:
            continue
        m = re.search(r"[?&]page=(\d+)", part)
        if m:
            return int(m.group(1))
    return None


def _github_repo_snapshot(url: str, launch_time: str | None = None, timeout: float = 12.0) -> GithubRepoSnapshot | None:
    repo_ref = _github_repo_from_url(url)
    if not repo_ref:
        return None
    owner, repo = repo_ref
    api_base = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        repo_resp = _GITHUB_SESSION.get(api_base, timeout=timeout)
        if repo_resp.status_code != 200:
            return None
        repo_data = repo_resp.json() or {}
    except Exception:
        return None

    created_at = repo_data.get("created_at")
    pushed_at = repo_data.get("pushed_at")
    default_branch = repo_data.get("default_branch")
    language = repo_data.get("language")
    archived = repo_data.get("archived")
    notes: list[str] = []
    if archived:
        notes.append("repository is archived")

    commit_count = None
    first_commit_at = None
    try:
        commits_resp = _GITHUB_SESSION.get(f"{api_base}/commits?per_page=1", timeout=timeout)
        if commits_resp.status_code == 200:
            commit_count = _github_last_page_number(commits_resp.headers.get("Link")) or 1
            commits_data = commits_resp.json() or []
            if commit_count and commit_count > 1:
                oldest_resp = _GITHUB_SESSION.get(f"{api_base}/commits?per_page=1&page={commit_count}", timeout=timeout)
                if oldest_resp.status_code == 200:
                    oldest_data = oldest_resp.json() or []
                    if oldest_data:
                        first_commit_at = ((oldest_data[0] or {}).get("commit") or {}).get("committer", {}).get("date")
            elif commits_data:
                first_commit_at = ((commits_data[-1] or {}).get("commit") or {}).get("committer", {}).get("date")
    except Exception:
        notes.append("commit history unavailable")

    contributor_count = None
    try:
        contrib_resp = _GITHUB_SESSION.get(f"{api_base}/contributors?per_page=1&anon=1", timeout=timeout)
        if contrib_resp.status_code == 200:
            contributor_count = _github_last_page_number(contrib_resp.headers.get("Link")) or len(contrib_resp.json() or [])
    except Exception:
        notes.append("contributor history unavailable")

    repository_age_days = None
    predates_launch = None
    if created_at:
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            repository_age_days = max(0, (datetime.now(timezone.utc) - created_dt).days)
            if launch_time:
                launch_dt = datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
                predates_launch = created_dt <= launch_dt
        except Exception:
            notes.append("repo timestamp parsing failed")

    if first_commit_at and launch_time:
        try:
            commit_dt = datetime.fromisoformat(first_commit_at.replace("Z", "+00:00"))
            launch_dt = datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
            if commit_dt <= launch_dt:
                notes.append("first commit predates token launch")
            else:
                notes.append("first commit after token launch")
        except Exception:
            pass

    return GithubRepoSnapshot(
        url=url,
        owner=owner,
        repo=repo,
        created_at=created_at,
        pushed_at=pushed_at,
        default_branch=default_branch,
        language=language,
        archived=archived,
        commit_count=commit_count,
        first_commit_at=first_commit_at,
        contributor_count=contributor_count,
        repository_age_days=repository_age_days,
        predates_launch=predates_launch,
        notes=notes,
    )


def collect_github_repos(seed_urls: list[str], crawled_pages: list[PageSnapshot], launch_time: str | None = None) -> list[GithubRepoSnapshot]:
    repos: list[GithubRepoSnapshot] = []
    seen: set[tuple[str, str]] = set()
    candidates = list(seed_urls)
    for page in crawled_pages:
        candidates.append(page.url)
        candidates.extend(page.links)
    for url in candidates:
        repo_ref = _github_repo_from_url(url)
        if not repo_ref or repo_ref in seen:
            continue
        seen.add(repo_ref)
        snap = _github_repo_snapshot(url, launch_time=launch_time)
        if snap:
            repos.append(snap)
    return repos


def _verify_program_address(program_address: str, launch_time: str | None = None) -> ProgramSnapshot | None:
    if not HeliusClient().configured:
        return None
    client = HeliusClient()
    snap = ProgramSnapshot(address=program_address)
    try:
        account = client.get_account_info(program_address) or {}
    except Exception as exc:
        snap.notes.append(f"account lookup failed: {str(exc)[:120]}")
        return snap
    value = account.get("value") if isinstance(account, dict) else None
    if not value:
        snap.verified = False
        snap.notes.append("program account not found")
        return snap
    snap.owner = value.get("owner")
    snap.executable = value.get("executable")
    snap.lamports = value.get("lamports")
    snap.verified = bool(value.get("executable")) and bool(value.get("owner"))
    if snap.verified:
        snap.notes.append("account is executable and exists on chain")
    if launch_time:
        try:
            sigs = client.get_signatures_for_address(program_address, limit=1)
            if sigs:
                sig = sigs[0]
                snap.deployment_signature = sig.signature
                if sig.block_time:
                    snap.deployment_time = datetime.fromtimestamp(sig.block_time, tz=timezone.utc).isoformat()
                    launch_dt = datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
                    deploy_dt = datetime.fromisoformat(snap.deployment_time.replace("Z", "+00:00"))
                    snap.notes.append(
                        "program signature predates launch" if deploy_dt <= launch_dt else "program signature after launch"
                    )
        except Exception as exc:
            snap.notes.append(f"deployment lookup failed: {str(exc)[:120]}")
    return snap


def collect_program_snapshots(
    metadata: dict[str, Any],
    crawled_pages: list[PageSnapshot],
    launch_time: str | None = None,
) -> list[ProgramSnapshot]:
    if not HeliusClient().configured:
        return []
    claims = _find_program_claims(metadata, crawled_pages)
    if not claims:
        return []
    addresses: list[str] = []
    for _, blob in claims:
        addresses.extend(_extract_program_addresses(blob))
    if not addresses:
        return [
            ProgramSnapshot(
                address="unknown",
                claimed=True,
                verified=False,
                notes=["claim detected but no Solana program address could be extracted"],
            )
        ]
    seen: set[str] = set()
    out: list[ProgramSnapshot] = []
    for address in addresses:
        if address in seen:
            continue
        seen.add(address)
        snap = _verify_program_address(address, launch_time=launch_time)
        if snap:
            snap.claimed = True
            out.append(snap)
    return out


def fetch_social_profile(url: str | None, timeout: float = 10.0) -> PageSnapshot | None:
    """Fetch a social/profile page when it may contain project links or bio text."""
    normalized = _normalize_url(url)
    if not normalized:
        return None
    if _is_social_host(normalized):
        nitter = _fetch_nitter_social_profile(normalized, timeout=timeout)
        if nitter:
            return nitter
    try:
        resp = _SESSION.get(normalized, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return None
    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        return None
    body = resp.text
    page = _extract_page_snapshot(resp.url, body)
    # Social sites are noisy. Keep only the profile bio/description plus extracted tweet text,
    # rather than the full page shell that often contains unrelated markup.
    page.text = None
    page.description = page.description or _extract_meta_content(
        body,
        ("og:description", "twitter:description", "description"),
    )
    extra_text = _extract_social_text(body)
    if extra_text:
        page.text = extra_text
    # Add metadata-driven social and project links that are commonly exposed in the page text.
    page.links.extend(_scan_text_for_useful_links(body))
    uniq: list[str] = []
    seen: set[str] = set()
    for link in page.links:
        norm = _normalize_url(link)
        if norm and norm not in seen:
            uniq.append(norm)
            seen.add(norm)
    page.links = uniq
    return page


def _extract_meta_content(body: str, names: tuple[str, ...]) -> str | None:
    patterns = []
    for name in names:
        escaped = re.escape(name)
        patterns.append(
            rf'<meta[^>]+(?:property|name)=["\']{escaped}["\'][^>]+content=["\']([^"\']+)',
        )
    for pattern in patterns:
        m = re.search(pattern, body, flags=re.I)
        if m:
            return html.unescape(m.group(1))
    return None


def _extract_social_text(body: str, max_chars: int = 2500) -> str | None:
    chunks: list[str] = []
    for pattern in (
        r"<article\b[^>]*>(.*?)</article>",
        r'<div[^>]+data-testid=["\']tweetText["\'][^>]*>(.*?)</div>',
        r'<div[^>]+lang=["\'][^"\']+["\'][^>]*>(.*?)</div>',
    ):
        for match in re.finditer(pattern, body, flags=re.I | re.S):
            fragment = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", match.group(1), flags=re.I | re.S)
            fragment = re.sub(r"<[^>]+>", " ", fragment)
            fragment = html.unescape(fragment)
            fragment = re.sub(r"\s+", " ", fragment).strip()
            if fragment:
                chunks.append(fragment)
    if not chunks:
        return None
    text = " ".join(chunks)
    return text[:max_chars]


def _strip_html_fragment(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_nitter_rss(xml_text: str, source_url: str) -> PageSnapshot | None:
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None

    channel = root.find("channel")
    if channel is None:
        return None

    title = channel.findtext("title")
    description = channel.findtext("description")
    feed_links: list[str] = []
    tweets: list[str] = []
    seen_items: set[str] = set()

    for item in channel.findall("item"):
        item_title = _strip_html_fragment(item.findtext("title") or "")
        item_desc = _strip_html_fragment(item.findtext("description") or "")
        content_nodes = item.findall("{http://purl.org/rss/1.0/modules/content/}encoded")
        item_content = ""
        if content_nodes:
            item_content = _strip_html_fragment(" ".join(node.text or "" for node in content_nodes))
        guid = _strip_html_fragment(item.findtext("guid") or "")
        link = _normalize_url(item.findtext("link"))
        cache_key = guid or link or item_title or item_desc
        if cache_key in seen_items:
            continue
        seen_items.add(cache_key)
        tweet_blob = " ".join(part for part in (item_title, item_desc, item_content) if part)
        if tweet_blob:
            tweets.append(tweet_blob)
        if link:
            feed_links.append(link)

    merged_text = "\n".join(tweets)[:2500] if tweets else None
    uniq_links: list[str] = []
    seen_links: set[str] = set()
    for link in feed_links:
        if link not in seen_links:
            uniq_links.append(link)
            seen_links.add(link)

    return PageSnapshot(
        url=source_url,
        title=_strip_html_fragment(title or "") or None,
        description=_strip_html_fragment(description or "") or None,
        text=merged_text,
        links=uniq_links,
    )


def _fetch_nitter_social_profile(url: str | None, timeout: float = 10.0) -> PageSnapshot | None:
    handle = _twitter_handle_from_url(url)
    if not handle:
        return None
    merged_pages: list[PageSnapshot] = []
    for mirror in NITTER_MIRRORS:
        feed_url = f"{mirror.rstrip('/')}/{handle}/rss"
        try:
            resp = _SESSION.get(feed_url, timeout=timeout, allow_redirects=True)
            if resp.status_code != 200:
                print(f"[nitter] {handle} {mirror} http={resp.status_code}")
                continue
            if not resp.text.strip():
                print(f"[nitter] {handle} {mirror} empty_feed")
                continue
            page = _parse_nitter_rss(resp.text, source_url=url)
            if page:
                text_len = len(page.text or "")
                item_count = len(page.links)
                print(f"[nitter] {handle} {mirror} ok text={text_len} links={item_count}")
                merged_pages.append(page)
            else:
                print(f"[nitter] {handle} {mirror} parse_failed")
        except Exception:
            print(f"[nitter] {handle} {mirror} error")
            continue
    if not merged_pages:
        print(f"[nitter] {handle} no_feeds")
        return None

    title = next((page.title for page in merged_pages if page.title), None)
    description = next((page.description for page in merged_pages if page.description), None)
    text_parts: list[str] = []
    links: list[str] = []
    seen_text: set[str] = set()
    seen_links: set[str] = set()
    for page in merged_pages:
        if page.text and page.text not in seen_text:
            text_parts.append(page.text)
            seen_text.add(page.text)
        for link in page.links:
            if link not in seen_links:
                links.append(link)
                seen_links.add(link)
    return PageSnapshot(
        url=url or merged_pages[0].url,
        title=title,
        description=description,
        text="\n".join(text_parts)[:2500] if text_parts else None,
        links=links,
    )


def _dedupe_pages(pages: list[PageSnapshot]) -> list[PageSnapshot]:
    uniq: list[PageSnapshot] = []
    seen: set[str] = set()
    for page in pages:
        if page.url in seen:
            continue
        uniq.append(page)
        seen.add(page.url)
    return uniq


def _scan_text_for_useful_links(text: str) -> list[str]:
    links = []
    for match in re.finditer(r"https?://[^\s\"'<>]+", text, flags=re.I):
        url = _normalize_url(match.group(0))
        if url:
            links.append(url)
    return links


def _extract_page_snapshot(url: str, body: str, max_text: int = 2000) -> PageSnapshot:
    parser = _HTMLLinkParser()
    try:
        parser.feed(body)
    except Exception:
        pass
    title = " ".join(parser.title).strip() or None
    raw_text = " ".join(parser.text).strip()
    text = re.sub(r"\s+", " ", raw_text)[:max_text] or None
    links = []
    for href in parser.links:
        norm = _normalize_url(urljoin(url, href))
        if norm:
            links.append(norm)
    # also scan visible text for bare URLs
    links.extend(_scan_text_for_useful_links(body))
    # dedupe while preserving order
    uniq: list[str] = []
    seen: set[str] = set()
    for link in links:
        if link not in seen:
            uniq.append(link)
            seen.add(link)
    description = None
    m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', body, flags=re.I)
    if m:
        description = html.unescape(m.group(1))
    return PageSnapshot(url=url, title=title, description=description, text=text, links=uniq)


def crawl_project_sites(seed_urls: list[str], *, max_pages: int | None = None, max_depth: int | None = None) -> list[PageSnapshot]:
    max_pages = max_pages or SETTINGS.utility_crawl_pages
    max_depth = max_depth if max_depth is not None else SETTINGS.utility_crawl_depth
    queue = deque([(u, 0) for u in seed_urls if u])
    seen: set[str] = set()
    pages: list[PageSnapshot] = []
    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        try:
            resp = _SESSION.get(url, timeout=10.0, allow_redirects=True)
            if "text/html" not in (resp.headers.get("Content-Type") or "").lower():
                continue
            resp.raise_for_status()
            page = _extract_page_snapshot(resp.url, resp.text)
            pages.append(page)
            if depth < max_depth:
                for link in page.links:
                    if link not in seen:
                        queue.append((link, depth + 1))
        except Exception:
            continue
    return pages


def _contains_any(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)


def _keyword_hits(text: str, terms: set[str]) -> list[str]:
    hits = []
    for term in sorted(terms, key=len, reverse=True):
        if term in text:
            hits.append(term)
    return hits


def _bounded(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, value))


def score_utility_project(
    *,
    name: str | None,
    symbol: str | None,
    metadata: dict[str, Any],
    socials: dict[str, str | None],
    crawled_pages: list[PageSnapshot],
) -> tuple[int, str, list[str], list[str], int, int, int, dict[str, Any]]:
    score = 0
    reasons: list[str] = []
    useful_links: list[str] = []
    utility_signals = 0
    infra_signals = 0
    meme_signals = 0

    blob = " ".join(
        str(v) for v in [
            name or "",
            symbol or "",
            metadata.get("description") or "",
            metadata.get("summary") or "",
            metadata.get("about") or "",
        ]
    ).lower()

    if socials.get("website"):
        score += 2
        reasons.append("website link present")
        utility_signals += 1
    if socials.get("twitter"):
        score += 1
        reasons.append("twitter/x link present")
    if socials.get("telegram"):
        score += 1
        reasons.append("telegram link present")

    infra_hits = _keyword_hits(blob, INFRA_KEYWORDS)
    if infra_hits:
        score += 3
        reasons.append(f"metadata contains infrastructure-oriented language: {', '.join(infra_hits[:5])}")
        utility_signals += 2
        infra_signals += 2

    blob_hits = _keyword_hits(blob, UTILITY_KEYWORDS)
    if blob_hits:
        score += 2
        reasons.append(f"metadata contains utility-oriented language: {', '.join(blob_hits[:5])}")
        utility_signals += 2
    meme_hits = _keyword_hits(blob, MEME_KEYWORDS)
    if meme_hits:
        score -= 2
        reasons.append(f"metadata contains meme-oriented language: {', '.join(meme_hits[:5])}")
        meme_signals += 1

    for page in crawled_pages:
        page_blob = " ".join(filter(None, [page.title or "", page.description or "", page.text or ""])).lower()
        if _is_blog_style_url(page.url):
            score -= 2
            reasons.append(f"blog/article-style page on {page.url}")
            meme_signals += 1
        if _contains_any(page_blob, BLOG_TEXT_HINTS):
            score -= 1
            reasons.append(f"blog/article language on {page.url}")
            meme_signals += 1
        if "/docs" in page.url.lower() or any(term in page_blob for term in ("documentation", "getting started", "how it works", "managed agents")):
            score += 3
            reasons.append(f"dedicated docs/product page on {page.url}")
            utility_signals += 2
        page_infra_hits = _keyword_hits(page_blob, INFRA_KEYWORDS)
        if page_infra_hits:
            score += 2
            reasons.append(f"infrastructure language on {page.url}: {', '.join(page_infra_hits[:5])}")
            utility_signals += 2
            infra_signals += 2
        page_utility_hits = _keyword_hits(page_blob, UTILITY_KEYWORDS)
        if page_utility_hits:
            score += 1
            reasons.append(f"utility language on {page.url}: {', '.join(page_utility_hits[:5])}")
            utility_signals += 2
        page_meme_hits = [] if _is_social_host(page.url) else _keyword_hits(page_blob, MEME_KEYWORDS)
        if page_meme_hits:
            score -= 1
            reasons.append(f"meme language on {page.url}: {', '.join(page_meme_hits[:5])}")
            meme_signals += 1
        for link in page.links:
            l = link.lower()
            if any(hint in l for hint in RESEARCH_LINK_HINTS):
                score += 2
                useful_links.append(link)
                reasons.append(f"research link found: {link}")
                utility_signals += 2
            elif "github.com" in l or "docs." in l:
                score += 2
                useful_links.append(link)
                reasons.append(f"useful external link found: {link}")
                utility_signals += 2

    useful_links = list(dict.fromkeys(useful_links))
    project_relevance = _bounded(
        utility_signals * 10 + infra_signals * 12 - meme_signals * 8 + (5 if socials.get("website") else 0),
    )
    evidence_quality = _bounded(
        (15 if socials.get("website") else 0)
        + (12 if socials.get("twitter") else 0)
        + (12 if socials.get("telegram") else 0)
        + (20 if useful_links else 0)
        + (18 if any("github.com" in link.lower() for link in useful_links) else 0)
        + (18 if any("docs" in link.lower() or "whitepaper" in link.lower() or "litepaper" in link.lower() for link in useful_links) else 0)
        + (18 if crawled_pages else 0),
    )
    execution_score = _bounded(
        (25 if crawled_pages else 0)
        + (20 if useful_links else 0)
        + min(len(crawled_pages) * 10, 30)
        + min(sum(1 for page in crawled_pages if page.links), 20),
    )
    market_risk = _bounded(
        (70 if meme_signals >= 2 else 0)
        + (35 if meme_signals == 1 else 0)
        + (25 if not useful_links else 0)
        + (15 if not socials.get("website") else 0),
    )
    analysis_confidence = _bounded(
        (25 if evidence_quality >= 40 else 0)
        + (20 if evidence_quality >= 60 else 0)
        + (20 if useful_links else 0)
        + (10 if crawled_pages else 0)
        + (10 if meme_signals == 0 else 0)
        + (10 if utility_signals >= 4 or infra_signals >= 2 else 0),
    )
    if infra_signals >= 2 and meme_signals <= 1 and len(useful_links) >= 1:
        verdict = "infra_candidate"
    elif utility_signals >= 5 and meme_signals <= 1 and len(useful_links) >= 1:
        verdict = "utility_candidate"
    elif utility_signals >= 4 and meme_signals <= 2 and len(useful_links) >= 1:
        verdict = "possible_utility"
    elif meme_signals >= 2 and infra_signals < 2 and utility_signals <= 4:
        verdict = "meme_candidate"
    else:
        verdict = "unclear"
    if verdict in {"utility_candidate", "infra_candidate"} and analysis_confidence >= 70 and market_risk < 30:
        alert_tier = "Review"
    elif verdict in {"utility_candidate", "infra_candidate"} and analysis_confidence >= 45:
        alert_tier = "Watch"
    elif market_risk >= 50:
        alert_tier = "Urgent Risk"
    else:
        alert_tier = "Watch"
    score_breakdown = {
        "project_relevance": project_relevance,
        "evidence_quality": evidence_quality,
        "execution_score": execution_score,
        "market_risk": market_risk,
        "analysis_confidence": analysis_confidence,
        "alert_tier": alert_tier,
    }
    return score, verdict, reasons, useful_links, utility_signals, infra_signals, meme_signals, score_breakdown


def build_project_research(
    *,
    mint: str,
    name: str | None = None,
    symbol: str | None = None,
    uri: str | None = None,
    creator: str | None = None,
    token_metadata: dict[str, Any] | None = None,
    launch_time: str | None = None,
) -> ProjectResearch:
    token_metadata = token_metadata or {}
    socials = _extract_from_metadata(token_metadata)
    dex_socials = fetch_dexscreener_metadata(mint)
    for key, value in dex_socials.items():
        if not socials.get(key) and value:
            socials[key] = value

    if _has_tiktok_signal(token_metadata, socials):
        return ProjectResearch(
            mint=mint,
            symbol=symbol,
            name=name,
            uri=uri,
            creator=creator,
            metadata=token_metadata,
            socials=socials,
            seed_urls=[],
            crawled_pages=[],
            useful_links=[],
            score=0,
            verdict="tiktok_excluded",
            contract_found=False,
            utility_signals=0,
            infra_signals=0,
            meme_signals=0,
            reasons=["tiktok social or website detected; excluded from analysis"],
        )

    seed_urls: list[str] = []
    for key in ("website", "twitter", "telegram"):
        url = socials.get(key)
        if url:
            if _is_tiktok_url(url):
                continue
            seed_urls.append(url)

    profile_pages: list[PageSnapshot] = []
    for social_key in ("twitter", "telegram"):
        profile = fetch_social_profile(socials.get(social_key))
        if profile:
            profile_pages.append(profile)
            for link in profile.links:
                if link not in seed_urls:
                    seed_urls.append(link)
    crawled_pages = _dedupe_pages(profile_pages + crawl_project_sites(seed_urls))
    contract_found, contract_evidence = _find_contract_evidence(mint, seed_urls, crawled_pages)
    program_snapshots = collect_program_snapshots(token_metadata, crawled_pages, launch_time=launch_time)
    if not contract_found:
        return ProjectResearch(
            mint=mint,
            symbol=symbol,
            name=name,
            uri=uri,
            creator=creator,
            metadata=token_metadata,
            socials=socials,
            seed_urls=seed_urls,
            crawled_pages=crawled_pages,
            useful_links=[],
            score=0,
            verdict="contract_not_found",
            contract_found=False,
            contract_evidence=None,
            utility_signals=0,
            infra_signals=0,
            meme_signals=0,
            reasons=["token contract address was not found on the website, docs, or GitHub"],
        )
    github_repos = collect_github_repos(seed_urls, crawled_pages, launch_time=launch_time)
    score, verdict, reasons, useful_links, utility_signals, infra_signals, meme_signals, score_breakdown = score_utility_project(
        name=name,
        symbol=symbol,
        metadata=token_metadata,
        socials=socials,
        crawled_pages=crawled_pages,
    )
    if github_repos:
        repo_notes: list[str] = []
        repo_score_boost = 0
        repo_quality_boost = 0
        repo_execution_boost = 0
        for repo in github_repos:
            if repo.created_at:
                repo_notes.append(f"github repo discovered: {repo.owner}/{repo.repo} created {repo.created_at}")
                repo_quality_boost += 8
            if repo.commit_count is not None:
                repo_notes.append(f"github commit history: {repo.commit_count} commits")
                repo_execution_boost += min(10, max(0, repo.commit_count // 5))
            if repo.first_commit_at:
                repo_notes.append(f"first commit at {repo.first_commit_at}")
            if repo.contributor_count is not None:
                repo_notes.append(f"contributors: {repo.contributor_count}")
                repo_quality_boost += min(6, repo.contributor_count)
            if repo.predates_launch is True:
                repo_notes.append("repository predates token launch")
                repo_quality_boost += 12
                repo_execution_boost += 8
            elif repo.predates_launch is False:
                repo_notes.append("repository created after token launch")
                repo_quality_boost -= 8
            if repo.archived:
                repo_notes.append("repository is archived")
                repo_execution_boost -= 4
        score += repo_score_boost + repo_quality_boost + repo_execution_boost
        reasons.extend(repo_notes)
        score_breakdown["evidence_quality"] = _bounded(score_breakdown.get("evidence_quality", 0) + repo_quality_boost)
        score_breakdown["execution_score"] = _bounded(score_breakdown.get("execution_score", 0) + repo_execution_boost)
        if github_repos:
            score_breakdown["github_repositories"] = [asdict(repo) for repo in github_repos]
            score_breakdown["github_repo_count"] = len(github_repos)
            if any(repo.predates_launch is True for repo in github_repos):
                score_breakdown["github_authenticity"] = "stronger"
            elif any(repo.predates_launch is False for repo in github_repos):
                score_breakdown["github_authenticity"] = "weaker"

    if program_snapshots:
        program_notes: list[str] = []
        verified = [snap for snap in program_snapshots if snap.verified]
        if verified:
            for snap in verified:
                program_notes.append(f"claimed Solana program verified: {snap.address}")
                if snap.deployment_time:
                    program_notes.append(f"program deployment observed at {snap.deployment_time}")
            score_breakdown["deployment_verification"] = {
                "claimed": True,
                "verified": True,
                "program_count": len(program_snapshots),
                "verified_programs": [asdict(snap) for snap in verified],
            }
            score_breakdown["evidence_quality"] = _bounded(score_breakdown.get("evidence_quality", 0) + 15)
            score_breakdown["execution_score"] = _bounded(score_breakdown.get("execution_score", 0) + 12)
            score += 10
        else:
            program_notes.append("project claimed an on-chain deployment but verification was inconclusive")
            score_breakdown["deployment_verification"] = {
                "claimed": True,
                "verified": False,
                "program_count": len(program_snapshots),
                "programs": [asdict(snap) for snap in program_snapshots],
            }
        reasons.extend(program_notes)

    return ProjectResearch(
        mint=mint,
        symbol=symbol,
        name=name,
        uri=uri,
        creator=creator,
        metadata=token_metadata,
        socials=socials,
        seed_urls=seed_urls,
        crawled_pages=crawled_pages,
        useful_links=useful_links,
        score=score,
        verdict=verdict,
        contract_found=contract_found,
        contract_evidence=contract_evidence,
        utility_signals=utility_signals,
        infra_signals=infra_signals,
        meme_signals=meme_signals,
        score_breakdown=score_breakdown,
        github_repos=github_repos,
        program_snapshots=program_snapshots,
        reasons=reasons,
    )
