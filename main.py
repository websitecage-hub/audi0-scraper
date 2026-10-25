"""TikTok Trending Audio API -- FastAPI service (hashtag-mining build).

Kept from the first build (these parts were right):

  * Every response is served from memory, so no request approaches Render's 30s
    edge timeout. A cold keyword returns 202 immediately; the scrape runs as a
    BackgroundTask and the client polls.
  * The audio file is never downloaded -- only its URL is returned.
  * In-memory cache only (no database), TTL 600s, stale-while-revalidate so a
    slow refresh never degrades a warm keyword back to a 202.
  * The cache is bounded and concurrent scrapes are capped, because 0.1 vCPU /
    512 MB cannot absorb an unbounded burst.

Changed by the pivot:

  * Sounds now come from HASHTAG MINING plus TikTok's trending feed, instead of
    the generic Explore feed. `?hashtag=` overrides the keyword mapping.
  * `video_count` is the REAL global usage count (from /api/music/details), so
    ranking uses it directly and the synthetic `trend_score` is gone.

Endpoint contract
-----------------
GET /v1/trending?keyword=&limit=&country=&hashtag=&refresh=
  202 {"status":"processing", ...}      scrape started or already running; poll
  200 {"status":"ready","data":[...]}   sounds available (may be stale + refreshing)
  200 {"status":"error", ...}           terminal failure; retried after a cooldown

GET /v1/health               200 {"status":"ok", ...}   (GET + HEAD, +/- slash)
GET /v1/trending/hashtags    200 keyword -> hashtag map
GET /v1/trending/countries   200 regions accepted by ?country=
GET /                       self-contained console page
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

import scraper

VERSION = "2.1.0"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "600"))
ERROR_RETRY_COOLDOWN_SECONDS = int(os.environ.get("ERROR_RETRY_COOLDOWN_SECONDS", "60"))
CACHE_MAX_ENTRIES = int(os.environ.get("CACHE_MAX_ENTRIES", "256"))
MAX_CONCURRENT_SCRAPES = int(os.environ.get("MAX_CONCURRENT_SCRAPES", "2"))
STALE_PROCESSING_SECONDS = int(os.environ.get("STALE_PROCESSING_SECONDS", "120"))
POLL_AFTER_SECONDS = int(os.environ.get("POLL_AFTER_SECONDS", "5"))
MAX_LIMIT = 50

# One scrape fills the cache for any `limit`, so sample the maximum and slice
# per request; a limit=5 request must not poison a later limit=50.
SCRAPE_LIMIT = MAX_LIMIT

# Hashtags are alphanumeric + underscore upstream. Anything else cannot resolve,
# and failing fast beats a scrape that reports "hashtag not found" 20s later.
_HASHTAG_RE = re.compile(r"^#?[A-Za-z0-9_]{1,100}$")

log = logging.getLogger("api")

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

app = FastAPI(
    title="TikTok Trending Audio API",
    version=VERSION,
    description=("Trending TikTok sounds for a keyword/niche, found by hashtag mining, "
                 "with real global usage counts. No API key, no proxy, no browser."),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # spec: allow all origins
    allow_credentials=False,      # "*" and credentials cannot be combined
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

CACHE: dict[str, dict] = {}
_CACHE_LOCK = threading.Lock()
_SCRAPE_SEM = threading.BoundedSemaphore(max(1, MAX_CONCURRENT_SCRAPES))


def _cache_key(keyword: str, country: str | None, hashtag: str | None) -> str:
    return f"{keyword.strip().lower()}|{(country or '-').lower()}|{(hashtag or '-').lower()}"


def _new_entry() -> dict:
    now = time.time()
    return {
        "status": "processing",   # processing | ready | error
        "data": None,
        "meta": None,
        "error": None,
        "refresh_error": None,
        "ready_at": None,         # when data was last refreshed successfully
        "state_at": now,          # when `status` last changed
        "refreshing": False,
        "scrapes": 0,
    }


def _evict_locked() -> None:
    """Bounded cache. Never evicts an entry that is mid-refresh."""
    if len(CACHE) <= CACHE_MAX_ENTRIES:
        return
    victims = sorted(
        (k for k, v in CACHE.items() if not v.get("refreshing")),
        key=lambda k: CACHE[k].get("ready_at") or CACHE[k].get("state_at") or 0,
    )
    for key in victims[: max(1, len(CACHE) - CACHE_MAX_ENTRIES)]:
        CACHE.pop(key, None)


def _scheduled_locked() -> int:
    """Entries with a scrape queued or running.

    This counts SCHEDULED work, not concurrent work: tasks queue on
    _SCRAPE_SEM, so the two differ under a burst. Reporting it as "in flight"
    would overstate how much is actually running.
    """
    return sum(1 for v in CACHE.values() if v.get("refreshing"))


# ---------------------------------------------------------------------------
# Background scrape
# ---------------------------------------------------------------------------

def scrape_and_cache(key: str, keyword: str, country: str | None, hashtag: str | None) -> None:
    """Run one scrape and fold the result into the cache.

    Runs in a worker thread after the response is sent, so it may take longer
    than the edge timeout. Must never raise: an unhandled exception would leave
    the entry stuck in "processing" until STALE_PROCESSING_SECONDS expires.
    """
    with _SCRAPE_SEM:
        try:
            started = time.monotonic()
            result = scraper.fetch_trending_sounds(
                keyword, limit=SCRAPE_LIMIT, country=country, hashtag=hashtag
            )
            with _CACHE_LOCK:
                entry = CACHE.setdefault(key, _new_entry())
                entry.update({
                    "status": "ready",
                    "data": result["sounds"],
                    "meta": result["meta"],
                    "error": None,
                    "refresh_error": None,
                    "ready_at": time.time(),
                    "state_at": time.time(),
                    "refreshing": False,
                    "scrapes": entry.get("scrapes", 0) + 1,
                })
            log.info("scrape ok key=%s sounds=%d enriched=%s in %.2fs",
                     key, len(result["sounds"]), result["meta"].get("sounds_enriched"),
                     time.monotonic() - started)

        except Exception as exc:  # noqa: BLE001 - must not propagate
            message = f"{type(exc).__name__}: {exc}"
            log.warning("scrape failed key=%s: %s", key, message)
            with _CACHE_LOCK:
                entry = CACHE.setdefault(key, _new_entry())
                if entry.get("data"):
                    # Stale beats broken: keep serving and record why the refresh failed.
                    entry.update({
                        "status": "ready",
                        "refreshing": False,
                        "refresh_error": message,
                        "state_at": time.time(),
                        "scrapes": entry.get("scrapes", 0) + 1,
                    })
                else:
                    entry.update({
                        "status": "error",
                        "error": message,
                        "refreshing": False,
                        "state_at": time.time(),
                        "data": None,
                        "meta": None,
                        "scrapes": entry.get("scrapes", 0) + 1,
                    })


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/trending")
def trending(
    background_tasks: BackgroundTasks,
    keyword: str | None = Query(None, max_length=120,
                                description="Keyword or niche, e.g. 'sad'. Mapped onto real TikTok hashtags. Optional when `hashtag` is given."),
    limit: int = Query(20, ge=1, le=MAX_LIMIT, description=f"1-{MAX_LIMIT}"),
    country: str | None = Query(None, min_length=2, max_length=2,
                                description="2-letter region, forwarded as TikTok's `region`. Content follows this server's egress IP."),
    hashtag: str | None = Query(None,
                                description="Explicit hashtag(s), comma-separated: 'sad,sadmusic'. Skips the keyword mapping and works on its own."),
    refresh: bool = Query(False, description="Force a re-scrape even if the cached value is fresh."),
):
    keyword = (keyword or "").strip()
    hashtag = (hashtag or "").strip() or None

    # Either selector is enough. A hashtag-only request is a legitimate way to
    # use this API, so it must not need a dummy keyword passed alongside it.
    if not keyword and not hashtag:
        return JSONResponse(
            {"status": "error",
             "error": "provide `keyword` or `hashtag`",
             "hint": "e.g. ?keyword=sad  or  ?hashtag=sad,sadmusic,emotional"},
            status_code=400,
        )
    if not keyword:
        # Label the job after the first tag: it keys the cache and names the
        # response, and the scraper takes the hashtags as an explicit override.
        keyword = (hashtag or "").split(",")[0].strip().lstrip("#") or "hashtag"

    if country:
        country = country.strip().upper()

    # Validate hashtags up front so a typo is a 400, not a scrape that reports
    # "hashtag not found" twenty seconds later.
    if hashtag:
        bad = [t for t in (p.strip() for p in hashtag.split(",")) if t and not _HASHTAG_RE.match(t)]
        if bad:
            return JSONResponse(
                {"status": "error",
                 "error": f"invalid hashtag(s): {bad}",
                 "hint": "hashtags may contain only letters, digits and underscores, e.g. sadmusic"},
                status_code=400,
            )

    key = _cache_key(keyword, country, hashtag)
    now = time.time()
    schedule = False

    with _CACHE_LOCK:
        entry = CACHE.get(key)

        if entry is None:
            entry = _new_entry()
            CACHE[key] = entry
            _evict_locked()
            entry["refreshing"] = True
            schedule = True
            decision = "cold"

        elif entry["status"] == "ready" and entry.get("data"):
            age = now - (entry.get("ready_at") or 0)
            if refresh or age >= CACHE_TTL_SECONDS:
                if not entry.get("refreshing"):
                    entry["refreshing"] = True
                    schedule = True
                decision = "stale-serve-and-refresh"
            else:
                decision = "fresh"

        elif entry["status"] == "processing":
            # A worker that was recycled mid-scrape leaves this stuck. Recover
            # only after the grace period, and only if nobody is on it.
            if not entry.get("refreshing") and now - entry["state_at"] > STALE_PROCESSING_SECONDS:
                entry["refreshing"] = True
                entry["state_at"] = now
                schedule = True
                decision = "processing-stale-reschedule"
            else:
                decision = "processing"

        elif entry["status"] == "error":
            if refresh or now - entry["state_at"] >= ERROR_RETRY_COOLDOWN_SECONDS:
                entry["status"] = "processing"
                entry["state_at"] = now
                entry["refreshing"] = True
                schedule = True
                decision = "error-retry"
            else:
                decision = "error-cooldown"

        else:                                  # pragma: no cover - defensive
            entry.update(_new_entry())
            entry["refreshing"] = True
            schedule = True
            decision = "reset"

        snapshot = dict(entry)
        scheduled = _scheduled_locked()

    if schedule:
        background_tasks.add_task(scrape_and_cache, key, keyword, country, hashtag)

    # -- shape the response --------------------------------------------------
    if decision in ("cold", "processing", "processing-stale-reschedule", "error-retry"):
        return JSONResponse(
            {
                "status": "processing",
                "keyword": keyword,
                "message": f"Scrape started. Poll again in {POLL_AFTER_SECONDS} seconds.",
                "retry_after_seconds": POLL_AFTER_SECONDS,
                "scrapes_scheduled": scheduled,
                "max_concurrent_scrapes": MAX_CONCURRENT_SCRAPES,
                "cache_key": key,
                "meta": {
                    "source_is_keyword_search": False,
                    "usage_counts_available": scraper.usage_counts_available(),
                },
            },
            status_code=202,
            headers={"Retry-After": str(POLL_AFTER_SECONDS)},
        )

    if decision in ("fresh", "stale-serve-and-refresh"):
        data = snapshot["data"] or []
        capped = data[:limit]
        meta = dict(snapshot.get("meta") or {})
        meta.update({
            "stale": decision == "stale-serve-and-refresh",
            "refreshing": bool(snapshot.get("refreshing")),
            "cache_age_seconds": round(now - (snapshot.get("ready_at") or now), 1),
            "cache_ttl_seconds": CACHE_TTL_SECONDS,
            "sounds_returned": len(capped),
            "requested_limit": limit,
            "with_usage_count": sum(1 for s in capped if s.get("video_count") is not None),
            # Auditable from the query response itself, not just /v1/health.
            "niche_priority": scraper.NICHE_PRIORITY,
            "niche_sounds": sum(1 for s in capped if s.get("is_niche")),
            "trending_only_sounds": sum(1 for s in capped if not s.get("is_niche")),
            "refresh_error": snapshot.get("refresh_error"),
        })
        return {
            "status": "ready",
            "keyword": keyword,
            "count": len(capped),
            "data": capped,
            "meta": meta,
        }

    # error-cooldown
    return {
        "status": "error",
        "keyword": keyword,
        "error": snapshot.get("error") or "scrape failed",
        "retry_after_seconds": max(
            1, int(ERROR_RETRY_COOLDOWN_SECONDS - (now - snapshot["state_at"]))
        ),
        "meta": {"source_is_keyword_search": False,
                 "usage_counts_available": scraper.usage_counts_available()},
    }


def _health_body(probe: bool = False) -> dict:
    with _CACHE_LOCK:
        ready = sum(1 for v in CACHE.values() if v["status"] == "ready")
        entries = len(CACHE)
        scheduled = _scheduled_locked()
    body = {
        "status": "ok",
        "service": "tiktok-trending-api",
        "version": VERSION,
        "cache_entries": entries,
        "cache_ready": ready,
        "scrapes_scheduled": scheduled,
        "max_concurrent_scrapes": MAX_CONCURRENT_SCRAPES,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "usage_counts_available": scraper.usage_counts_available(),
        "niche_priority": scraper.NICHE_PRIORITY,
        "source": "tiktok_hashtag_mining",
        "timestamp": time.time(),
    }
    if probe:
        # Opt-in only. The default health path must stay free of upstream calls:
        # an uptime monitor hits this every few minutes, and a round-the-clock
        # signed heartbeat from one datacenter IP is exactly the traffic that
        # gets an IP retired.
        body["upstream_probe"] = scraper.health_probe()
    return body


@app.get("/v1/health")
@app.head("/v1/health")
@app.get("/v1/health/", include_in_schema=False)
@app.head("/v1/health/", include_in_schema=False)
@app.get("/health", include_in_schema=False)          # UptimeRobot monitor
@app.head("/health", include_in_schema=False)
@app.get("/health/", include_in_schema=False)
@app.head("/health/", include_in_schema=False)
def health(request: Request, probe: bool = Query(False, description="Also make one live upstream call.")):
    """Health check for Render AND the UptimeRobot monitor, which is configured
    against `/health` on the older deployment this replaces.

    Registered for GET *and* HEAD, with and without a trailing slash, under
    both `/v1/health` and `/health`: a HEAD-configured monitor gets a 501 from
    a GET-only route, and an exact-path match 404s on the slash variant --
    both read as "service down" while the service is perfectly healthy.
    """
    body = _health_body(probe)
    if request.method == "HEAD":
        return Response(status_code=200, media_type="application/json")
    return body


@app.get("/v1/trending/hashtags")
def hashtags():
    """The curated keyword -> hashtag map.

    Exposed so a client can see what a keyword resolves to before trusting it,
    and copy a tag list straight into `?hashtag=`.
    """
    catalog = scraper.hashtag_catalog()
    return {
        "keywords": catalog,
        "keyword_count": len(catalog),
        "fallback": ("A keyword absent from this map is derived from the keyword itself: "
                     "the phrase with spaces removed, then each meaningful word."),
        "note": ("Sounds are found by mining these hashtags and merging TikTok's trending "
                 "feed -- not by a text search, which requires a login TikTok will not "
                 "grant a datacenter IP."),
    }


@app.get("/v1/trending/countries")
def countries():
    """Regions accepted by `?country=`."""
    return {
        "countries": scraper.supported_countries(),
        "default": "US",
        "regionalization_supported": False,
        "note": ("?country= is validated and forwarded as TikTok's `region` param, but the "
                 "returned feed follows this server's egress IP, not the value. Deployed on "
                 "Render (Oregon) that yields US-skewed content; from an Asian egress the "
                 "same code returns Asian content."),
    }


_CONSOLE = Path(__file__).with_name("console.html")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def console():
    try:
        return HTMLResponse(_CONSOLE.read_text(encoding="utf-8"))
    except OSError:
        return HTMLResponse(
            "<h1>TikTok Trending Audio API</h1>"
            "<p>console.html is missing. Try <code>/v1/trending?keyword=sad</code> "
            "or <a href='/docs'>/docs</a>.</p>",
            status_code=200,
        )
