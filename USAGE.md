# USAGE — TikTok Trending Audio API

**Live service:** https://audi0-scraper.onrender.com
**Interactive console:** https://audi0-scraper.onrender.com/
**OpenAPI / Swagger UI:** https://audi0-scraper.onrender.com/docs

---

## 0. Authentication: there is none

**No API key. No token. No signup. No proxy. Nothing to configure.**

Every endpoint is public. The only thing you need is the base URL. If you are
reading this looking for a key to paste somewhere — there isn't one, and any
guide that tells you otherwise is wrong.

The service reaches TikTok by mining hashtags on the public web endpoints, so
you never supply TikTok credentials either.

---

## 1. Quick start

The API is asynchronous: a search **starts a scrape** and gives you `202`, then
you **poll** until it says `ready`. That is one extra request, and it is what
keeps a 30-second request timeout from ever being an issue.

```bash
BASE=https://audi0-scraper.onrender.com

# 1) start a search (returns 202 + the exact URL to poll)
curl "$BASE/v1/trending?keyword=gym&limit=5"

# 2) poll with fresh=false until status is "ready"
curl "$BASE/v1/trending?keyword=gym&limit=5&fresh=false"
```

The `202` response tells you how to poll, so you never have to guess:

```json
{
  "status": "processing",
  "keyword": "gym",
  "message": "Fresh scrape started (cache bypassed). Poll this URL with &fresh=false in 5 seconds for the result.",
  "fresh": true,
  "poll_with": "https://audi0-scraper.onrender.com/v1/trending?keyword=gym&limit=5&fresh=false",
  "retry_after_seconds": 5,
  "scrapes_scheduled": 1,
  "max_concurrent_scrapes": 2,
  "cache_key": "gym|-|-"
}
```

A `ready` response:

```json
{
  "status": "ready",
  "keyword": "gym",
  "count": 5,
  "data": [
    {
      "id": "7234567890123456789",
      "title": "SLAY!",
      "artist": "Eternxlkz",
      "play_link": "https://sf16-ies-music-sg.tiktokcdn.com/obj/tos-alisg-ve-2774/....",
      "duration_seconds": 60,
      "video_count": 3800000,
      "usage_basis": "upstream_music_details",
      "is_niche": true,
      "sample_frequency": 2,
      "sample_plays": 892600,
      "sample_likes": 41100,
      "sources": ["hashtag:gymtok", "hashtag:workout"],
      "cover": "https://p16-sign.tiktokcdn.com/....",
      "tiktok_url": "https://www.tiktok.com/music/....",
      "is_original_sound": false,
      "is_commercial_use_allowed": true,
      "is_copyrighted": false,
      "streaming_links": []
    }
  ],
  "meta": { "...see section 5..." }
}
```

---

## 2. Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/trending` | **The main endpoint.** Trending sounds for a keyword. |
| `GET` | `/v1/health` | Health check (JSON). |
| `GET` | `/health` | Same, unversioned — for uptime monitors. |
| `GET` | `/v1/trending/hashtags` | The curated keyword → hashtag map. |
| `GET` | `/v1/trending/countries` | Regions accepted by `?country=`. |
| `GET` | `/` | Browser console (search UI + live status). |
| `GET` | `/docs` | Swagger UI. |
| `GET` | `/openapi.json` | OpenAPI schema. |

Health endpoints answer `GET` **and** `HEAD`, with and without a trailing slash,
so any monitor configuration works.

### `GET /v1/trending`

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `keyword` | string | — | The niche, e.g. `gym`, `anxiety`, `sourdough`. Required unless `hashtag` is given. Max 120 chars. |
| `hashtag` | string | — | Explicit tag(s), comma-separated: `sad,sadmusic`. Skips the keyword mapping, works on its own. Letters/digits/underscore only. |
| `limit` | int | `20` | 1–50. Slices the cached result, so any limit is served from one scrape. |
| `fresh` | bool | *server default = true* | See section 3. |
| `refresh` | bool | `false` | Serve the cached value now and refresh behind the request. |
| `country` | string | — | 2-letter code, forwarded as TikTok's `region`. Does **not** change content (see section 7). |

