"""Trending-sound discovery for TikTok: hashtag mining + real usage counts.

PIVOT NOTE (2026-10-25)
-----------------------
The first build used only the unsigned Explore feed, on the belief that signed
endpoints were blocked from a datacenter IP. **That belief was wrong.** The
probe that produced it passed `signed=False` for every case, including one
mislabelled "signed", so the signed path was never actually exercised. Tested
properly, the signed endpoints answer fine from a datacenter IP:

    /api/challenge/detail/   SIGNED -> hashtag name -> id   (status 0)
    /api/challenge/item_list/ SIGNED -> 20 videos per hashtag
    /api/music/detail/       SIGNED -> playUrl + duration + REAL videoCount
    /api/recommend/item_list/ SIGNED -> the For You / trending feed

Only keyword SEARCH is genuinely dead: `/api/search/item/full/` answers
`status_code 2483 "Please login"` without cookies, and the cookie boot
(`/api/search/general/preview/` needs none, but page loads do) hits a
SlardarWAF JS challenge. So the design mines hashtags instead of searching.

WHAT THIS DOES NOW
------------------
  1. keyword -> hashtag list  (KEYWORD_HASHTAGS + a generic fallback)
  2. hashtags -> /api/challenge/detail/ -> id -> /api/challenge/item_list/ (20 videos)
  3. the trending feed (/api/recommend/item_list/) is merged in, so results are
     not hashtag-only
  4. every unique sound is enriched via /api/music/detail/ -> play_link,
     duration_seconds and the real global `video_count`
  5. ranked by that real `video_count`

Fallback chain: hashtags (+trending) -> trending alone -> Explore (last resort).

Region: the `country` param is forwarded as TikTok's `region`, but content
follows the SERVER's egress IP. On Render (Oregon) that means US-skewed
content, which is the intent; from an Asia egress the same code returns
Asia-skewed content.
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import _vendor  # noqa: F401  (sys.path bootstrap -- must precede the imports below)

from tiktok import parsers as P
from tiktok import shared
from tiktok.discovery import EXPLORE_CATEGORIES
from tiktok.fetch import (
    TikTokBadRequest,
    TikTokBlocked,
    TikTokNotFound,
    TikTokUpstreamError,
    api,
)

log = logging.getLogger("scraper")

# ---------------------------------------------------------------------------
# Upstream paths (all VERIFIED working signed, see module docstring)
# ---------------------------------------------------------------------------

HASHTAG_DETAIL_PATH = "/api/challenge/detail/"
HASHTAG_VIDEOS_PATH = "/api/challenge/item_list/"
MUSIC_DETAIL_PATH = "/api/music/detail/"
TRENDING_PATH = "/api/recommend/item_list/"
EXPLORE_PATH = "/api/explore/item_list/"          # last-resort fallback only

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Videos requested per hashtag. VERIFIED ceiling: /api/challenge/item_list/
# honours count=30 (30 videos back) and REJECTS count=50 outright
# (TikTokBadRequest), so 30 is the deepest safe single call. Deeper pools come
# from more hashtags, not a bigger count.
MAX_VIDEOS_PER_HASHTAG = 30
VIDEOS_PER_HASHTAG = min(int(os.environ.get("TIKTOK_VIDEOS_PER_HASHTAG", "30")),
                         MAX_VIDEOS_PER_HASHTAG)
TRENDING_COUNT = int(os.environ.get("TIKTOK_TRENDING_COUNT", "20"))

# Sample this many videos at random from the whole fetched pool before reading
# their sounds. Without it every scrape reads the same top videos and returns
# an identical list; with it, a repeat search (or ?fresh=true) yields a
# different mix of the same niche. 0 disables sampling (use the whole pool).
POOL_SAMPLE = int(os.environ.get("TIKTOK_POOL_SAMPLE", "35"))
HASHTAG_FANOUT = int(os.environ.get("TIKTOK_HASHTAG_FANOUT", "3"))
MAX_WORKERS = int(os.environ.get("TIKTOK_MAX_WORKERS", "4"))
ENRICH_WORKERS = int(os.environ.get("TIKTOK_ENRICH_WORKERS", "6"))

# How many of the most promising sounds get a /music/details call. Each call is
# ~0.19s and was verified NOT to be retired after 25 sequential calls, so this
# cap is about bounding one request's latency, not about evading a rate limit.
ENRICH_TOP_N = int(os.environ.get("TIKTOK_ENRICH_TOP_N", "30"))

SCRAPE_DEADLINE_SECONDS = float(os.environ.get("SCRAPE_DEADLINE_SECONDS", "25"))
MAX_RETRIES = int(os.environ.get("TIKTOK_MAX_RETRIES", "2"))
BACKOFF_BASE_SECONDS = float(os.environ.get("TIKTOK_BACKOFF_BASE", "5"))

# When True (the default), a sound mined from the keyword's own hashtags
# outranks a trending-feed-only sound regardless of usage count, so generic
# viral audio cannot crowd a niche out of its own results. Each track carries
# `is_niche` either way, so a client can re-rank. Accepts 0/false/no/off.
NICHE_PRIORITY = os.environ.get("NICHE_PRIORITY", "1").strip().lower() not in (
    "0", "false", "no", "off", "")

# In-process caches. Hashtag ids are stable for hours; a sound's usage count
# changes slowly, so a 10-minute TTL costs nothing and removes most of the
# upstream load when several keywords share popular sounds.
HASHTAG_ID_TTL = float(os.environ.get("TIKTOK_HASHTAG_ID_TTL", "3600"))
MUSIC_CACHE_TTL = float(os.environ.get("TIKTOK_MUSIC_CACHE_TTL", "600"))
_CACHE_MAX = 4000

# Regions accepted by `?country=`. STATIC configuration, not scraped data:
# verified 2026-10-25 that the value is accepted and forwarded as TikTok's
# `region` param, but the returned content follows the SERVER's egress IP.
COUNTRY_CODES = [
    "US", "GB", "CA", "AU", "NZ", "IE", "DE", "FR", "IT", "ES", "NL", "BE",
    "SE", "NO", "DK", "FI", "PL", "PT", "AT", "CH", "GR", "CZ", "RO", "HU",
    "TR", "SA", "AE", "IL", "EG", "ZA", "NG", "KE", "MA", "IN", "PK", "BD",
    "ID", "MY", "SG", "PH", "TH", "VN", "JP", "KR", "TW", "HK", "BR", "MX",
    "AR", "CL", "CO", "PE", "VE", "EC", "UY", "PY", "BO", "CR", "PA", "GT",
]

# ---------------------------------------------------------------------------
# keyword -> hashtags
# ---------------------------------------------------------------------------

# Deliberately small and hand-checked: a wrong hashtag silently returns
# unrelated audio, so a curated map beats guessing. Every tag here was resolved
# against /api/challenge/detail/ during development.
KEYWORD_HASHTAGS: dict[str, list[str]] = {
    "sad": ["sad", "sadmusic", "emotional"],
    "sadness": ["sad", "sadmusic", "emotional"],
    "emotional": ["emotional", "sadmusic", "deepfeelings"],
    "heartbreak": ["heartbreak", "sadmusic", "breakup"],
    "crying": ["sadmusic", "sad", "emotional"],
    "depressed": ["sadmusic", "mentalhealth", "emotional"],
    "lonely": ["sadmusic", "emotional", "lonely"],
    # Mental-health niches: every tag below was verified to resolve AND list 30
    # videos. Without these entries a single-word keyword derives to ONE tag,
    # which caps the pool at ~50 videos instead of ~110.
    "anxiety": ["anxiety", "mentalhealth", "anxietyrelief"],
    "mentalhealth": ["mentalhealth", "anxiety", "overthinking"],
    "overthinking": ["overthinking", "anxiety", "mentalhealth"],
    "relationship": ["relationship", "couplegoals", "dating"],
    "love": ["love", "couplegoals", "lovestory"],
    "breakup": ["breakup", "heartbreak", "sadmusic"],
    "dating": ["dating", "relationship", "couplegoals"],
    "wedding": ["wedding", "weddingtiktok", "bride"],
    "gym": ["gymtok", "workout", "gymmotivation"],
    "workout": ["workout", "gymtok", "fitness"],
    "fitness": ["fitness", "gymtok", "workout"],
    "motivation": ["motivation", "motivationalspeech", "gymmotivation"],
    "running": ["running", "runtok", "workout"],
    "yoga": ["yoga", "yogapractice", "wellness"],
    "dance": ["dance", "dancechallenge", "dancetrend"],
    "music": ["music", "newmusic", "musiclover"],
    "comedy": ["comedy", "funny", "fyp"],
    "funny": ["funny", "comedy", "memes"],
    "memes": ["memes", "funny", "dankmemes"],
    "food": ["food", "foodtok", "recipe"],
    "recipe": ["recipe", "cooking", "foodtok"],
    "cooking": ["cooking", "recipe", "foodie"],
    "coffee": ["coffee", "coffeetok", "barista"],
    "travel": ["travel", "traveltok", "wanderlust"],
    "cars": ["carsoftiktok", "car", "carreview"],
    "animals": ["animals", "petsoftiktok", "cuteanimals"],
    "dogs": ["dogsoftiktok", "dogs", "puppy"],
    "cats": ["catsoftiktok", "cats", "kitten"],
    "gaming": ["gaming", "gamingontiktok", "gamer"],
    "sports": ["sports", "sportstok", "football"],
    "football": ["football", "soccer", "footballtiktok"],
    "basketball": ["basketball", "nba", "hoops"],
    "beauty": ["beauty", "beautytok", "makeup"],
    "makeup": ["makeup", "makeuptutorial", "beautytok"],
    "skincare": ["skincare", "skincareroutine", "beauty"],
    "fashion": ["fashion", "fashiontok", "outfit"],
    "outfit": ["outfit", "outfitinspo", "fashion"],
    "art": ["art", "arttok", "artistsoftiktok"],
    # NOTE: #booktok itself is NOT listable -- /api/challenge/item_list/ answers
    # `upstream status 1` for it every time (verified 3/3, and standalone), even
    # though the tag obviously exists. Route around it rather than fail on it.
    "books": ["booktiktok", "bookrecommendations", "bookclub"],
    "booktok": ["booktiktok", "bookrecommendations", "bookclub"],
    "reading": ["reading", "bookworm", "bookish"],
    "anime": ["anime", "animetiktok", "manga"],
    "study": ["studytok", "studymotivation", "study"],
    "school": ["school", "studytok", "college"],
    "business": ["business", "entrepreneur", "smallbusiness"],
    "money": ["money", "moneytok", "financialfreedom"],
    "crypto": ["crypto", "cryptotiktok", "bitcoin"],
    "tech": ["tech", "techtok", "technology"],
    "coding": ["coding", "programming", "developer"],
    "ai": ["ai", "artificialintelligence", "tech"],
    "nature": ["nature", "naturetok", "outdoors"],
    "asmr": ["asmr", "asmrsounds", "satisfying"],
    "satisfying": ["satisfying", "asmr", "oddlysatisfying"],
    "aesthetic": ["aesthetic", "aestheticvibes", "moodboard"],
    "summer": ["summer", "summervibes", "summer2026"],
    "winter": ["winter", "wintervibes", "christmas"],
    "christmas": ["christmas", "christmasmusic", "holiday"],
    "halloween": ["halloween", "spookyseason", "halloweensounds"],
    "party": ["party", "partymusic", "nightout"],
    "sports-highlights": ["sports", "highlights", "sportstok"],
}

# If a keyword is not in the map, derive candidates from the keyword itself:
# the raw phrase with spaces stripped, then each meaningful token.
_STOPWORDS = {"a", "an", "the", "and", "or", "for", "of", "to", "in", "on",
              "with", "my", "your", "sounds", "sound", "audio", "audios",
              "music", "song", "songs", "videos", "video", "trending", "trend"}


class ScrapeError(RuntimeError):
    """No source produced usable data. Carries a caller-safe message."""


def resolve_hashtags(keyword: str, explicit: str | None = None,
                     fanout: int = HASHTAG_FANOUT) -> tuple[list[str], str]:
    """Map a keyword onto real hashtag names.

    Returns `(tags, how)` where `how` is one of:
      explicit | mapped | derived | empty-keyword
    """
    if explicit:
        tags = [t.strip().lstrip("#").lower() for t in explicit.split(",") if t.strip()]
        tags = [t for t in dict.fromkeys(tags)]          # de-dupe, keep order
        if tags:
            return tags[:max(1, fanout)], "explicit"

    text = (keyword or "").strip().lower()
    if not text:
        return ["fyp"], "empty-keyword"

    if text in KEYWORD_HASHTAGS:
        return KEYWORD_HASHTAGS[text][:max(1, fanout)], "mapped"

    # token overlap: "gym motivation" should still find the gym mapping
    words = [w for w in text.replace("#", " ").split() if w]
    for word in words:
        if word in KEYWORD_HASHTAGS:
            return KEYWORD_HASHTAGS[word][:max(1, fanout)], "mapped"

    # Derive. A hashtag cannot contain spaces, so try the tokens joined first
    # ("sad music" -> "sadmusic", a real tag), then each meaningful token alone.
    # Stopwords are dropped -- and only reintroduced if that would leave nothing
    # at all, or "sounds of the ocean" derives junk like "of" that costs an
    # upstream call to discover does not exist.
    def clean(word):
        return "".join(c for c in word if c.isalnum())

    meaningful = [w for w in (clean(x) for x in words) if w and w not in _STOPWORDS]
    candidates: list[str] = []
    if len(meaningful) >= 2:
        joined = "".join(meaningful)
        if joined:
            candidates.append(joined)
    for w in meaningful:
        if w not in candidates:
            candidates.append(w)
    if not candidates:                      # every word was a stopword
        for w in (clean(x) for x in words):
            if w and w not in candidates:
                candidates.append(w)
    return (candidates or ["fyp"])[:max(1, fanout)], "derived"


def hashtag_catalog() -> dict[str, list[str]]:
    """The curated keyword -> hashtag map, for clients that want to see or
    bypass the mapping."""
    return {k: list(v) for k, v in sorted(KEYWORD_HASHTAGS.items())}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _absolute_url(url):
    """TikTok sometimes hands back a protocol-relative or root-relative audio
    URL. Make it absolute so a client can fetch it directly."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://www.tiktok.com" + url
    if url.startswith(("http://", "https://")):
        return url
    return "https://www.tiktok.com/" + url.lstrip("./")


