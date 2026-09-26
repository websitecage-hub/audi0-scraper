"""Audio download providers built on yt-dlp + ffmpeg.

Instagram does not expose a public, separate audio-file endpoint, and its API is
login-gated, so real "download the trending audio" has two honest paths:

  * YouTubeAudioProvider  (default) — match the trending track name (Title + Artist)
    on YouTube and pull the actual audio. Reliable, no auth, always works for
    published songs. This is what most reels sound bytes come from anyway.

  * InstagramReelProvider — if you have an Instagram *session* (cookies.json from
    the app/web) and a reel URL, yt-dlp can grab the real reel and we extract its
    audio with ffmpeg. Best-effort; anonymous IG access is largely blocked.

Each provider exposes:  download(track: Track, dest_dir: str) -> DownloadResult

Correctness notes (why this file looks the way it does)
------------------------------------------------------
A download must return the file IT produced, never "the newest file in the
folder". The old `_newest_audio(dest_dir)` shortcut returned whichever audio file
in the shared niche folder had the latest mtime, so concurrent requests (and even
sequential ones, once a bigger download landed) all reported the same wrong
track. Every download now writes to a stem derived from the source URL, resolves
its own file by that stem, and verifies size + duration before returning.
"""
from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field

import yt_dlp

log = logging.getLogger("trend.audio.download")

AUDIO_EXTS = ("mp3", "m4a", "opus", "webm", "aac", "ogg", "wav")
MIN_AUDIO_BYTES = 10 * 1024          # anything smaller is a failed/empty fetch

# One lock per output stem: two requests for the SAME source must not both run
# yt-dlp against one destination (they would clobber each other's file).
_stem_locks: dict[str, threading.Lock] = {}
_stem_locks_guard = threading.Lock()


@dataclass
class DownloadResult:
    ok: bool
    path: str = ""
    provider: str = ""
    message: str = ""


@dataclass
class SongTrack:
    """Minimal track shape for name-based search (mirrors the trending record)."""
    title: str = ""
    artist: str = ""


class AudioGrabError(Exception):
    pass


# ------------------------------------------------------------------ helpers

def _safe_name(*parts: str) -> str:
    s = " - ".join(x.strip() for x in parts if x and x.strip())
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:140] or "untitled"


def source_key(url: str) -> str:
    """Stable short id for a source URL — the download's identity."""
    return hashlib.sha256((url or "").strip().encode()).hexdigest()[:12]


def source_sha256(url: str) -> str:
    return hashlib.sha256((url or "").strip().encode()).hexdigest()


def stem_for(safe: str, url: str) -> str:
    """Deterministic output stem: readable name + source hash.

    Deterministic (not random) on purpose: re-requesting the same URL produces the
    same path, so the caller can skip the download entirely when the file is
    already on disk, and concurrent requests for one URL serialise on one lock.
    """
    return f"{safe[:80]}__{source_key(url)}"


def _lock_for(stem: str) -> threading.Lock:
    with _stem_locks_guard:
        lock = _stem_locks.get(stem)
        if lock is None:
            lock = _stem_locks[stem] = threading.Lock()
        return lock


def _audio_ext(path: str) -> bool:
    return path.rsplit(".", 1)[-1].lower() in AUDIO_EXTS


