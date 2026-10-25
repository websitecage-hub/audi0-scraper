# TikTok Trending Audio API

Given a keyword/niche, return currently-trending TikTok sounds as JSON: title,
artist, duration, **real global usage count**, and a **direct audio URL**.

FastAPI + Uvicorn, in-memory cache only, no database, no API key, no proxy, no
browser automation. Sized for Render's free tier (0.1 vCPU / 512 MB / 30 s
request timeout).

```
GET /v1/trending?keyword=sad&limit=20

200 {
  "status": "ready",
  "count": 20,
  "data": [{
      "id": "6757082343317440514",
      "title": "Je te laisserai des mots",
      "artist": "Patrick Watson",
      "play_link": "https://sf16-ies-music-sg.tiktokcdn.com/obj/tos-alisg-ve-2774/...",
      "duration_seconds": 60,
      "video_count": 524700,                     <-- real global usage count
      "usage_basis": "upstream_music_details",
      "sample_frequency": 1,
      "sample_plays": 9800000,
      "sample_likes": 430000,
      "sources": ["hashtag:sad", "trending:DEFAULT"],
      "cover": "https://p16-common-sign.tiktokcdn.com/...",
      "tiktok_url": "https://www.tiktok.com/music/...",
      "is_original_sound": false,
      "streaming_links": [{"platform": "spotify", "song_id": "07K1i..."}]
  }],
  "meta": {
    "hashtag_resolution": "mapped",
    "hashtags_requested": ["sad", "sadmusic", "emotional"],
    "hashtags_sampled": ["emotional", "sad", "sadmusic"],
    "sources": ["hashtag:emotional", "hashtag:sad", "hashtag:sadmusic", "trending:DEFAULT"],
    "videos_sampled": 80, "sounds_considered": 79,
    "sounds_enriched": 30, "sounds_with_usage_count": 20,
    "signed_surface": "ok", "elapsed_seconds": 5.3
  }
}
```

---

## v2: the pivot to hashtag mining (and an error this project made)

The first build used only the unsigned **Explore** feed and reported
`video_count: null`, on the belief that TikTok's signed endpoints were blocked
from a datacenter IP.

**That belief was wrong, and the test that produced it was broken.** The probe
passed `signed=False` for every case — including one it *labelled* "signed" — so
the signed path was never exercised. Tested properly, the signed endpoints
answer normally from a datacenter IP:

| Endpoint | Signed result |
|---|---|
| `/api/challenge/detail/` | hashtag name → id (`sad` → 5927) |
| `/api/challenge/item_list/` | 20 videos per hashtag |
| `/api/music/detail/` | `playUrl` + `duration` + **real `videoCount`** (1256, 346, …) |
| `/api/recommend/item_list/` | the trending feed (unsigned → an 18-byte stub) |

So this build mines **hashtags** and merges the **trending feed**, and reports a
real usage count. Only keyword *search* remains genuinely dead:
`/api/search/item/full/` answers `status_code 2483 "Please login"` without
cookies, and the cookie boot hits a **SlardarWAF** JS challenge (HTTP 200, 1462
bytes, zero cookies). Full evidence: [`PROBE_RESULTS.md`](PROBE_RESULTS.md).

### What the two builds returned for `sad`

| | v1 (Explore feed) | v2 (hashtag mining) |
|---|---|---|
| Top result | `原聲` / `阿顿先生` — 15s clip, no count | `Je te laisserai des mots` / Patrick Watson — 60s, **524,700 uses** |
| `video_count` | `null` for every track | real, 444 → 524,700 |
| Duration | 9–23 s clips | full sounds, 37 s–475 s |
| Keyword effect | nearly none | distinct per keyword (0 overlap vs the old run) |

### Known limits

* **Renaming the keyword barely changes the result** if the words do not hit the
  curated map. `"sad"` → `[sad, sadmusic, emotional]` (real tags);
  `"inspiring sounds"` and `"sad audios"` both derive the same shape. Use
  `?hashtag=` to be explicit. `/v1/trending/hashtags` shows the map.
* **Content follows the server's egress IP.** `?country=` is forwarded as
  TikTok's `region` param but does not region-shift results. On Render (Oregon)
  this yields US-skewed content; the results above were produced from an Asian
  egress and show Portuguese/Vietnamese/Chinese titles alongside English ones.