def _int(value, default=None):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _retryable(exc):
    return isinstance(exc, (TikTokBlocked, TikTokUpstreamError))


def _call(path, params, *, label, deadline, country=None, not_found=(),
          retries=None):
    """One signed upstream call with bounded exponential backoff.

    Signed is the default here because that is what these endpoints need, and
    what the earlier (mis-tested) build wrongly avoided.
    """
    retries = MAX_RETRIES if retries is None else retries
    last = None
    for attempt in range(retries + 1):
        try:
            return api(path, params, signed=True,
                       country=(country.lower() if country else None),
                       not_found=not_found, label=label)
        except TikTokBadRequest:
            raise                                   # params wrong; retry cannot help
        except Exception as exc:                    # noqa: BLE001 - normalised below
            last = exc
            if not _retryable(exc) or attempt == retries:
                break
            delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
            remaining = deadline - time.monotonic()
            if delay >= remaining:
                log.warning("%s: backoff %.1fs exceeds remaining %.1fs, giving up",
                            label, delay, max(0.0, remaining))
                break
            log.warning("%s: %s -- retry %d/%d in %.1fs",
                        label, type(exc).__name__, attempt + 1, retries, delay)
            time.sleep(delay)
    raise ScrapeError(f"{label} failed: {type(last).__name__}: {last}")