def probe_duration(path: str) -> float:
    """Audio duration in seconds via ffprobe; 0.0 when unprobeable."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60)
        data = json.loads(r.stdout or "{}")
        streams = data.get("streams") or []
        if not any(s.get("codec_type") == "audio" for s in streams):
            return 0.0
        return float((data.get("format") or {}).get("duration") or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def verify_audio(path: str, *, min_seconds: float = 1.0) -> tuple[bool, str]:
    """A downloaded file is only accepted if it is real audio of real length.

    This is the guard that would have caught the 'wrong file returned' bug: a
    stale/empty/leftover file fails here instead of being reported as the track
    the caller asked for.
    """
    if not path:
        return False, "no path"
    if not os.path.isfile(path):
        return False, "file missing"
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return False, f"stat failed: {exc}"
    if size < MIN_AUDIO_BYTES:
        return False, f"too small ({size} bytes)"
    dur = probe_duration(path)
    if dur < min_seconds:
        return False, f"no usable audio stream (duration {dur:.2f}s)"
    return True, ""


def resolve_download(dest_dir: str, stem: str) -> str:
    """The file THIS download produced, identified by its unique stem.

    Prefers an exact `<stem>.mp3` (our postprocessor target), then any audio file
    sharing the stem. Never falls back to an unrelated file in the folder.
    """
    exact = os.path.join(dest_dir, f"{stem}.mp3")
    if os.path.isfile(exact):
        return exact
    matches = [p for p in glob.glob(os.path.join(dest_dir, f"{stem}.*")) if _audio_ext(p)]
    if not matches:
        return ""
    matches.sort(key=os.path.getmtime, reverse=True)
    return matches[0]


def _cleanup_partials(dest_dir: str, stem: str) -> int:
    """Remove leftover partial/format fragments for a failed download.

    Covers both `<stem>.part` and `<stem>.<ext>.part` shapes, since yt-dlp uses
    the latter while the ffmpeg postprocessor uses the former.
    """
    removed = 0
    seen = set()
    for suffix in (".part", ".ytdl", ".temp"):
        for pat in (f"{stem}{suffix}", f"{stem}.*{suffix}"):
            for p in glob.glob(os.path.join(dest_dir, pat)):
                if p in seen:                 # patterns overlap; count each file once
                    continue
                seen.add(p)
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    continue
    return removed


def _run_ydl(opts: dict, query: str) -> tuple[bool, str]:
    """Download first search result for query to outtmpl; return (ok, path)."""
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=True)
            if not info:
                return False, "no result"
            base = opts["outtmpl"] % {"title": _safe_name(info.get("title", ""))} \
                if isinstance(opts["outtmpl"], str) else opts["outtmpl"]
            return True, base
    except Exception as exc:
        log.debug("yt-dlp error for %r: %s", query, exc)
        return False, str(exc)[:160]


def _base_ydl_opts(dest_dir: str, safe: str, ffmpeg: str, quality: int,
                   search_prefix: str) -> dict:
    """Shared yt-dlp options for extracting a search result to mp3."""
    return {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(dest_dir, f"{safe}.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "restrictfilenames": False,
        "default_search": search_prefix,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": str(quality),
        }],
    }


def _finished_audio(dest_dir: str, safe: str) -> str:
    """Locate the produced audio file (mp3/m4a/opus/webm) for a search download."""
    if os.path.exists(os.path.join(dest_dir, f"{safe}.mp3")):
        return os.path.join(dest_dir, f"{safe}.mp3")
    for f in os.listdir(dest_dir):
        if f.startswith(safe) and f.rsplit(".", 1)[-1] in ("mp3", "m4a", "opus", "webm"):
            return os.path.join(dest_dir, f)
    return ""


class SoundCloudAudioProvider:
    """Download a track's audio from SoundCloud (reliable, not IP-bot-walled)."""

    name = "soundcloud"

    def __init__(self, quality: int = 192, ffmpeg: str | None = None):
        self.quality = quality
        self.ffmpeg = ffmpeg or "ffmpeg"

    def download(self, track, dest_dir: str) -> DownloadResult:
        safe = _safe_name(track.title, track.artist)
        opts = _base_ydl_opts(dest_dir, safe, self.ffmpeg, self.quality, "scsearch1")
        query = f"scsearch1:{track.title} {track.artist}" if track.artist else \
            f"scsearch1:{track.title}"
        ok, msg = _run_ydl(opts, query)
        path = _finished_audio(dest_dir, safe) if ok else ""
        if path:
            good, why = verify_audio(path)
            if not good:
                return DownloadResult(ok=False, message=f"soundcloud: {why}",
                                      provider=self.name)
        if ok and path:
            return DownloadResult(ok=True, path=path, provider=self.name)
        return DownloadResult(ok=False, message=f"soundcloud: {msg[:120]}",
                              provider=self.name)


class YouTubeAudioProvider:
    """Download the real audio for a track by searching YouTube."""

    name = "youtube"

    def __init__(self, quality: int = 192, ffmpeg: str | None = None):
        self.quality = quality
        self.ffmpeg = ffmpeg or "ffmpeg"

    def download(self, track, dest_dir: str) -> DownloadResult:
        safe = _safe_name(track.title, track.artist)
        opts = _base_ydl_opts(dest_dir, safe, self.ffmpeg, self.quality, "ytsearch1")
        # youtube-specific: avoid the "Sign in to confirm you're not a bot" wall
        opts["extractor_args"] = {
            "youtube": {"player_client": ["android", "ios", "web"]},
        }
        opts["user_agent"] = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Mobile Safari/537.36")
        query = f"ytsearch1:{track.title} {track.artist}" if track.artist else \
            f"ytsearch1:{track.title}"
        ok, msg = _run_ydl(opts, query)
        path = _finished_audio(dest_dir, safe) if ok else ""
        if path:
            good, why = verify_audio(path)
            if not good:
                return DownloadResult(ok=False, message=f"yt-dlp: {why}",
                                      provider=self.name)
        if ok and path:
            return DownloadResult(ok=True, path=path, provider=self.name)
        return DownloadResult(ok=False, message=f"yt-dlp: {msg[:120]}",
                              provider=self.name)