* **Some sounds come back without a usage count** when enrichment is skipped or
  fails. Those are `video_count: null` and sort **last** — never `0`, which
  would be indistinguishable from a real zero.

---

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open <http://localhost:8000/> for a self-contained console (no CDN, no build
step), or <http://localhost:8000/docs>.

### Test the polling contract

```bash
# 1. cold request -> 202 Accepted, Retry-After: 5
curl -i "http://localhost:8000/v1/trending?keyword=sad&limit=5"

# 2. poll -> 200 with status "ready", real video_count, and play_link
curl -s "http://localhost:8000/v1/trending?keyword=sad&limit=5" | python -m json.tool
```

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /v1/trending` | Trending sounds. `keyword` (required unless `hashtag`), `limit` (1–50, default 20), `country`, `hashtag`, `fresh`, `refresh` |
| `GET /v1/health` | `{"status":"ok", ...}` — GET **and** HEAD, with and without a trailing slash |
| `GET /health` | Same handler under the plain path — for monitors pointed at `/health` (e.g. UptimeRobot) |
| `GET /v1/trending/hashtags` | The curated keyword → hashtag map |
| `GET /v1/trending/countries` | Regions accepted by `?country=` |
| `GET /` | Console page |
| `GET /docs` | OpenAPI UI |

### The state contract

`/v1/trending` is a poll endpoint. It **never** makes you wait on a scrape:

| State | HTTP | Body |
|---|---|---|
| cold / running | `202` | `{"status":"processing","fresh":true,"poll_with":"…&fresh=false",…}` + `Retry-After` |
| ready | `200` | `{"status":"ready","data":[...]}` |
| still fresh (cache hit) | `200` | as above, `meta.stale: false` |
| expired but populated | `200` | serves the **stale** data immediately, `meta.stale: true`, refreshes in background |
| failed | `200` | `{"status":"error","error":"...","retry_after_seconds":N}` |
| bad hashtag | `400` | e.g. `sad music`, `sad!` — fails fast instead of 20 s later |

**Stale-while-revalidate**: once a keyword has data, an expired entry is served
instantly and refreshed behind the request, so a slow refresh never degrades a
warm keyword back to a 202.

### Fresh results every time

A cache that replays the same list makes the API look broken when you are
browsing a niche, so **freshness is the default**. Three mechanisms:

**1. A plain search always scrapes.** `fresh` is tri-state:

| Request | Behaviour |
|---|---|
| `?keyword=x` (omitted) | **forces a new scrape** — `202`, then poll |
| `?keyword=x&fresh=true` | same, explicitly |
| `?keyword=x&fresh=false` | reads the cached result — use this to **poll** |

The 202 body therefore includes `"poll_with"`: the exact URL to poll, already
carrying `fresh=false`. A `fresh` request against a ready entry deliberately
starts *another* scrape — that is what bypassing the cache means — so polling
with `fresh=true` would never converge. Set `FRESH_BY_DEFAULT=0` to make the
cache authoritative again.

```bash
curl "localhost:8000/v1/trending?keyword=anxiety"                # 202 (+ poll_with)
curl "localhost:8000/v1/trending?keyword=anxiety&fresh=false"    # ready, NEW mix
```

**2. Each scrape reads a random, stratified sample of a deep pool.** Up to 30
videos per hashtag × 3 tags + the trending feed = a pool of ~110 videos
(`meta.videos_fetched`), of which `TIKTOK_POOL_SAMPLE` (35) are read
(`meta.videos_sampled`). Sampling is **stratified per source**, so a flat random
draw cannot miss a whole hashtag and leave one facet of the niche unrepresented.

**3. Every keyword gets that deep pool** — see "How a scrape is assembled"
above; unmapped keywords are expanded with TikTok's own suggestions.

Measured: repeating an unmapped keyword (`thrifting`) returned **1 of 20 sounds
in common** between consecutive scrapes, versus an identical list before.

`?refresh=true` is different and still supported: it serves the cached value
immediately and refreshes behind the request (stale-while-revalidate).

One honest trade-off, since the cache is keyed per keyword: while a scrape is
running (`~5–15 s`) that keyword answers `202` to *everyone*, not just the caller
who asked for it. The cached data is not deleted, so if the scrape fails the old
list is still served with `meta.refresh_error`, and calls arriving mid-scrape
coalesce rather than queueing more work.

### Targeting a niche exactly

The keyword→hashtag mapping is a curated heuristic. Override it:

```bash
curl "localhost:8000/v1/trending?keyword=x&hashtag=sad,sadmusic,emotional"
```

## How a scrape is assembled

```
keyword
   │
   ├─ curated map? ── yes ──> its 3 hashtags
   │      no
   │      v
   ├─ /api/search/general/preview/   (UNSIGNED) "what do people search with this?"
   │      -> tagify + existence-check -> fill up to 3 hashtags
   │      -> "derived+suggested"   (falls back to plain derivation, then "fyp")
   │
   v