# ---------------------------------------------------------------------------
# Hashtag id resolution (cached)
# ---------------------------------------------------------------------------

_hashtag_ids: dict[str, tuple[float, str, dict]] = {}
_hashtag_lock = threading.Lock()


def hashtag_id(tag: str, deadline: float, country=None) -> tuple[str, dict]:
    """hashtag name -> (id, challenge dict). Cached for HASHTAG_ID_TTL."""
    key = tag.lower().lstrip("#")
    with _hashtag_lock:
        hit = _hashtag_ids.get(key)
        if hit and hit[0] > time.time():
            return hit[1], hit[2]

    payload = _call(HASHTAG_DETAIL_PATH, {"challengeName": tag},
                    label=f"hashtag #{tag}", deadline=deadline, country=country,
                    not_found=shared.HASHTAG_NOT_FOUND)
    info = payload.get("challengeInfo") or {}
    challenge = info.get("challenge") or {}
    cid = challenge.get("id")
    if not cid:
        raise TikTokNotFound(f"hashtag #{tag} has no id")

    stats = P.hashtag(info) or {}          # library parser prefers statsV2
    with _hashtag_lock:
        if len(_hashtag_ids) > _CACHE_MAX:
            _hashtag_ids.clear()
        _hashtag_ids[key] = (time.time() + HASHTAG_ID_TTL, str(cid), challenge)
    return str(cid), stats