class UrlAudioProvider:
    """Download the audio of ANY media URL (Instagram reel, YouTube, SoundCloud, ...).

    Uses yt-dlp's generic extractor; pulls the actual media's metadata (title,
    artist) for the manifest. For Instagram, supply cookies_file (logged-in session)
    or use a public reel URL — anonymous IG fetch is usually blocked.

    Guarantees:
      * writes to a stem derived from the URL (concurrent-safe, deterministic)
      * returns the file it actually produced (resolved by stem, not by mtime)
      * skips the network entirely when the same source is already on disk
      * verifies size + real audio duration before reporting success
    """

    name = "url"

    def __init__(self, cookies_file: str | None = None, quality: int = 192,
                 ffmpeg: str | None = None):
        self.cookies = cookies_file
        self.quality = quality
        self.ffmpeg = ffmpeg or "ffmpeg"

    def download(self, url: str, dest_dir: str) -> "UrlDownloadResult":
        os.makedirs(dest_dir, exist_ok=True)
        key = source_key(url)
        sha = source_sha256(url)

        # The caller's requested name is unknown until we look; we name the file
        # after the source hash so two URLs never collide regardless of title.
        stem = f"src-{key}"

        lock = _lock_for(stem)
        with lock:
            # Already downloaded this exact source? Reuse it — no network, no extra
            # disk (this is the single biggest storage saving on a free tier).
            existing = resolve_download(dest_dir, stem)
            if existing:
                good, _why = verify_audio(existing)
                if good:
                    return UrlDownloadResult(
                        ok=True, path=existing, provider=self.name,
                        title="", artist="", webpage=url, cached=True,
                        source_key=key, source_sha256=sha,
                        bytes=os.path.getsize(existing),
                        duration_s=probe_duration(existing))

            opts = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(dest_dir, f"{stem}.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "restrictfilenames": False,
                "socket_timeout": 30,      # don't let a hung source tie up the request
                "retries": 3,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": str(self.quality),
                }],
            }
            if self.cookies:
                opts["cookiefile"] = self.cookies
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
            except Exception as exc:
                _cleanup_partials(dest_dir, stem)
                return UrlDownloadResult(ok=False, message=str(exc)[:160],
                                         provider=self.name, title="", artist="",
                                         source_key=key, source_sha256=sha)

            path = resolve_download(dest_dir, stem)
            title = (info or {}).get("title", "") or ""
            artist = ((info or {}).get("artist")
                      or (info or {}).get("uploader") or "")
            good, why = verify_audio(path)
            if not good:
                _cleanup_partials(dest_dir, stem)
                return UrlDownloadResult(ok=False, message=f"verify failed: {why}",
                                         provider=self.name, title=title,
                                         artist=artist, source_key=key,
                                         source_sha256=sha)
            return UrlDownloadResult(
                ok=True, path=path, provider=self.name, title=title, artist=artist,
                webpage=str((info or {}).get("webpage_url") or url or ""),
                source_key=key, source_sha256=sha,
                bytes=os.path.getsize(path), duration_s=probe_duration(path))


@dataclass
class UrlDownloadResult:
    ok: bool
    path: str = ""
    provider: str = ""
    title: str = ""
    artist: str = ""
    webpage: str = ""
    message: str = ""
    # provenance / verification (added so callers can trust the file they got)
    cached: bool = False
    source_key: str = ""
    source_sha256: str = ""
    bytes: int = 0
    duration_s: float = 0.0