3 hashtags x 30 videos  +  trending feed (20)          = pool of ~110 videos
   │
   v
stratified random sample of 35 videos                  = different mix each scrape
   │  (per-source quota, so no hashtag can be squeezed out by chance)
   v
dedupe by music.id  ──>  enrich top 30 via /api/music/detail/
   │                       (play_link, duration, REAL video_count)
   v
rank: is_niche desc  ->  video_count desc  ->  in-sample recurrence
```

Fallback chain: hashtags + trending → trending alone → Explore feed (last
resort, unsigned, so it still works if TikTok rotates the signer).

**Every keyword gets the deep pool, not just hand-mapped ones.** The curated map
covers ~67 keywords; anything else is expanded with TikTok's own query
suggestions, which is what stops an unmapped word from deriving to a single tag
(a ~50-video ceiling). Verified across mapped, unmapped and multi-word keywords:
all seven produced 3 tags and a 110-video pool.

One nuance: suggestions are **regional**, because TikTok ranks them by what is
popular from the requesting IP — from an Asian egress `skateboarding` returned
`skateboardingtaiwan` / `skateboardingphilippines`. On Render (Oregon) they come
back US-oriented, like the rest of the feed.

## Usage counts

`video_count` is the **real global usage count** from `/api/music/details`, for
the sounds that were enriched (`meta.sounds_enriched` of
`meta.sounds_considered`). Ranking is by that count, descending.

* Enrichment is capped by `TIKTOK_ENRICH_TOP_N` (default 30) purely as a latency
  budget: each call is ~0.19 s and **verified not to be retired after 25
  sequential calls**.
* A sound whose enrichment failed has `video_count: null`,
  `usage_basis: "unavailable_enrichment_failed"`, and sorts last.
* A sound that was never a candidate for enrichment has
  `usage_basis: "not_enriched"`.
* `meta.signed_surface` reports `ok` / `degraded` / `untested` — the honest
  signal that the signed path actually worked on this run. If TikTok rotates its
  signer, this flips to `degraded` and the response degrades to the Explore
  fallback rather than failing.
* **No proxy is required.** `TIKTOK_PROXY` exists but is unnecessary for
  correctness; it would only change the egress location the content skews to.

## Deploy to Render

1. Push this directory to a GitHub repo.
2. Render → **New → Blueprint**, point at the repo. `render.yaml` does the rest.

Or manually: build `pip install -r requirements.txt`, start
`uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`, health check
`/v1/health`, env `PYTHON_VERSION=3.11.0`.

**On Render's US IP, results become US-skewed automatically** — that is the one
thing that genuinely changes, and it is why region matters here. The free plan
**spins down after ~15 minutes idle** and persists nothing; the cache is rebuilt
on the next request. `.github/workflows/keepalive.yml` pings `/v1/health` on a
schedule if you want it kept warm.

### Configuration

| Env var | Default | Meaning |
|---|---|---|
| `CACHE_TTL_SECONDS` | `600` | Cache lifetime (the spec's 10 minutes) |
| `CACHE_MAX_ENTRIES` | `256` | Cache bound; oldest evicted first |
| `MAX_CONCURRENT_SCRAPES` | `2` | Scrapes running at once; the rest queue |
| `TIKTOK_HASHTAG_FANOUT` | `3` | Hashtags mined per keyword |
| `TIKTOK_VIDEOS_PER_HASHTAG` | `20` | Videos pulled per hashtag |
| `TIKTOK_TRENDING_COUNT` | `20` | Videos pulled from the trending feed |
| `TIKTOK_ENRICH_TOP_N` | `30` | Sounds enriched with a real usage count |
| `TIKTOK_ENRICH_WORKERS` | `6` | Parallel enrichment calls |
| `TIKTOK_MAX_RETRIES` / `TIKTOK_BACKOFF_BASE` | `2` / `5` | Retry + exponential backoff on a block |
| `SCRAPE_DEADLINE_SECONDS` | `25` | Hard ceiling on one scrape |
| `ERROR_RETRY_COOLDOWN_SECONDS` | `60` | How long an errored keyword is served before retrying |
| `NICHE_PRIORITY` | `1` | Niche-mined sounds outrank trending-feed-only ones, regardless of usage count. `0` = rank purely by count |
| `FRESH_BY_DEFAULT` | `1` | A request without `fresh` forces a new scrape, so every search returns a new mix. `0` = cache is authoritative |
| `TIKTOK_SUGGEST_TAGS` | `1` | Expand an unmapped keyword with TikTok's query suggestions, so it mines 3 tags instead of 1. `0` = plain derivation |
| `TIKTOK_VIDEOS_PER_HASHTAG` | `30` | Videos pulled per hashtag. Hard-capped at 30: TikTok rejects `count=50` (verified) |
| `TIKTOK_POOL_SAMPLE` | `35` | Videos drawn at random from the pool before reading sounds. `0` = read the whole pool |
| `LOG_LEVEL` | `INFO` | |

---

## Tests

```bash
pip install -r requirements-dev.txt