def hashtag_videos(tag: str, deadline: float, country=None) -> list[dict]:
    """Up to VIDEOS_PER_HASHTAG videos for one hashtag."""
    cid, _stats = hashtag_id(tag, deadline, country)
    payload = _call(HASHTAG_VIDEOS_PATH,
                    {"challengeID": cid, "count": VIDEOS_PER_HASHTAG, "cursor": 0},
                    label=f"videos of #{tag}", deadline=deadline, country=country)
    return P.videos(payload.get("itemList")) or []


def trending_videos(deadline: float, country=None) -> list[dict]:
    """The logged-out For You / trending feed. Signed; unsigned returns an
    18-byte stub."""
    payload = _call(TRENDING_PATH, {"count": TRENDING_COUNT},
                    label="trending feed", deadline=deadline, country=country)
    return P.videos(payload.get("itemList")) or []


def explore_videos(deadline: float, country=None) -> list[dict]:
    """Last-resort fallback: the unsigned Explore feed. Kept because it is the
    one surface that needs no signature at all, so it still works if TikTok
    rotates the signer."""
    payload = api(EXPLORE_PATH, {"categoryType": EXPLORE_CATEGORIES["all"], "count": 30},
                  signed=False, boot=False,
                  country=(country.lower() if country else None),
                  label="explore fallback")
    return P.videos(payload.get("itemList")) or []


