#!/usr/bin/env python3
"""HTTP API for the trending-audio downloader — drop-in for content automation.

Endpoints
---------
POST /v1/download        {"url": "...", "niche": "...", "title": "...", "artist": "..."}
                          -> downloads the audio, saves to library/<niche>/, returns
                             {ok, path, title, artist, provider, ...}
                          ?cookies=... (JSON) supplies an IG session for private reels
GET  /v1/library/<niche>  -> list of saved entries (manifest)
GET  /v1/file/<niche>/<name>  -> serve the downloaded audio file
GET  /health              -> liveness

Run
---
.venv/bin/python server.py --port 8000 --library /path/library
Then POST from your automation system, e.g.:
  curl -X POST localhost:8000/v1/download \
       -H 'Content-Type: application/json' \
       -d '{"url":"https://www.instagram.com/reel/XXXX/","niche":"self-improvement"}'
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile

from flask import Flask, jsonify, request, send_file, abort

from trend_scraper.audio import AudioLibrary

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LIBRARY = os.getenv("TL_LIBRARY", os.path.join(ROOT, "library"))
_MAX_CONCURRENT = int(os.getenv("TL_MAX_CONCURRENT", "2"))  # yt-dlp+ffmpeg is RAM-heavy

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # request body cap (2MB, urls only)
app.config["LIBRARY"] = DEFAULT_LIBRARY             # set at import; gunicorn loads app directly

# Serialize heavy downloads so a burst of requests can't OOM a free-tier instance.
_download_slots = __import__("threading").BoundedSemaphore(max(1, _MAX_CONCURRENT))


def _write_cookies(cookies: dict | None) -> str | None:
    """Persist optional session cookies to a temp file for yt-dlp."""
    if not cookies:
        return None
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(cookies, fh)
    return path


@app.get("/health")
def health():
    return jsonify({"status": "ok", "library": app.config["LIBRARY"]})


@app.post("/v1/download")
def download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return jsonify({"ok": False, "message": "a valid http(s) url is required"}), 400

    niche = (data.get("niche") or "manual").strip() or "manual"
    cookies_path = _write_cookies(data.get("cookies"))
    try:
        lib = AudioLibrary(app.config["LIBRARY"],
                           provider="url",
                           cookies_file=cookies_path,
                           max_bytes=int(os.getenv("TL_MAX_LIBRARY_MB", "700")) * 1024 * 1024)
        _download_slots.acquire()
        try:
            entry = lib.download_url(url, niche=niche,
                                     title=(data.get("title") or "").strip() or None,
                                     artist=(data.get("artist") or "").strip() or None)
        finally:
            _download_slots.release()
        status = 200 if entry.get("ok") else 502
        return jsonify(entry), status
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)[:300]}), 500
    finally:
        if cookies_path:
            try:
                os.remove(cookies_path)
            except OSError:
                pass


@app.get("/v1/library/<niche>")
def library(niche: str):
    manifest = os.path.join(app.config["LIBRARY"],
                            niche.strip().lower().replace(" ", "-"), "manifest.json")
    if not os.path.exists(manifest):
        return jsonify({"niche": niche, "entries": []})
    return jsonify({"niche": niche,
                    "entries": json.load(open(manifest))})


@app.get("/v1/file/<niche>/<path:filename>")
def serve_file(niche: str, filename: str):
    safe = os.path.basename(filename)  # no path traversal
    base = os.path.join(app.config["LIBRARY"], niche.strip().lower().replace(" ", "-"))
    target = os.path.abspath(os.path.join(base, safe))
    if not target.startswith(os.path.abspath(base)) or not os.path.isfile(target):
        abort(404)
    return send_file(target, as_attachment=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    ap.add_argument("--library", default=DEFAULT_LIBRARY)
    args = ap.parse_args()
    os.makedirs(args.library, exist_ok=True)
    app.config["LIBRARY"] = args.library
    app.run(host=args.host, port=args.port, threaded=True,
            debug=os.getenv("FLASK_DEBUG", "0") == "1")


if __name__ == "__main__":
    main()
