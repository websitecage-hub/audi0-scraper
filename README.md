# Trending Audio Scraper + Downloader (niche-aware)

A **Scrapling**-based system that finds **trending Instagram Reels audio for a given
niche** (motivation, self improvement, gym, business…), **saves the discovery data**,
and **downloads the actual audio** into a local, indexed music library.

## Why not scrape Instagram directly?
Instagram's audio library is login-walled and aggressively anti-bot. This system instead
gathers trending audio from **public, scrapable aggregators + content pages**, runs a
**niche-targeted search pass**, **niche-scores** every track, then downloads the real audio.

## Install
```
cd trending-audio-scraper
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m patchright install chromium        # stealth browser
```
Need `ffmpeg` on PATH (`apt-get install ffmpeg`).

## Quick start
```
# 1) discover + save trending audio data for a niche
.venv/bin/python cli.py fetch "self improvement" --json

# 2) download one track
.venv/bin/python cli.py download "Inner Light — Elderbrook & Bob Moses" --niche self-improvement

# 3) download the top N trending tracks for a niche into library/
.venv/bin/python cli.py sync gym --top 10

# 4) list what's already saved
.venv/bin/python cli.py library gym
```

## Commands
- `fetch "niche" [--top N] [--json]` — discover + save trending audio (JSON cache in `data/`)
- `download "Title — Artist" [--niche X] [--reel-url URL] [--provider P]` — one track
- `download --url <media-url> [--title T --artist A] [--niche X]` — audio from any URL
- `sync "niche" [--top N] [--provider P]` — download top N tracks for a niche
- `library "niche"` — show saved tracks + file paths
- `server.py --port 8000` — HTTP API for your content-automation system

## URL-based download (any media URL)
Give the downloader a direct URL (Instagram reel, YouTube, SoundCloud, etc.) and it pulls
the real media, extracts the audio (ffmpeg → MP3), and records title/artist/URL in the
manifest — no track-name matching needed:
```
.venv/bin/python cli.py download --url "https://soundcloud.com/elderbrook/inner-light-feat-bob-moses" --niche self-improvement
```
Instagram private reels need a logged-in session: `--cookies cookies.txt` (yt-dlp format).

## HTTP API for your automation system
`server.py` exposes a small REST API (Flask) your pipeline can POST to:
```
POST /v1/download   {"url":"...", "niche":"...", "title":"...", "artist":"...","cookies":{...}}
                    -> saves audio to library/<niche>/ and returns the manifest entry
GET  /v1/library/<niche>   -> list saved entries for a niche
GET  /v1/file/<niche>/<file> -> download the audio file
GET  /health                -> liveness
```
Example:
```
.venv/bin/python server.py --port 8000 --host 0.0.0.0
curl -X POST http://localhost:8000/v1/download \
     -H 'Content-Type: application/json' \
     -d '{"url":"https://www.instagram.com/reel/XXXX/","niche":"self-improvement"}'
```
The response is the manifest entry — grab `path` (or the `/v1/file/...` URL) and hand the
audio to the next step of your automation. Pass `cookies` (JSON) for private Instagram
content; it is written to a temp file and deleted after the request.

### Provider options (`--provider`)
- `auto` (default) — try YouTube, then fall back to SoundCloud
- `youtube` / `soundcloud` — force a single source
- `instagram` — needs `--reel-url <public reel URL>` and ideally `--cookies cookies.txt`;
  pulls the real reel and extracts its audio with ffmpeg

## How discovery works (the pipeline)
1. **Discover** — DuckDuckGo Lite searches run through Scrapling's **stealth headless
   browser** (patchright) because DDG blocks plain curl with an `anomaly` ban.
2. **Fetch** — discovered pages + current-trending aggregators (metricool, vaizle,
   scottsocial) via fast `curl_cffi` or the stealth browser; bot-walled pages (e.g.
   HubPages 301/403) fall back to the **Wayback Machine** automatically.
3. **Extract** — pulls `Title — Artist` entries, anchored to line starts with a
   capitalization + prose-blacklist filter so article sentences/headlines are skipped.
4. **Score** — a per-niche lexicon (mood + context words) scores each track; niche-search
   hits get a bonus.
5. **Cache** — deduped and cached daily in `data/<niche>-<date>.json`. Re-runs are instant.

## Audio download
- **YouTube provider** — `bestaudio` + ffmpeg → MP3. Uses the `android`/`ios` player
  clients to dodge YouTube's "Sign in to confirm you're not a bot" wall on datacenter IPs.
- **SoundCloud provider** — reliable fallback because YouTube IS block-walled from some
  datacenter ranges (mp3 via `scsearch`).
- **Instagram provider** — real reel URL(s) downloaded and audio-extracted; usually needs
  a logged-in session cookie because anonymous IG fetch is blocked.
- Files + a `manifest.json` (title, artist, path, provider, score, origin, date) live in
  `library/<niche>/`. Downloads are idempotent — re-running skips tracks already saved.

## Project layout
```
trending-audio-scraper/
  cli.py                       # fetch / download / sync / library
  trend_scraper/
    fetcher.py                 # HTTPFetcher (curl) + StealthFetcher (headless)
    searcher.py                # DuckDuckGo Lite discovery
    extract.py                 # Track, Niche presets, extractor + scoring
    sources.py                 # current-trending + evergreen aggregators
    engine.py                  # discovery pipeline (search->fetch->extract->cache)
    audio/
      providers.py             # YouTube / SoundCloud / Instagram downloaders
      library.py               # on-disk audio library + manifest + idempotency
  data/                        # daily trending JSON cache
  library/                     # downloaded .mp3 files + manifest.json per niche
  requirements.txt, README.md
```

## Honest limitations
- **Niche detection is heuristic** (lexicon-based). Great for niche searches, imperfect
  for judging whether a random pop hit "fits" a niche.
- **Instagram "original audio"** (the gritty voiceover/beat-drop sounds) is not nameable
  or indexable from outside Instagram — those are only downloadable via the Instagram
  provider with a real reel URL + logged-in cookies.
- **YouTube can be IP-bot-walled** on datacenter ranges; the SoundCloud auto-fallback
  covers most published tracks, but not everything.
- Adding a niche = one `@classmethod` in `extract.py` (keywords + query phrasings).