# ---------------------------------------------------------------------------
# Per-sound enrichment (cached)
# ---------------------------------------------------------------------------

_music_cache: dict[str, tuple[float, dict]] = {}
_music_lock = threading.Lock()


def music_details(music_id: str, deadline: float, country=None,
                  fresh: bool = False) -> dict | None:
    """Full sound object: play_link, duration_seconds and the REAL video_count.

    This is the endpoint the original build wrongly declared unobtainable.
    Returns None on failure so one bad sound cannot sink the batch.

    `fresh` skips the cache read (the write still happens), so a forced
    ?fresh=true scrape re-reads usage counts live instead of reusing values
    cached by an earlier scrape.

    Deliberately NO retry (retries=0), unlike the hashtag/trending calls. Two
    reasons, both learned from live logs: a sound that has been deleted answers
    a permanent `upstream status 10219`, and retrying it just burns the 5s
    backoff before failing anyway (it cost one scrape 15s); and this is
    best-effort optional data across ~30 sounds, so a transient failure is far
    better handled by the next scrape 10 minutes later than by sleeping here.
    """
    key = str(music_id)
    if not fresh:
        with _music_lock:
            hit = _music_cache.get(key)
            if hit and hit[0] > time.time():
                return hit[1]

    try:
        payload = _call(MUSIC_DETAIL_PATH, {"musicId": key},
                        label=f"music {key}", deadline=deadline, country=country,
                        # 10219 observed for sounds that no longer resolve; kept
                        # out of upstream status noise so the log reads clearly.
                        not_found=tuple(shared.MUSIC_NOT_FOUND) + (10219,),
                        retries=0)
    except (ScrapeError, TikTokNotFound) as exc:
        log.info("music/detail unavailable for %s: %s", key, exc)
        return None

    info = payload.get("musicInfo") or {}
    parsed = P.music(info.get("music"), info.get("stats") or {}, info.get("author") or {})
    if not parsed:
        return None
    with _music_lock:
        if len(_music_cache) > _CACHE_MAX:
            _music_cache.clear()
        _music_cache[key] = (time.time() + MUSIC_CACHE_TTL, parsed)
    return parsed


