"""Token metadata and project-link research helpers."""

from __future__ import annotations

import html
import json
import re
from collections import deque
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from .config import SETTINGS

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "solana-wallet-investigator/0.1"})

UTILITY_KEYWORDS = {
    "ai",
    "agent",
    "agents",
    "compute",
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
    "docs",
    "whitepaper",
    "litepaper",
}

INFRA_KEYWORDS = {
    "ai",
    "agent",
    "agents",
    "compute",
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
}

MEME_KEYWORDS = {
    "pepe",
    "doge",
    "inu",
    "wojak",
    "moon",
    "pump",
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


def _is_blog_style_url(url: str | None) -> bool:
    if not url:
        return False
    low = url.lower()
    return any(re.search(pattern, low) for pattern in BLOG_PATH_HINTS)


def _find_contract_evidence(
    mint: str,
    website: str | None,
    crawled_pages: list[PageSnapshot],
) -> tuple[bool, str | None]:
    site_host = _host_of(website)
    for page in crawled_pages:
        if site_host and _host_of(page.url) != site_host:
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


def fetch_social_profile(url: str | None, timeout: float = 10.0) -> PageSnapshot | None:
    """Fetch a social/profile page when it may contain project links or bio text."""
    normalized = _normalize_url(url)
    if not normalized:
        return None
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
    page.description = page.description or _extract_meta_content(
        body,
        ("og:description", "twitter:description", "description"),
    )
    extra_text = _extract_social_text(body)
    if extra_text:
        page.text = f"{page.text or ''} {extra_text}".strip()
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


def score_utility_project(
    *,
    name: str | None,
    symbol: str | None,
    metadata: dict[str, Any],
    socials: dict[str, str | None],
    crawled_pages: list[PageSnapshot],
) -> tuple[int, str, list[str], list[str], int, int, int]:
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
        page_meme_hits = _keyword_hits(page_blob, MEME_KEYWORDS)
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
    if infra_signals >= 2 and meme_signals == 0 and len(useful_links) >= 1:
        verdict = "infra_candidate"
    elif utility_signals >= 5 and meme_signals == 0 and len(useful_links) >= 1:
        verdict = "utility_candidate"
    elif utility_signals >= 4 and meme_signals <= 1 and len(useful_links) >= 1:
        verdict = "possible_utility"
    elif meme_signals >= 1 and infra_signals < 2 and utility_signals <= 4:
        verdict = "meme_candidate"
    else:
        verdict = "unclear"
    return score, verdict, reasons, useful_links, utility_signals, infra_signals, meme_signals


def build_project_research(
    *,
    mint: str,
    name: str | None = None,
    symbol: str | None = None,
    uri: str | None = None,
    creator: str | None = None,
    token_metadata: dict[str, Any] | None = None,
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
    contract_found, contract_evidence = _find_contract_evidence(mint, socials.get("website"), crawled_pages)
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
            reasons=["token contract address was not found on the website"],
        )
    score, verdict, reasons, useful_links, utility_signals, infra_signals, meme_signals = score_utility_project(
        name=name,
        symbol=symbol,
        metadata=token_metadata,
        socials=socials,
        crawled_pages=crawled_pages,
    )

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
        reasons=reasons,
    )