Errors: `400` if you supply neither `keyword` nor `hashtag`, or if a hashtag
contains illegal characters.

### `GET /v1/trending/hashtags`

The hand-curated keyword → hashtag map (~67 keywords), so you can see what a
keyword resolves to before trusting it, and copy a tag list into `?hashtag=`:

```json
{
  "keyword_count": 67,
  "keywords": {
    "gym": ["gymtok", "workout", "gymmotivation"],
    "sad": ["sad", "sadmusic", "emotional"],
    "anxiety": ["anxiety", "mentalhealth", "anxietyrelief"]
  }
}
```

Keywords *not* in this map still work — they are expanded automatically with
TikTok's own search suggestions.

### `GET /v1/trending/countries`

Returns the accepted `?country=` codes (US, GB, IN, …). Note
`"regionalization_supported": false` and the accompanying explanation.

---

## 3. Freshness — read this before you build a client

You asked for **fresh results every time**, so that is the default:

| Request | What happens |
|---|---|
| `?keyword=gym` | **Forces a new scrape.** `202`, then poll. |
| `?keyword=gym&fresh=true` | Same, explicitly. |
| `?keyword=gym&fresh=false` | Reads the cached result. **Use this to poll.** |

Why the distinction matters: a `fresh` request against an already-answered
keyword deliberately starts *another* scrape (that is what "bypass the cache"
means). If you poll with `fresh=true` you would restart the scrape on every
poll and never see a result. Poll with `fresh=false` and you converge.

```
search (fresh)  ->  202  ->  poll (fresh=false)  ->  202  ->  poll  ->  200 ready
```

The cache (`CACHE_TTL_SECONDS`, default 600s) is only consulted by
`fresh=false`. Two searches of the same keyword return **different mixes** —
measured at 5–6 songs in common out of 20.

Server-side default can be changed with `FRESH_BY_DEFAULT=0`, which makes plain
requests cache-first again.

---

## 4. Fields on each track

| Field | Meaning |
|---|---|
| `id` | TikTok music id. Stable — use it as your primary key. |
| `title` | Sound title, as TikTok has it. Often `original sound`. |
| `artist` | Sound author / credited artist. |
| `play_link` | **Direct audio URL.** CDN link, signed, expires. See section 6. |
| `duration_seconds` | Full sound length. Can be long (100s+ for ambient). |
| `video_count` | **Real global usage count** — how many videos use this sound. `null` if enrichment failed (never `0`, which would be a lie). |
| `usage_basis` | Where `video_count` came from: `upstream_music_details`, `unavailable_enrichment_failed`, `not_enriched`. |
| `is_niche` | `true` = found via the keyword's own hashtags; `false` = came only from the shared trending feed. |
| `sample_frequency` | How many of the sampled videos used this sound. |
| `sample_plays` / `sample_likes` | Engagement summed across those videos. |
| `sources` | Which hashtags (and/or the trending feed) produced it. |
| `tiktok_url` | Link to the sound's TikTok page. |
| `is_original_sound`, `is_copyrighted`, `is_commercial_use_allowed` | Flags straight from TikTok. |
| `streaming_links` | Official streaming service links when TikTok provides them. |

### Ordering

```
is_niche desc  ->  video_count desc  ->  sample_frequency desc
```

Niche-mined sounds come first (so generic viral audio cannot crowd out a niche),
then by real usage count. Sounds with no usage count sort last *within their
tier*. Set `NICHE_PRIORITY=0` to rank purely by usage count.

---

## 5. `meta` — what the scrape actually did