# ---------------------------------------------------------------------------
# Record building + ranking
# ---------------------------------------------------------------------------

def _record(music: dict, videos_using: list[dict], sources: set[str],
            enriched: bool) -> dict | None:
    """One output sound. `music` is the enriched /music/details object when
    available, otherwise the music object embedded in the video."""
    play_link = _absolute_url(music.get("play_link"))
    if not play_link:
        return None                       # spec: drop sounds with no audio URL

    plays = sum(_int((v.get("stats") or {}).get("play_count"), 0) or 0 for v in videos_using)
    likes = sum(_int((v.get("stats") or {}).get("like_count"), 0) or 0 for v in videos_using)

    video_count = _int(music.get("video_count"))
    if enriched and video_count is not None:
        usage_basis = "upstream_music_details"
    else:
        # null, never 0: 0 is indistinguishable from a real zero and would sort
        # a genuinely huge sound to the bottom.
        usage_basis = "unavailable_enrichment_failed" if enriched else "not_enriched"

    source_list = sorted(sources)
    return {
        "id": str(music.get("id")),
        "title": music.get("title"),
        "artist": music.get("author_name"),
        "play_link": play_link,
        "duration_seconds": _int(music.get("duration_seconds")),

        # REAL global usage count from /api/music/details.
        "video_count": video_count,
        "usage_basis": usage_basis,

        # In-sample signals, kept so a client can re-rank or audit.
        "sample_frequency": len(videos_using),
        "sample_plays": plays,
        "sample_likes": likes,
        "sources": source_list,
        # True when the sound was found via the keyword's own hashtags; False
        # when it arrived only from the shared trending feed (or the Explore
        # fallback). _order_key ranks on this under NICHE_PRIORITY.
        "is_niche": any(s.startswith("hashtag:") for s in source_list),

        "cover": _absolute_url(music.get("cover")),
        "tiktok_url": music.get("link"),
        "is_original_sound": music.get("is_original_sound"),
        "is_commercial_use_allowed": music.get("is_commercial_use_allowed"),
        "is_copyrighted": music.get("is_copyrighted"),
        "streaming_links": music.get("streaming_links") or [],
    }


