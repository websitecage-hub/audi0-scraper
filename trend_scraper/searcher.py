"""Search-engine discovery for niche-targeted trend pages.

Multiple engines are tried in order because each blocks differently:

  bing       works over plain HTTP (no browser) -> the API/free-tier path
  ddg-html   often 202-challenges plain HTTP; works through stealth
  ddg-lite   JS-free but heavily challenged; works through stealth

The engine picks the first that returns results, so the CLI (with a browser) can
use the stealth engines while the free-tier API uses the HTTP-only path.
"""
from __future__ import annotations

import html as _html
import logging
import re
from urllib.parse import quote, unquote

from .fetcher import HTTPFetcher, StealthFetcher

log = logging.getLogger("trend.audio.search")

# DuckDuckGo redirect links:  href="//duckduckgo.com/l/?uddg=<urlencoded>&rut=..."
_UDDG_RE = re.compile(r'href="//duckduckgo\.com/l/\?uddg=([^&"]+)')
# Plain result links (bing/others):  href="https://example.com/..."
_PLAIN_RE = re.compile(r'href="(https?://[^"]+)"')

NOISE = ("duckduckgo.com", "google.com", "bing.com", "yandex", "microsoft.com",
         "msn.com", "w3.org", "schema.org", "facebook.com", "twitter.com",
         "pinterest.com", "youtube.com", "tiktok.com", "amazon.com")

# Domains likely to actually contain "trending reels audio" lists.
_GOOD_HINT = ("trend", "instagram", "reel", "song", "music", "viral", "audio",
              "social", "tiktok", "blog")


def _likely_content(link: str) -> bool:
    low = link.lower()
    if any(n in low for n in NOISE):
        return False
    return any(h in low for h in _GOOD_HINT)


def _clean(url: str) -> str:
    return unquote(_html.unescape(url)).strip()


def _titles_from_bing(html: str) -> list[str]:
    return [_clean(u) for u in _PLAIN_RE.findall(html)]


def _titles_from_ddg(html: str) -> list[str]:
    return [_clean(u) for u in _UDDG_RE.findall(html)]


def _dedupe(links: list[str], max_results: int) -> list[str]:
    out, seen = [], set()
    for link in links:
        if not link or link in seen:
            continue
        if not _likely_content(link):
            continue
        seen.add(link)
        out.append(link)
        if len(out) >= max_results:
            break
    return out


def ddg_lite_search(query: str, max_results: int = 8,
                    stealth: StealthFetcher | None = None) -> list[str]:
    """One DuckDuckGo Lite search through the stealth browser (CLI path)."""
    stealth = stealth or StealthFetcher()
    url = "https://lite.duckduckgo.com/lite/?q=" + query.replace(" ", "+")
    status, body = stealth.fetch(url)
    if status is None or not (200 <= status < 300):
        log.warning("DDG lite search failed (status=%s) for: %s", status, query)
        return []
    return _dedupe(_titles_from_ddg(body.decode("utf-8", "ignore")), max_results)


def http_search(query: str, max_results: int = 8,
                http: HTTPFetcher | None = None) -> list[str]:
    """Browser-free multi-engine search — the free-tier API path.

    Bing is fetched first because it answers plain HTTP requests; DDG HTML is a
    fallback. Returns [] if every engine blocks us (the caller still has the
    aggregator feeds).
    """
    http = http or HTTPFetcher()
    engines = [
        ("bing", "https://www.bing.com/search?q=", _titles_from_bing),
        ("ddg-html", "https://html.duckduckgo.com/html/?q=", _titles_from_ddg),
    ]
    for name, prefix, parser in engines:
        status, body = http.fetch(prefix + quote(query), retries=1)
        if not status or not (200 <= status < 300) or not body:
            log.debug("engine %s blocked (status=%s) for %s", name, status, query)
            continue
        links = _dedupe(parser(body.decode("utf-8", "ignore")), max_results)
        if links:
            log.info("engine %s -> %d links for %r", name, len(links), query)
            return links
    return []