| Field | Meaning |
|---|---|
| `hashtag_resolution` | `mapped`, `derived+suggested`, `derived`, `explicit`, `empty-keyword`. |
| `hashtags_requested` / `hashtags_sampled` | What was asked for vs what returned videos. |
| `sources` / `sources_failed` | Per-source outcome. A partial failure is normal; only a total failure errors. |
| `videos_fetched` | Size of the pool pulled (~110). |
| `videos_sampled` | How many of those were read (`TIKTOK_POOL_SAMPLE`, default 35). |
| `sample_strategy` | `stratified_by_source` or `whole_pool`. |
| `sounds_considered` / `sounds_enriched` / `sounds_returned` | Funnel. |
| `niche_priority`, `niche_sounds`, `trending_only_sounds` | Ranking mode and the split. |
| `fresh` | Whether this result came from a forced scrape. |
| `signed_surface` | `ok` / `degraded` / `untested` — whether TikTok's signed endpoints worked. |
| `usage_counts_available` | Whether real usage counts are obtainable. |
| `elapsed_seconds` | Scrape duration. |
| `stale`, `cache_age_seconds`, `refreshing`, `refresh_error` | Cache state (present on reads). |
| `notes` | Human-readable caveats for this result. |

---

## 6. The audio URLs

* `play_link` points at TikTok's CDN. **The service never downloads audio** — it
  only returns the URL.
* Links are **signed and expire** (they carry a token in the path). Do not store
  them long-term; re-query or stream promptly.
* Most are `audio/mp4` (AAC) or raw MP3 inside an MP4 container. Both are
  audio-only and play fine in a browser `<audio>` tag, VLC, or ffmpeg.
* A running user may see `tiktokcdn-us.com` or `tiktokcdn-sg.com` etc. depending
  on the server's egress region.

---

## 7. Region behaviour

`?country=` is validated and forwarded, but **content follows the server's
egress IP**, not the parameter. The deployed service runs on Render in
**Oregon**, so results are US-skewed.

The same applies to hashtag expansion: TikTok's suggestions are regional, so an
unmapped keyword can fan out to different tags in different regions.

---

## 8. Status codes

| Code | Meaning |
|---|---|
| `200` | Result ready (or cached). |
| `202` | Scrape started / still running. Poll with `fresh=false`. |
| `400` | Bad parameters — missing keyword+hashtag, or an illegal hashtag. |
| `200` + `"status":"error"` | The scrape failed. Body carries `error` and `retry_after_seconds`. |
| `422` | Malformed value (e.g. `limit=999`). |

A failed scrape keeps serving the previous good result with
`meta.refresh_error` set, rather than replacing it with an error.

---

## 9. Client examples

### curl

```bash
BASE=https://audi0-scraper.onrender.com

curl -s "$BASE/v1/trending?keyword=anxiety&limit=10"            # 202
curl -s "$BASE/v1/trending?keyword=anxiety&limit=10&fresh=false" | jq '.data[] | {title, artist, video_count, play_link}'
curl -s "$BASE/v1/trending?keyword=x&hashtag=sad,sadmusic"      # explicit tags
curl -s "$BASE/health"                                          # monitor
curl -s "$BASE/v1/trending/hashtags" | jq '.keywords.gym'
```

### Python

```python
import time, urllib.request, json

BASE = "https://audi0-scraper.onrender.com"

def trending(keyword, limit=20, hashtag=None, tries=30):
    url = f"{BASE}/v1/trending?keyword={urllib.parse.quote(keyword)}&limit={limit}"
    if hashtag:
        url += f"&hashtag={urllib.parse.quote(hashtag)}"
    for _ in range(tries):
        with urllib.request.urlopen(url, timeout=60) as r:
            body = json.load(r)
        if body["status"] != "processing":
            return body
        url = body.get("poll_with") or url + "&fresh=false"   # the API tells you
        time.sleep(body.get("retry_after_seconds", 5))
    raise TimeoutError("scrape did not finish")

for s in trending("gym", limit=10)["data"]:
    print(s["video_count"], "uses |", s["title"], "/", s["artist"])
    print("   ", s["play_link"])
```

Using `poll_with` from the response is the recommended pattern — it always
carries `fresh=false`, so it can never loop.

### JavaScript

```js
const BASE = "https://audi0-scraper.onrender.com";

async function trending(keyword, limit = 20) {
  let url = `${BASE}/v1/trending?keyword=${encodeURIComponent(keyword)}&limit=${limit}`;
  for (let i = 0; i < 30; i++) {
    const body = await (await fetch(url)).json();
    if (body.status !== "processing") return body;
    url = body.poll_with || url + "&fresh=false";
    await new Promise(r => setTimeout(r, (body.retry_after_seconds || 5) * 1000));
  }
  throw new Error("scrape timed out");
}

const { data } = await trending("anxiety", 10);
data.forEach(s => console.log(s.video_count, s.title, s.play_link));
```