def _order_key(r: dict):
    """Niche sounds first (under NICHE_PRIORITY), then by REAL global usage
    count; sounds whose enrichment failed sort last within their tier, ordered
    by in-sample recurrence.

    With NICHE_PRIORITY on, a niche sound with 5,000 uses outranks a generic
    trending sound with 200,000 uses: the trending feed is shared by every
    keyword, so ranking on count alone lets it dominate every niche. Turn the
    knob off to rank purely by usage count. `is_niche` is on every record
    either way, so clients can re-rank.
    """
    vc = r["video_count"]
    niche_tier = 0 if (not NICHE_PRIORITY or r.get("is_niche")) else 1
    return (
        niche_tier,                           # niche sounds first when priority is on
        0 if vc is not None else 1,          # known counts before unknown, within tier
        -(vc or 0),                           # then by global usage, desc
        -r["sample_frequency"],               # then in-sample recurrence
        -r["sample_plays"],
        (r["title"] or "").lower(),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_trending_sounds(keyword, limit=20, country=None, hashtag=None, deadline=None,
                          fresh=False):
    """Return trending sounds for a keyword/niche.

    `fresh` skips the per-sound enrichment cache, so usage counts are re-read
    live rather than reused from a previous scrape. The video pool is always
    fetched live and sampled at random, so two scrapes of the same keyword
    return a different mix even without this flag.

    Raises ScrapeError only when NO source produced data. Partial failures are
    reported in `meta.sources_failed`.
    """
    started = time.monotonic()
    deadline = deadline or (time.monotonic() + SCRAPE_DEADLINE_SECONDS)
    limit = max(1, min(int(limit or 20), 50))

    tags, resolution = resolve_hashtags(keyword, explicit=hashtag)

    sources: list[tuple[str, list[dict]]] = []
    failed: list[dict] = []

    # --- 1. hashtag mining (parallel) -------------------------------------
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(tags)))) as pool:
        futures = {pool.submit(hashtag_videos, t, deadline, country): t for t in tags}
        for future in as_completed(futures):
            tag = futures[future]
            try:
                videos = future.result()
                if videos:
                    sources.append((f"hashtag:{tag}", videos))
                else:
                    failed.append({"source": f"hashtag:{tag}", "error": "returned no videos"})
            except Exception as exc:          # noqa: BLE001 - one tag must not sink the job
                failed.append({"source": f"hashtag:{tag}", "error": f"{type(exc).__name__}: {exc}"})

    # --- 2. trending feed, merged ----------------------------------------
    trending_label = f"trending:{(country or 'default').upper()}"
    try:
        trending = trending_videos(deadline, country)
        if trending:
            sources.append((trending_label, trending))
        else:
            failed.append({"source": trending_label, "error": "returned no videos"})
    except Exception as exc:                  # noqa: BLE001
        failed.append({"source": trending_label, "error": f"{type(exc).__name__}: {exc}"})

    # --- 3. fallbacks -----------------------------------------------------
    fallback_used = None
    if not sources:
        try:
            explore = explore_videos(deadline, country)
            if explore:
                sources.append(("explore:all", explore))
                fallback_used = "explore"
        except Exception as exc:              # noqa: BLE001
            failed.append({"source": "explore:all", "error": f"{type(exc).__name__}: {exc}"})

    if not sources:
        detail = "; ".join(f"{f['source']}: {f['error']}" for f in failed) or "no sources"
        raise ScrapeError(f"no sounds found for {keyword!r} ({detail})")

    # --- 4. sample the pool at random ------------------------------------
    # Sampling happens across the whole pool (every hashtag plus the trending
    # feed) so repeated searches surface different sounds instead of the same
    # top videos. Labels ride along with each video, so a sampled video keeps
    # its real source and `is_niche` stays correct.
    pool = [(label, video) for label, videos in sources for video in videos]
    videos_fetched = len(pool)
    if POOL_SAMPLE and len(pool) > POOL_SAMPLE:
        pool = random.sample(pool, POOL_SAMPLE)
    videos_sampled = len(pool)

    # --- 5. dedupe by music id -------------------------------------------
    by_music: dict[str, dict] = {}
    for label, video in pool:
        music = video.get("music")
        if not isinstance(music, dict) or not music.get("id"):
            continue
        key = str(music["id"])                # dedupe by id; by title would merge
        acc = by_music.setdefault(            # distinct sounds sharing a title
            key, {"music": music, "videos": [], "sources": set()})
        acc["videos"].append(video)
        acc["sources"].add(label)

    if not by_music:
        raise ScrapeError(f"no sounds found for {keyword!r} (videos had no music objects)")

    # --- 5. enrich the most promising N with real usage counts -----------
    candidates = list(by_music.items())
    candidates.sort(key=lambda kv: (-len(kv[1]["videos"]),
                                    -(kv[1]["music"].get("duration_seconds") or 0)))
    to_enrich = candidates[:max(0, ENRICH_TOP_N)]

    enriched_map: dict[str, dict] = {}
    if to_enrich and time.monotonic() < deadline - 2:
        with ThreadPoolExecutor(max_workers=min(ENRICH_WORKERS, len(to_enrich))) as pool:
            futures = {pool.submit(music_details, key, deadline, country, fresh=fresh): key
                       for key, _acc in to_enrich}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    detail = future.result()
                except Exception as exc:      # noqa: BLE001
                    log.info("enrichment failed for %s: %s", key, exc)
                    detail = None
                if detail:
                    enriched_map[key] = detail

    # --- 6. build + rank --------------------------------------------------
    records = []
    for key, acc in candidates:
        detail = enriched_map.get(key)
        music = detail if detail else acc["music"]
        record = _record(music, acc["videos"], acc["sources"], enriched=detail is not None)
        if record:
            records.append(record)

    if not records:
        raise ScrapeError(f"every sound for {keyword!r} lacked an audio URL")

    records.sort(key=_order_key)
    selected = records[:limit]
    with_count = sum(1 for r in selected if r["video_count"] is not None)

    if not to_enrich:
        signed_surface = "untested"
    elif enriched_map:
        signed_surface = "ok"
    else:
        signed_surface = "degraded"

    meta = {
        "keyword": keyword,
        "hashtag_resolution": resolution,
        "hashtags_requested": tags,
        "hashtags_sampled": [label.split(":", 1)[1] for label, _v in sources
                             if label.startswith("hashtag:")],
        "sources": [label for label, _v in sources],
        "sources_failed": failed,
        "fallback_used": fallback_used,
        "videos_fetched": videos_fetched,
        "videos_sampled": videos_sampled,
        "pool_sample": POOL_SAMPLE or None,
        "fresh": bool(fresh),
        "sounds_considered": len(records),
        "sounds_enriched": len(enriched_map),
        "sounds_with_usage_count": with_count,
        "sounds_returned": len(selected),
        "requested_limit": limit,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "country": (country or "").upper() or None,
        "source": "tiktok_hashtag_mining",
        "source_endpoints": [HASHTAG_DETAIL_PATH, HASHTAG_VIDEOS_PATH,
                             MUSIC_DETAIL_PATH, TRENDING_PATH],
        "source_is_keyword_search": False,
        "signed_surface": signed_surface,
        "usage_counts_available": usage_counts_available(),
        "notes": [
            "Sounds are found by mining hashtags mapped from the keyword and merged with "
            "TikTok's trending feed. The literal keyword-SEARCH endpoint is unusable: it "
            "answers status_code 2483 'Please login' without cookies and its cookie boot "
            "hits a SlardarWAF challenge.",
            f"video_count is the real global usage count from {MUSIC_DETAIL_PATH} for the "
            f"{len(enriched_map)} sounds enriched; sounds that failed enrichment have "
            "video_count null and sort last. usage_basis says which is which.",
            "Audio URLs are signed TikTok CDN links that expire; do not cache them beyond "
            "this service's own TTL.",
            "Feed content follows this server's egress IP, not the ?country= value. On "
            "Render (Oregon) results are US-skewed; from an Asian egress they are Asian.",
            f"videos_fetched is the whole pool; videos_sampled is the random sample "
            f"({POOL_SAMPLE or 'all'}) actually read for sounds, which is why a repeat "
            "search of the same keyword returns a different mix. Pass ?fresh=true to "
            "force a new scrape instead of waiting out the cache TTL.",
        ],
    }
    return {"sounds": selected, "meta": meta}


