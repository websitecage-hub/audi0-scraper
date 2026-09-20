# Trending Audio Scraper — Full Usage Guide

Find what audio is **actually trending** in any Instagram-Reels niche, and
download it. Two surfaces:

1. **Trending API** (`server.py`) — the always-on service. `GET /v1/trending/<niche>`
   returns ranked, real trending audio; `POST /v1/download` saves any track's audio.
   No auth, fully public.
2. **Discovery CLI** (`cli.py`) — the same engine locally, with the browser-based
   search pass for deeper coverage.

## How "trending" is decided

A track is not promoted because one blog mentioned it. The engine merges evidence
from independent sources and scores it:

| Signal | Weight | Meaning |
|--------|--------|---------|
| Cross-source corroboration | 40 | How many *independent* sources list it |
| Niche relevance | 25 | Mood/genre match to the niche |
| Momentum | 20 | Share of hits from *current* (this-week) feeds |
| Rank quality | 15 | How high it sits in those lists |

`trend_score` is 0-100; `confidence` (0-1) says how much evidence backed it.
Filter on `confidence` when you need only well-corroborated rows.

**Evidence sources (all real, live data):**

| Source | Kind | Gives |
|--------|------|-------|
| Apple Music most-played chart | current | real rank, genre tags, release date |
| Deezer global + per-genre charts | current | real rank, genre-specific hits |
| Trending-audio blog feeds (Metricool, Vaizle, …) | current | this-week lists |
| Niche-targeted web search | current | niche blog/list pages |
| Evergreen "best of" lists | evergreen | coverage, not momentum |

The API runs the **browser-free** path (charts + blogs + HTTP search) so it fits
Render's free tier. The CLI can additionally use a headless browser for the
bot-walled sources.

Live API base URL: **`https://audi0-scraper.onrender.com`**
Repo: `https://github.com/websitecage-hub/audi0-scraper`

---