---

## 10. Browser console

<https://audi0-scraper.onrender.com/>

Search a keyword, optionally give explicit hashtags, tick **fresh scrape**
(on by default), and watch the poll. It shows a live health dot with the API
version, per-track **niche/trending** badges, real usage counts, a "pool
fetched / sampled" KPI, and the raw `meta` JSON. Each row links straight to the
audio URL.

---

## 11. Free-tier behaviour (important for uptime)

The service runs on Render's **free** plan:

* It **spins down after ~15 minutes idle**. The next request wakes it, which
  takes ~30–60s and returns `202` while it boots.
* Keep it warm by pointing **UptimeRobot** (or any monitor) at
  `https://audi0-scraper.onrender.com/health` — that path exists specifically
  for this, answers `200` in ~0.5s, and makes no upstream call.
* Nothing is persisted between restarts: the cache is in memory and empty after
  a cold start. That is by design — there is no database.

---

## 12. Configuration (deployment)

Set as environment variables on the service; defaults are sane.

| Variable | Default | Purpose |
|---|---|---|
| `FRESH_BY_DEFAULT` | `1` | Plain requests always scrape. `0` = cache-first. |
| `NICHE_PRIORITY` | `1` | Niche sounds rank above trending-only ones. |
| `TIKTOK_SUGGEST_TAGS` | `1` | Expand unmapped keywords with TikTok suggestions. |
| `TIKTOK_VIDEOS_PER_HASHTAG` | `30` | Hard cap; TikTok rejects `count=50`. |
| `TIKTOK_POOL_SAMPLE` | `35` | Videos read per scrape from the pool. |
| `TIKTOK_HASHTAG_FANOUT` | `3` | Hashtags mined per keyword. |
| `TIKTOK_ENRICH_TOP_N` | `30` | Sounds given a real usage count. |
| `TIKTOK_MAX_RETRIES` / `TIKTOK_BACKOFF_BASE` | `2` / `5` | Retry/backoff on a block. |
| `SCRAPE_DEADLINE_SECONDS` | `25` | Ceiling on one scrape. |
| `CACHE_TTL_SECONDS` | `600` | Cache lifetime for `fresh=false`. |
| `MAX_CONCURRENT_SCRAPES` | `2` | Keep small on 0.1 vCPU. |
| `CACHE_MAX_ENTRIES` | `256` | Memory bound. |
| `LOG_LEVEL` | `INFO` | |

---

## 13. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Always `202`, never `ready` | You are polling with `fresh=true` (or omitting it, with fresh default on). Poll with `fresh=false`, or just follow `poll_with`. |
| Same songs every time | You are passing `fresh=false` on the first request. Omit it. |
| `video_count` is `null` | That sound's usage lookup failed; it sorts last. Other rows still have counts. |
| Fewer than `limit` results | The pool genuinely held fewer usable sounds — check `meta.sounds_considered`. |
| `400 invalid hashtag(s)` | Tags may only contain letters, digits, underscores. |
| Empty/odd results for a niche | Check `meta.hashtags_sampled` and `meta.sources_failed` — the keyword may have resolved to a tag TikTok no longer serves. `#booktok` is a known dead tag. |
| First request after idle is slow | Free-tier cold start (see section 11). |

---

## 14. Limits and fair use

There is no key and no quota, but the service is a single 0.1-vCPU instance with
the cache disabled by default, so every uncached call costs a real scrape
(~5–15s, several upstream requests). Be considerate:

* Prefer `fresh=false` when you do not need a new mix.
* Do not poll in a tight loop; respect `retry_after_seconds`.
* Repeated simultaneous searches for the same keyword coalesce automatically.

---

## 15. Legal

Scraping TikTok is against TikTok's Terms of Service, and the returned audio
URLs point at TikTok's CDN. This is a technical artifact, not legal advice — you
own the decision to use it and any consequences. The bundled scraper subset is
MIT licensed (see `vendor/UPSTREAM.md`); the application code is MIT.