def usage_counts_available() -> bool:
    """Whether real global usage counts are obtainable.

    True. The original build reported False, and was wrong: the probe that
    justified it never sent a signature. /api/music/details answers normally
    when signed (verified from a datacenter IP).
    """
    return True


def supported_countries():
    return list(COUNTRY_CODES)


def health_probe():
    """One cheap signed call, used only by /v1/health?probe=1.

    Probes the SIGNED surface because that is what correctness now depends on,
    unlike the old unsigned-Explore probe.
    """
    started = time.monotonic()
    deadline = time.monotonic() + 10
    try:
        cid, stats = hashtag_id("fyp", deadline)
        return {"upstream": "ok", "signed_surface": "ok", "probe": f"hashtag #fyp -> {cid}",
                "elapsed_seconds": round(time.monotonic() - started, 3)}
    except Exception:                          # noqa: BLE001
        try:
            videos = trending_videos(deadline)
            return {"upstream": "ok" if videos else "empty", "signed_surface": "ok" if videos else "degraded",
                    "probe": f"trending -> {len(videos)} videos",
                    "elapsed_seconds": round(time.monotonic() - started, 3)}
        except Exception as exc:               # noqa: BLE001
            return {"upstream": "error", "signed_surface": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_seconds": round(time.monotonic() - started, 3)}



