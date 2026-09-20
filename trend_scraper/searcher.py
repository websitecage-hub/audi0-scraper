"""Search-engine discovery via DuckDuckGo Lite (JS-free, bot-tolerant through stealth)."""
from __future__ import annotations

import logging
import re
from urllib.parse import unquote

from .fetcher import StealthFetcher

log = logging.getLogger("trend.audio.search")

# DuckDuckGo Lite redirect links look like:
#   href="//duckduckgo.com/l/?uddg=<urlencoded-target>&rut=..."
_UDDG_RE = re.compile(r'href="//duckduckgo\.com/l/\?uddg=([^&"]+)')

# Ad/sponsored noise domains we never want as "content" links.
NOISE = ("duckduckgo.com", "instagram.com/popular", "google.com", "bing.com", "yandex")


def _clean(url: str) -> str:
    return unquote(url).strip()


def ddg_lite_search(query: str, max_results: int = 8, stealth: StealthFetcher | None = None) -> list[str]:
    """Run one DuckDuckGo Lite search; return up to max_results result URLs.

    Uses the stealth browser because DDG blocks plain curl with an 'anomaly' challenge.
    """
    stealth = stealth or StealthFetcher()
    url = "https://lite.duckduckgo.com/lite/?q=" + query.replace(" ", "+")
    status, body = stealth.fetch(url)
    if status is None or not (200 <= status < 300):
        log.warning("DDG lite search failed (status=%s) for: %s", status, query)
        return []

    html = body.decode("utf-8", "ignore")
    out: list[str] = []
    seen: set[str] = set()
    for raw in _UDDG_RE.findall(html):
        link = _clean(raw)
        if not link:
            continue
        host = link.split("/", 3)[2] if "://" in link else ""
        if any(n in host for n in NOISE):
            continue
        if link not in seen:
            seen.add(link)
            out.append(link)
        if len(out) >= max_results:
            break
    return out
