# Trending Audio Scraper — Full Usage Guide

Two things live in this repo:

1. **Discovery engine (CLI)** — finds trending Instagram-Reels audio per niche
   using Scrapling, scores it, and caches the data.
2. **HTTP API (server.py)** — the always-on service. Downloads audio from any
   URL into a per-niche library and serves it back. No auth, fully public.

| | Discovery (CLI) | API (server) |
|---|---|---|
| Runtime | local / dev machine | Render free tier |
| Needs a browser | yes (Scrapling stealth) | no (lightweight) |
| Job | find + score trending audio | fetch + store + serve audio |

Live API base URL: **`https://audi0-scraper.onrender.com`**
Repo: `https://github.com/websitecage-hub/audi0-scraper`

---

## Part 1 — The HTTP API

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/` | Service info + endpoint list |
| GET  | `/health` | Liveness / keep-alive probe |
| POST | `/v1/download` | Download audio from a URL into a niche |
| GET  | `/v1/library/<niche>` | List saved entries for a niche |
| GET  | `/v1/file/<niche>/<filename>` | Fetch a saved audio file |

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
| `fetch "<niche>" [--top N] [--json]` | Discover + score trending audio, cache to `data/` |
| `sync "<niche>" [--top N] [--provider ...]` | Discover AND download the top N into `library/` |
| `download "Title — Artist" [--niche X] [--reel-url URL]` | Download one track |
| `download --url <media-url> --niche X` | Download audio straight from a URL |
| `library "<niche>"` | Show what's already downloaded |

```bash
python cli.py fetch "self improvement" --json
python cli.py sync gym --top 10
python cli.py library self-improvement
python cli.py download "Inner Light — Elderbrook & Bob Moses" --niche self-improvement
python cli.py download --url "https://www.instagram.com/reel/XXXX/" --niche self-improvement
```

Providers: `auto` (default; YouTube -> SoundCloud -> Instagram fallback),
`youtube`, `soundcloud`, `instagram`. `--cookies path/to/cookies.json` supplies
an Instagram session for restricted audio. `--quality` sets the MP3 bitrate
(default 192).

---

## Part 3 — Using it from your automation system

The API is plain HTTP/JSON — drop it into any pipeline. Python example:

```python
import requests

API = "https://audi0-scraper.onrender.com"

def fetch_track(url: str, niche: str = "self-improvement") -> bytes | None:
    """Download audio for a URL and return the raw MP3 bytes."""
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

Call it at the stage of your pipeline that needs a music bed (for the
`yt-automation-v1` stage machine, that is around `voice` / before `render`).
Same shape as your other Render APIs (`meta-api`, `media-gen-mcp`): no auth,
generous timeout.

**Caveats that matter for batch callers:**
- Allow ~300s timeout on the download call — a free-tier cold start adds 1-2 min.
- The server caps at **2 concurrent downloads**. Keep ≤2 in flight or requests
  queue and may time out. Single-video-at-a-time pipelines are unaffected.
- No persistent disk on free tier: fetched audio is served back fine
  immediately, but is wiped on redeploy/restart. **Fetch and use within the same
  run** — don't store a `/v1/file` URL and reuse it hours later.
- Source reliability: direct media URLs and SoundCloud are dependable; YouTube is
  usually IP-blocked from datacenter ranges; Instagram "original audio" needs
  session `cookies`.

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