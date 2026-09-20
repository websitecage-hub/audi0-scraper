# Trending Audio API — Usage Guide

Base URL (live): `https://audi0-scraper.onrender.com`

Drop-in HTTP API that downloads audio from a URL (YouTube / SoundCloud / Instagram
reel / any direct media URL), stores it in a per-niche library, and serves it back.

No auth. Fully public.

---

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/` | Service info + endpoint list |
| GET  | `/health` | Liveness check |
| POST | `/v1/download` | Download audio from a URL into a niche |
| GET  | `/v1/library/<niche>` | List saved entries for a niche |
| GET  | `/v1/file/<niche>/<filename>` | Download a saved audio file |

---

## 1. Download audio

`POST /v1/download`

Request body (JSON):

| Field | Required | Notes |
|-------|----------|-------|
| `url` | yes | Any `http(s)` media URL |
| `niche` | no | Folder/library name. Default `manual`. Spaces -> dashes |
| `title` | no | Override the saved title |
| `artist` | no | Override the saved artist |
| `cookies` | no | JSON cookie/session object for private IG reels |

Response: `200` with the entry, or `502` when the source itself failed.

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

Example:

```bash
curl -X POST https://audi0-scraper.onrender.com/v1/download \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.instagram.com/reel/XXXX/","niche":"self-improvement"}'
```

> First request after ~15 min idle cold-starts (free tier) — it can take 1-2 min.

---

## 2. List a niche library

`GET /v1/library/<niche>`

```bash
curl https://audi0-scraper.onrender.com/v1/library/self-improvement
```

```json
{"niche":"self-improvement","entries":[{"title":"Some Track","filename":"Some-Track.mp3", "...":"..."}]}
```

Empty/unseen niche returns `{"niche":"...","entries":[]}`.

---

## 3. Fetch a saved audio file

`GET /v1/file/<niche>/<filename>`

```bash
curl -OJ https://audi0-scraper.onrender.com/v1/file/self-improvement/Some-Track.mp3
```

Sends the MP3 as an attachment. Use the filename from the `/v1/download`
response or the library listing.

---

## Automation snippet

```python
import requests

API = "https://audi0-scraper.onrender.com"

r = requests.post(f"{API}/v1/download", json={
    "url": reel_url,
    "niche": "self-improvement",
}, timeout=300)
entry = r.json()
if entry.get("ok"):
    audio = requests.get(f"{API}/v1/file/{entry['niche']}/{entry['filename']}",
                         timeout=120).content
    # ... feed `audio` to your pipeline
```

---

## Notes & limits (Render free tier)

- Instance sleeps after ~15 min idle; first call cold-starts (1-2 min). Point a
  keep-alive monitor (UptimeRobot) at `/health` to reduce this.
- No persistent disk on free: saved audio in `/data` resets on redeploy/restart.
- Storage auto-prunes (cap `TL_MAX_LIBRARY_MB`, default 700) — oldest audio
  removed first, so the service never crashes on a full disk.
- Max 2 concurrent downloads (`TL_MAX_CONCURRENT`) to protect instance RAM.
- Some sources work better than others: direct media URLs and SoundCloud are
  reliable; YouTube is often IP-blocked from datacenter ranges; Instagram
  "original audio" needs session `cookies`.

## Errors

| Status | Meaning |
|--------|---------|
| 400 | Missing/invalid `url` |
| 405 | Wrong method (e.g. GET on `/v1/download`) |
| 404 | Unknown path, or file not found |
| 502 | Source failed to download (DRM, private, region-blocked) |
| 500 | Unexpected server error |

---