pytest            # 60 offline tests, no network (live tests deselected)
pytest -m live    # 4 live tests: hits real TikTok
python tests/test_live_smoke.py   # the same live tests, as a script
```

The offline suite mocks the scraper to pin the 202→ready contract, cache
behaviour (hit, staleness, eviction, cooldown), error containment, URL
normalisation, ranking, and the keyword→hashtag mapping. The live suite asserts
the things that actually matter now: hashtag sources were used, at least one
real usage count came back, counts are ranked descending with unknowns last, and
a different keyword returns a **different** set of ids.

`tests/test_live_smoke.py::test_live_ids_differ_from_a_different_niche` is the
regression guard for this pivot: if hashtag mining ever silently stops
differentiating, that test fails.

## Architecture

```
main.py          FastAPI app: cache state machine, endpoints, background task
scraper.py       hashtag mining, enrichment, ranking, backoff, error taxonomy
console.html     self-contained console page
vendor/          pinned upstream subset (MIT) + provenance
tests/           offline suite + live smoke test
```

Three properties carry the free-tier fit, all measured:

* **~90% of the work is network I/O** (CPU/wall = 0.085–0.101), so 0.1 vCPU is
  not the bottleneck. A scrape is 5–7 s typical and 10.3 s worst observed (one
  hashtag needed a backoff retry), which is why it runs as a BackgroundTask and
  never inside a request.
* **Enrichment calls do not retry.** A deleted sound answers a permanent
  `upstream status 10219`, and retrying it cost one scrape 15.2 s to fail
  anyway; the hashtag and trending calls still retry with backoff. Removing the
  enrichment retry took that scrape to 6.5 s. `TIKTOK_MAX_RETRIES` governs the
  calls that matter.
* **The audio file is never downloaded.** Only the CDN URL is stored.
* **Memory, measured on the whole process tree:** 58 MB idle and **172 MB under
  a 6-way concurrent cold burst** (cap 512 MB). The v2 path costs more than v1's
  87 MB because a scrape now holds 30 enriched full music objects plus every
  sampled video — worth knowing before raising `MAX_CONCURRENT_SCRAPES`, which
  is the knob that multiplies it.
* **The cache is bounded and scrapes are capped**, so a burst of distinct
  keywords queues instead of exhausting 512 MB.

Reliability notes:

* A hashtag that fails is recorded in `meta.sources_failed`; the request only
  errors if *every* source fails.
* A failed refresh of an already-populated keyword keeps serving the old data
  and records `meta.refresh_error` — stale beats broken.
* The background task cannot raise into the server; an unexpected exception
  becomes an `error` cache state.
* A worker recycled mid-scrape is detected after `STALE_PROCESSING_SECONDS` and
  rescheduled.

## Legal

Scraping TikTok is against TikTok's Terms of Service, and the audio URLs lead to
TikTok's CDN. This is a technical artifact, not legal advice — you own the
decision to run it and the consequences.

## License

Application code: MIT. Vendored upstream code: MIT, © 2026 Chetan Jain — see
`vendor/upstream-LICENSE` and `vendor/UPSTREAM.md`.