## Part 1 — The HTTP API

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/` | Service info + endpoint list |
| GET  | `/health` | Liveness / keep-alive probe |
| GET  | `/v1/niches` | List supported niches |
| GET  | `/v1/trending/<niche>` | Ranked trending audio for a niche |
| POST | `/v1/download` | Download audio from a URL into a niche |
| GET  | `/v1/library/<niche>` | List saved entries for a niche |
| GET  | `/v1/file/<niche>/<filename>` | Fetch a saved audio file |

### `GET /v1/trending/<niche>`

Query params: `limit` (1-100, default 20), `refresh=1` to bypass the day cache.

```bash
curl "https://audi0-scraper.onrender.com/v1/trending/gym?limit=10"
```

```json
{
  "niche": "gym",
  "count": 10,
  "cached": true,
  "results": [
    {
      "title": "Janice STFU",
      "artist": "Drake",
      "trend_score": 76.3,
      "confidence": 1.0,
      "sources": ["itunes-most-played", "deezer-rap", "metricool"],
      "source_count": 3,
      "best_rank": 1,
      "relevance": 1.0,
      "category": "song:rap"
    }
  ]
}
```

Ranking is `trend_score` descending. Use `confidence >= 0.5` to drop
single-source noise. The **first call per niche per day is slow** — it builds the
ranking live (up to ~2 min on the free tier; a 90s budget caps the work, then
best-effort results are cached). Subsequent calls that day return in <0.5s. Pass
`refresh=1` to force a rebuild. Tip: warm niches with a scheduled call (or just
call it at the start of your run, not in the hot path).

### `GET /v1/niches`

```bash
curl https://audi0-scraper.onrender.com/v1/niches
# {"niches": ["anime","beauty","cinematic","crypto","fashion","food","gaming",
#             "gym","lofi","love","money","motivation","nature","party","rap",
#             "real-estate","sad","self-improvement","summer","travel"]}
```

Unknown niches still work — the engine derives queries from the name and falls
back to a broad genre spread.

### `POST /v1/download`

| Field | Required | Notes |
|-------|----------|-------|
| `url` | yes | Any `http(s)` media URL |
| `niche` | no | Library folder. Default `manual`; spaces become dashes |
| `title` | no | Override the detected title |
| `artist` | no | Override the detected artist |
| `cookies` | no | Cookie JSON for private/IG-session-restricted media |

Returns `200` on success, `502` when the source itself failed (DRM, private,
region-blocked), `400` on a bad/missing url, `405` on a wrong method.

```bash
curl -X POST https://audi0-scraper.onrender.com/v1/download \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.instagram.com/reel/XXXX/","niche":"self-improvement"}'
```

Success body:

```json
{
  "ok": true,
  "path": "/data/self-improvement/Some-Track.mp3",
  "title": "Some Track",
  "artist": "Someone",
  "provider": "url",
  "niche": "self-improvement",
  "source_url": "https://...",
  "downloaded": "2026-09-20",
  "message": ""
}
```

Failure body (source-side problem — the API itself is fine):

```json
{"ok": false, "message": "ERROR: [soundcloud] ... This video is DRM protected"}
```

---

### `GET /v1/library/<niche>`

```bash
curl https://audi0-scraper.onrender.com/v1/library/self-improvement
```

```json
{"niche": "self-improvement", "entries": [ { "title": "...", "filename": "...", "...": "..." } ]}
```

Unknown niche returns `{"niche":"...","entries":[]}`.

### `GET /v1/file/<niche>/<filename>`

```bash
curl -OJ https://audi0-scraper.onrender.com/v1/file/self-improvement/Some-Track.mp3
```

Returns the MP3 as an attachment. Path-traversal is guarded — only basenames.
Use the filename from the `/v1/download` response (`path` -> last segment).

### `GET /health`

```json
{"status": "ok", "library": "/data"}
```

Point an UptimeRobot HTTP(s) monitor here (5-minute interval) to keep the free
instance warm.

---

## Part 2 — The discovery CLI (run locally)

The CLI needs a browser (Scrapling stealth), so it runs on your machine, not on
the free Render instance. Install once:

```bash
pip install -r requirements.txt
python -m patchright install chromium   # Scrapling's browser
```

Commands:

| Command | What it does |
|---------|--------------|
| `fetch "<niche>" [--top N] [--json]` | Discover + rank trending audio, cache to `data/` |
| `sync "<niche>" [--top N] [--provider ...]` | Discover AND download the top N into `library/` |
| `download "Title — Artist" [--niche X] [--reel-url URL]` | Download one track |
| `download --url <media-url> --niche X` | Download audio straight from a URL |
| `library "<niche>"` | Show what's already downloaded |

```bash
python cli.py fetch "self improvement" --top 20          # ranked table w/ score+conf+genre
python cli.py fetch gym --json --no-cache                # full JSON records
python cli.py sync gym --top 10                          # download the top 10
python cli.py library self-improvement
python cli.py download "Inner Light — Elderbrook & Bob Moses" --niche self-improvement
python cli.py download --url "https://www.instagram.com/reel/XXXX/" --niche self-improvement
```

Fetch flags: `--no-charts` (skip iTunes/Deezer), `--no-search` (skip web search),
`--no-stealth` (never launch a browser — the fast, API-equivalent path),
`--no-evergreen`, `--no-cache`.

Providers: `auto` (default; YouTube -> SoundCloud -> Instagram fallback),
`youtube`, `soundcloud`, `instagram`. `--cookies path/to/cookies.json` supplies
an Instagram session for restricted audio. `--quality` sets the MP3 bitrate
(default 192).

The table shows per track: `SCORE` (0-100 trend score), `CONF` (0-1 evidence),
`SRC` (independent sources), `RANK` (best chart/list position), `GENRE`.

---

## Part 3 — Using it from your automation system

The API is plain HTTP/JSON — drop it into any pipeline.

**Get a niche's trending audio, then fetch the top pick's file:**

```python
import requests

API = "https://audi0-scraper.onrender.com"

def top_trending(niche: str, limit: int = 10, min_conf: float = 0.5):
    """Ranked trending audio for a niche (filtered to well-corroborated rows)."""
    r = requests.get(f"{API}/v1/trending/{niche}",
                     params={"limit": limit}, timeout=120)
    rows = r.json().get("results", [])
    return [t for t in rows if t.get("confidence", 0) >= min_conf]

def download_from_url(url: str, niche: str) -> bytes | None:
    """Download any media URL's audio and return the raw MP3 bytes."""
    r = requests.post(f"{API}/v1/download",
                      json={"url": url, "niche": niche}, timeout=300)
    entry = r.json()
    if not entry.get("ok"):
        print("skip:", entry.get("message"))   # DRM / private / blocked
        return None
    filename = entry["path"].rsplit("/", 1)[-1]
    f = requests.get(f"{API}/v1/file/{niche}/{filename}", timeout=180)
    return f.content if f.ok else None