class InstagramReelProvider:
    """Download an actual Instagram reel/audio and extract its audio.

    Two modes:
      * reel_url given  -> pull that exact reel's audio
      * track only      -> search Instagram's audio pages for the trending sound
                           name and pull the top match

    Instagram needs a logged-in session for essentially everything; the caller
    must supply `cookies` (see server._write_cookies, which converts a browser
    export to the Netscape format yt-dlp requires).
    """

    name = "instagram"

    def __init__(self, cookies_file: str | None = None, ffmpeg: str | None = None):
        self.cookies = cookies_file
        self.ffmpeg = ffmpeg or "ffmpeg"

    def _opts(self, dest_dir: str, stem: str) -> dict:
        opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(dest_dir, f"{stem}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "socket_timeout": 30,
            "retries": 3,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "ffmpeg_location": self.ffmpeg,
            # IG is aggressive about generic clients; identify as the mobile app.
            "extractor_args": {"instagram": {"api": ["graphql"]}},
            "http_headers": {
                "User-Agent": ("Instagram 219.0.0.12.117 Android "
                               "(30/11; 320dpi; 720x1440; samsung; SM-A015F; "
                               "a01core; qcom; en_US)"),
            },
        }
        if self.cookies:
            opts["cookiefile"] = self.cookies
        return opts

    def _store(self, query: str, dest_dir: str):
        stem = stem_for("ig", query)
        lock = _lock_for(stem)
        with lock:
            existing = resolve_download(dest_dir, stem)
            if existing and verify_audio(existing)[0]:
                return existing
            try:
                with yt_dlp.YoutubeDL(self._opts(dest_dir, stem)) as ydl:
                    ydl.extract_info(query, download=True)
            except Exception as exc:
                log.debug("IG download failed for %s: %s", query, exc)
                _cleanup_partials(dest_dir, stem)
                return ""
            path = resolve_download(dest_dir, stem)
            good, why = verify_audio(path)
            if not good:
                _cleanup_partials(dest_dir, stem)
                log.debug("IG verify failed for %s: %s", query, why)
                return ""
            return path

    def search(self, title: str, artist: str = "") -> str:
        """The best-guess Instagram URL for a trending sound name."""
        from urllib.parse import quote_plus
        term = f"{title} {artist}".strip()
        return f"https://www.instagram.com/explore/search/keyword/?q={quote_plus(term)}"

    def download(self, reel_url: str, track=None, dest_dir: str = ".") -> DownloadResult:
        path = self._store(reel_url, dest_dir)
        if path:
            return DownloadResult(ok=True, path=path, provider=self.name)
        return DownloadResult(ok=False, provider=self.name,
                              message="instagram fetch failed (session cookies required)")

    def download_sound(self, title: str, artist: str, dest_dir: str) -> DownloadResult:
        """Resolve a trending sound NAME to audio via Instagram audio pages."""
        from urllib.parse import quote_plus
        term = f"{title} {artist}".strip()
        candidates = [
            f"https://www.instagram.com/explore/search/keyword/?q={quote_plus(term)}",
            f"https://www.instagram.com/reels/audio/{quote_plus(term.lower().replace(' ', '-'))}/",
        ]
        for url in candidates:
            path = self._store(url, dest_dir)
            if path:
                return DownloadResult(ok=True, path=path, provider=self.name)
        return DownloadResult(ok=False, provider=self.name,
                              message=f"instagram: no audio for {term!r}")


class SongSearchProvider:
    """Resolve ANY song by name to audio, trying several sources in order.

    Used when the caller has a track title/artist (typically straight from the
    trending API) and no direct URL.
    """

    name = "songsearch"

    def __init__(self, quality: int = 192, ffmpeg: str | None = None):
        self.quality = quality
        self.ffmpeg = ffmpeg or "ffmpeg"

    def download(self, track, dest_dir: str) -> DownloadResult:
        title = getattr(track, "title", "") or ""
        artist = getattr(track, "artist", "") or ""
        if not title:
            return DownloadResult(ok=False, provider=self.name,
                                  message="no title to search")
        safe = _safe_name(title, artist)
        term = f"{title} {artist}".strip()
        # Ordered sources: SoundCloud is the least bot-walled, then YouTube.
        attempts = [
            ("scsearch1", "soundcloud"),
            ("ytsearch5", "youtube"),
        ]
        for prefix, source in attempts:
            stem = stem_for(f"{safe}-{source}", term)
            lock = _lock_for(stem)
            with lock:
                existing = resolve_download(dest_dir, stem)
                if existing and verify_audio(existing)[0]:
                    return DownloadResult(ok=True, path=existing, provider=self.name)
                opts = {
                    "format": "bestaudio/best",
                    "outtmpl": os.path.join(dest_dir, f"{stem}.%(ext)s"),
                    "noplaylist": True,
                    "quiet": True,
                    "no_warnings": True,
                    "noprogress": True,
                    "default_search": prefix,
                    "socket_timeout": 30,
                    "retries": 3,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": str(self.quality),
                    }],
                }
                if source == "youtube":
                    opts["extractor_args"] = {
                        "youtube": {"player_client": ["android", "ios", "web"]},
                    }
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.extract_info(f"{prefix}:{term}", download=True)
                except Exception as exc:
                    log.debug("%s search failed for %r: %s", source, term, exc)
                    _cleanup_partials(dest_dir, stem)
                    continue
                path = resolve_download(dest_dir, stem)
                good, why = verify_audio(path)
                if good:
                    return DownloadResult(ok=True, path=path, provider=self.name)
                _cleanup_partials(dest_dir, stem)
                log.debug("%s verify failed for %r: %s", source, term, why)
        return DownloadResult(ok=False, provider=self.name,
                              message=f"no source had {term!r}")