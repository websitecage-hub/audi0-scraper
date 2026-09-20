"""Orchestrator: discover trending Reels audio for a niche and rank it for real.

How "trending" is decided
-------------------------
A track is only promoted when evidence from *independent* sources agrees. The
engine merges mentions across:

  1. niche-targeted search results (DuckDuckGo -> blog/list pages)
  2. current-trending aggregator feeds (this-week lists = momentum)
  3. evergreen "best of" lists (coverage, not momentum)

Every mention updates the track's signals: how many distinct sources listed it,
the best rank it held, whether the source was a *current* feed, and how relevant
it is to the niche. `trend_scraper.metrics` then folds those into one 0-100
trend_score plus a 0-1 confidence. Results are cached per niche per day.
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import quote

from .charts import fetch_all_charts
from .extract import Track, Niche, extract_tracks, relevance_score
from .fetcher import HTTPFetcher, StealthFetcher
from .metrics import compute_score
from .searcher import ddg_lite_search, http_search
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
        use_current: bool = True,
        use_search: bool = True,
        use_stealth: bool = True,
        use_charts: bool = True,
        deadline_s: float | None = 120.0,
    ):
        self.cache_dir = cache_dir
        self.max_search_pages = max_search_pages
        self.max_results = max_results
        self.use_evergreen = use_evergreen
        self.use_current = use_current
        self.use_search = use_search          # DDG discovery (needs a browser)
        self.use_stealth = use_stealth        # allow the headless browser at all
        self.use_charts = use_charts          # real iTunes/Deezer chart data
        # Wall-clock budget: once exceeded, remaining source passes are skipped
        # so a slow free-tier instance can never hang a request indefinitely.
        self.deadline_s = deadline_s
        self._deadline: float | None = None
        self.http = HTTPFetcher()
        # StealthFetcher launches a real browser lazily; on a free-tier API with
        # no chromium we set use_stealth=False and never touch it.
        self._stealth: StealthFetcher | None = StealthFetcher() if use_stealth else None
        os.makedirs(cache_dir, exist_ok=True) if cache_dir else None

    @property
    def stealth(self) -> StealthFetcher:
        if self._stealth is None:
            self._stealth = StealthFetcher()
        return self._stealth

    # -- wall-clock budget --------------------------------------------------
    def _expired(self) -> bool:
        """True once the collect() budget is spent (skip remaining passes)."""
        return self._deadline is not None and time.monotonic() >= self._deadline

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
                return [_track_from_dict(t) for t in raw]
            except Exception:
                return None
        return None

    def _save_cache(self, niche: str, tracks: list[Track]) -> None:
        if not self.cache_dir:
            return
        with open(self._cache_path(niche), "w") as fh:
            json.dump([t.__dict__ for t in tracks], fh, indent=2, ensure_ascii=False)

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
        if use_stealth and self.use_stealth:
            status, body = self.stealth.fetch(url)
            if status and (200 <= status < 300) and body:
                return body
        status, body = self.http.fetch(url)
        if status and (200 <= status < 300) and body:
            return body
        # blocked -> try an archived copy (skipped once the budget is spent, and
        # only for sources that are worth the extra round-trip)
        if self._expired():
            return None
        snap = self._wayback_snapshot(url) if "archive.org" not in url else None
        if snap:
            status, body = self.http.fetch(snap)
            if status and (200 <= status < 300) and body:
                return body
        return None

    # -- merging ------------------------------------------------------------
    @staticmethod
    def _merge(tracks: dict[tuple[str, str], Track], tr: Track, *,
               source: str, is_current: bool, origin: str) -> None:
        """Fold one extracted track into the accumulator, updating all signals."""
        key = (tr.title.lower(), tr.artist.lower())
        existing = tracks.get(key)
        if existing is None:
            tr.sources = [source]
            tr.total_hits = 1
            tr.current_hits = 1 if is_current else 0
            tr.origin_label = origin
            tr.first_seen = tr.last_seen = date.today().isoformat()
            tracks[key] = tr
            return
        if source not in existing.sources:
            existing.sources.append(source)
            existing.total_hits += 1
            if is_current:
                existing.current_hits += 1
        # keep the best (lowest) rank and the list size it came from
        if tr.best_rank and (not existing.best_rank or tr.best_rank < existing.best_rank):
            existing.best_rank = tr.best_rank
            existing.list_size = tr.list_size
        existing.last_seen = date.today().isoformat()
        existing.origin_label = existing.origin_label or origin

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
        self._deadline = (time.monotonic() + self.deadline_s) if self.deadline_s else None

        # 0) REAL chart data (iTunes + Deezer) — the strongest, browser-free
        #    evidence: explicit ranks, genre tags, live updates. Fetched in
        #    parallel because these are the slowest and most valuable sources.
        if self.use_charts and not self._expired():
            try:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    entries = pool.submit(fetch_all_charts, niche.name).result(
                        timeout=max(5.0, (self._deadline or 0) - time.monotonic())
                        if self._deadline else 60)
                for ce in entries:
                    tr = Track(title=ce.title, artist=ce.artist,
                               source=ce.chart, category="song")
                    tr.best_rank = ce.rank
                    tr.list_size = ce.list_size
                    tr.sample_url = ce.url
                    if ce.release_date:
                        tr.first_seen = ce.release_date
                    tr.origin_label = "chart"
                    self._merge(all_tracks, tr, source=ce.chart,
                                is_current=True, origin="chart")
                    if ce.genres:
                        all_tracks[(ce.title.lower(), ce.artist.lower())].category = \
                            "song:" + ",".join(ce.genres[:3])
            except Exception as exc:
                log.warning("chart pass failed: %s", exc)

        # 1) niche-targeted search pass -> niche blog/list pages (relevance
        #    evidence). Deliberately narrow (2 queries x 4 pages): this is the
        #    most expensive pass and the charts already carry the ranking.
        for q in (niche.queries[:2] if self.use_search else []):
            if self._expired():
                log.info("deadline reached; skipping remaining search queries")
                break
            for i in range(1, 2):  # a couple of query phrasings
                query = f"best {q} songs for instagram reels trending" if i == 1 else \
                        f"{q} reels audio viral trending list"
                hits = (ddg_lite_search(query, max_results=self.max_results,
                                        stealth=self.stealth)
                        if self.use_stealth
                        else http_search(query, max_results=self.max_results,
                                         http=self.http))
                for url in hits:
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    body = self._fetch_any(url)
                    if body:
                        list_size = len(extract_tracks(body, source=url))
                        for tr in extract_tracks(body, source=url):
                            tr.list_size = list_size
                            self._merge(all_tracks, tr, source=url,
                                        is_current=False, origin="niche-search")
                if len(all_tracks) >= self.max_search_pages * 8:
                    break

        # 2) current-trending aggregators (momentum evidence)
        if self.use_current and not self._expired():
            for src in CURRENT_TRENDING:
                if self._expired():
                    log.info("deadline reached; stopping current-trend feeds")
                    break
                body = self._fetch_any(src.url, use_stealth=src.use_stealth)
                if body:
                    hits = extract_tracks(body, source=src.name)
                    for tr in hits:
                        tr.list_size = len(hits)
                        self._merge(all_tracks, tr, source=src.name,
                                    is_current=(src.kind == "current"),
                                    origin="general-trend")
                    log.info("source %s -> %d tracks", src.name, len(hits))

        # 3) evergreen lists (coverage; not momentum)
        if self.use_evergreen:
            for src in EVERGREEN:
                body = self._fetch_any(src.url)
                if body:
                    hits = extract_tracks(body, source=src.name)
                    for tr in hits:
                        tr.list_size = len(hits)
                        self._merge(all_tracks, tr, source=src.name,
                                    is_current=False, origin="evergreen")

        tracks = self._rank(list(all_tracks.values()), niche)
        self._save_cache(niche.name, tracks)
        return tracks

    @staticmethod
    def _rank(tracks: list[Track], niche: Niche) -> list[Track]:
        """Attach relevance + composite trend_score and sort best-first."""
        from .charts import genres_for_niche
        wanted_genres = set(genres_for_niche(niche.name))
        for tr in tracks:
            base = relevance_score(tr, niche)
            # genre agreement from real chart tags is hard relevance evidence:
            # a rock-chart hit belongs in the gym pool even if its title has no
            # keyword in common with the niche lexicon.
            genres = set(tr.category.split(":", 1)[1].split(",")) if ":" in tr.category else set()
            if genres & wanted_genres:
                base = min(1.0, base + 0.5)
            tr.relevance = base
            tr.trend_score, tr.confidence = compute_score(
                source_count=len(tr.sources),
                relevance=tr.relevance,
                current_hits=tr.current_hits,
                total_hits=max(1, tr.total_hits),
                best_rank=tr.best_rank,
                total_tracks=max(tr.list_size, tr.best_rank, 1),
                last_seen=tr.last_seen or None,
            )
            tr.niche_score = int(round(tr.relevance * 10))
        tracks.sort(key=lambda t: (t.trend_score, t.confidence, t.title.lower()),
                    reverse=True)
        return tracks


def _track_from_dict(d: dict) -> Track:
    """Rebuild a Track from cached JSON, ignoring unknown keys (forward-compat)."""
    allowed = {f for f in Track.__dataclass_fields__}
    return Track(**{k: v for k, v in d.items() if k in allowed})