```

Typical use in a reels pipeline:

```python
for t in top_trending("self-improvement", limit=5):
    print(t["title"], "-", t["artist"], t["trend_score"], t["category"])
    # then, if you have the track's media URL:
    # audio = download_from_url(media_url, "self-improvement")
```

Call it at the stage that needs a music bed (for the `yt-automation-v1` stage
machine, that is around `voice` / before `render`). Same shape as your other
Render APIs (`meta-api`, `media-gen-mcp`): no auth, generous timeout.

**Caveats that matter for batch callers:**
- `/v1/trending` is cached per niche per day; pass `refresh=1` to force a rebuild.
- Allow ~300s timeout on the download call — a free-tier cold start adds 1-2 min.
- The server caps at **2 concurrent downloads**. Keep ≤2 in flight or requests
  queue and may time out. Single-video-at-a-time pipelines are unaffected.
- No persistent disk on free tier: fetched audio is served back fine
  immediately, but is wiped on redeploy/restart. **Fetch and use within the same
  run** — don't store a `/v1/file` URL and reuse it hours later.
- Source reliability: direct media URLs and SoundCloud are dependable; YouTube is
  usually IP-blocked from datacenter ranges; Instagram "original audio" needs
  session `cookies`.
- Trending is evidence-based, not magic: `confidence` < 0.5 means a single
  source listed it. Raise `min_conf` for stricter, fewer results.

---

## Configuration (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `PORT` | `8000` | Server port |
| `HOST` | `0.0.0.0` | Bind host |
| `TL_LIBRARY` | `./library` (Render: `/data`) | Where audio + manifests live |
| `TL_MAX_CONCURRENT` | `2` | Max simultaneous downloads |
| `TL_MAX_LIBRARY_MB` | `700` | Storage cap; oldest audio pruned first |

## Run locally

```bash
python server.py --port 8000          # dev server
# or production (same as Render):
gunicorn -w 1 --threads 4 --timeout 600 --worker-class gthread -b 0.0.0.0:8000 server:app
```

## Deploy

Already live. To redeploy: push to `main` on
`github.com/websitecage-hub/audi0-scraper` — Render auto-deploys from the
root `Dockerfile`. See `deploy.md` for the dashboard steps and settings.

## HTTP status codes

| Status | Meaning |
|--------|---------|
| 200 | OK |
| 400 | Missing/invalid `url` |
| 404 | Unknown path, or audio file not found |
| 405 | Wrong HTTP method |
| 502 | Source failed to download (DRM / private / region-blocked) |
| 500 | Unexpected server error |

## Tests

```bash
pip install pytest
python -m pytest test_metrics.py -q        # 13 tests: metrics, niches, extraction, charts
```

Covers the scoring math (monotonicity/bounds), niche alias resolution,
genre normalization, and list-rank extraction — the parts where a silent
regression would quietly corrupt rankings.

## Architecture (module map)

| Module | Responsibility |
|--------|----------------|
| `trend_scraper/metrics.py` | Composite trend score + confidence (pure functions) |
| `trend_scraper/charts.py` | Real chart backends (iTunes RSS, Deezer), genre mapping |
| `trend_scraper/niches.py` | Niche registry: queries, mood/context lexicons, aliases |
| `trend_scraper/extract.py` | HTML -> Track extraction, rank capture, relevance |
| `trend_scraper/engine.py` | Orchestrates sources, merges signals, ranks, caches |
| `trend_scraper/searcher.py` | Search discovery (Bing/DDG-HTML browser-free, DDG-Lite stealth) |
| `trend_scraper/sources.py` | Blog/aggregator feed list (current vs evergreen) |
| `trend_scraper/fetcher.py` | HTTP + stealth fetchers (Scrapling) |
| `trend_scraper/audio/` | Download providers + on-disk library with storage cap |
| `server.py` | Flask API (trending, download, library, file, health) |
| `cli.py` | Local discovery/download CLI |

Adding a niche = one entry in `niches.py` + one in `NICHE_GENRES` in `charts.py`.