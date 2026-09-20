"""Orchestrator: drive searches + aggregator fetches, niche-score, dedupe, cache."""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from urllib.parse import quote

from .extract import Track, Niche, extract_tracks, score_tracks
from .fetcher import HTTPFetcher, StealthFetcher
from .searcher import ddg_lite_search
from .sources import CURRENT_TRENDING, EVERGREEN

log = logging.getLogger("trend.audio.engine")

# Wayback lookup: returns a working snapshot URL for a blocked source.
_WAYBACK_AVAIL = "https://archive.org/wayback/available?url="


class TrendingAudioEngine:
    def __init__(
        self,
        cache_dir: str | None = None,
        max_search_pages: int = 5,
        max_results: int = 8,
        use_evergreen: bool = True,
    ):
        self.cache_dir = cache_dir
        self.max_search_pages = max_search_pages
        self.max_results = max_results
        self.use_evergreen = use_evergreen
        self.http = HTTPFetcher()
        self.stealth = StealthFetcher()
        os.makedirs(cache_dir, exist_ok=True) if cache_dir else None

    # -- caching ----------------------------------------------------------
    def _cache_path(self, niche: str) -> str:
        key = niche.strip().lower().replace(" ", "-")
        return os.path.join(self.cache_dir or "", f"{key}-{date.today().isoformat()}.json")

    def _load_cache(self, niche: str) -> list[Track] | None:
        if not self.cache_dir:
            return None
        p = self._cache_path(niche)
        if os.path.exists(p):
            try:
                raw = json.load(open(p))
                return [Track(**t) for t in raw]
            except Exception:
                return None
        return None

    def _save_cache(self, niche: str, tracks: list[Track]) -> None:
        if not self.cache_dir:
            return
        with open(self._cache_path(niche), "w") as fh:
            json.dump([t.__dict__ for t in tracks], fh, indent=2)

    # -- fetching helpers ---------------------------------------------------
    def _wayback_snapshot(self, target: str) -> str | None:
        """Find an archived snapshot for a URL that blocked live fetch."""
        try:
            status, body = self.http.fetch(_WAYBACK_AVAIL + quote(target, safe=""))
            if status == 200:
                data = json.loads(body.decode("utf-8", "ignore"))
                closest = data.get("archived_snapshots", {}).get("closest", {})
                if closest.get("available"):
                    return closest["url"]
        except Exception as exc:
            log.debug("wayback lookup failed for %s: %s", target, exc)
        return None

    def _fetch_any(self, url: str, use_stealth: bool = False) -> bytes | None:
        """Fetch HTML, trying stealth then HTTP; fall back to Wayback snapshot."""
        if use_stealth:
            status, body = self.stealth.fetch(url)
            if status and (200 <= status < 300) and body:
                return body
        status, body = self.http.fetch(url)
        if status and (200 <= status < 300) and body:
            return body
        # blocked -> try an archived copy
        snap = self._wayback_snapshot(url) if "archive.org" not in url else None
        if snap:
            status, body = self.http.fetch(snap)
            if status and (200 <= status < 300) and body:
                return body
        return None

    # -- the pipeline -------------------------------------------------------
    def collect(self, niche: Niche, *, use_cache: bool = True) -> list[Track]:
        if use_cache:
            cached = self._load_cache(niche.name)
            if cached:
                log.info("using cached results for niche=%s (%d tracks)",
                         niche.name, len(cached))
                return cached

        all_tracks: dict[tuple[str, str], Track] = {}
        seen_urls: set[str] = set()

        # 1) niche-targeted search pass -> niche blog/list pages
        for q in niche.queries[:4]:
            for i in range(1, 3):  # a couple of query phrasings
                query = f"best {q} songs for instagram reels trending" if i == 1 else \
                        f"{q} reels audio viral trending list"
                hits = ddg_lite_search(query, max_results=self.max_results,
                                       stealth=self.stealth)
                for url in hits:
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    body = self._fetch_any(url)
                    if body:
                        for tr in extract_tracks(body, source=url):
                            tr.origin_label = "niche-search"
                            tr.niche_score += 3  # found on a niche-targeted page
                            all_tracks.setdefault((tr.title.lower(), tr.artist.lower()), tr)
                if len(all_tracks) >= self.max_search_pages * 8:
                    break

        # 2) current-trending aggregators (general) -> niche-scored later
        for src in CURRENT_TRENDING:
            body = self._fetch_any(src.url, use_stealth=src.use_stealth)
            if body:
                for tr in extract_tracks(body, source=src.name):
                    tr.origin_label = "general-trend"
                    tr.source = src.name
                    all_tracks.setdefault((tr.title.lower(), tr.artist.lower()), tr)

        # 3) evergreen motivation lists (via Wayback)
        if self.use_evergreen:
            for src in EVERGREEN:
                body = self._fetch_any(src.url)
                if body:
                    for tr in extract_tracks(body, source=src.name):
                        tr.origin_label = "evergreen"
                        tr.source = src.name
                        all_tracks.setdefault((tr.title.lower(), tr.artist.lower()), tr)

        tracks = score_tracks(list(all_tracks.values()), niche)
        tracks.sort(key=lambda t: (t.niche_score, t.title.lower()), reverse=True)
        self._save_cache(niche.name, tracks)
        return tracks
