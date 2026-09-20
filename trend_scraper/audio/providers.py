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
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

import yt_dlp

log = logging.getLogger("trend.audio.download")


@dataclass
class DownloadResult:
    ok: bool
    path: str = ""
    provider: str = ""
    message: str = ""


class AudioGrabError(Exception):
    pass


def _safe_name(*parts: str) -> str:
    s = " - ".join(x.strip() for x in parts if x and x.strip())
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:140] or "untitled"


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
    """Locate the produced audio file (mp3/m4a/opus/webm) for a download."""
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
        if ok and path:
            return DownloadResult(ok=True, path=path, provider=self.name)
        return DownloadResult(ok=False, message=f"yt-dlp: {msg[:120]}",
                              provider=self.name)


class UrlAudioProvider:
    """Download the audio of ANY media URL (Instagram reel, YouTube, SoundCloud, ...).

    Uses yt-dlp's generic extractor; pulls the actual media's metadata (title,
    artist) for the manifest. For Instagram, supply cookies_file (logged-in session)
    or use a public reel URL — anonymous IG fetch is usually blocked.
    """

    name = "url"

    def __init__(self, cookies_file: str | None = None, quality: int = 192,
                 ffmpeg: str | None = None):
        self.cookies = cookies_file
        self.quality = quality
        self.ffmpeg = ffmpeg or "ffmpeg"

    def download(self, url: str, dest_dir: str) -> "UrlDownloadResult":
        opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(dest_dir, "%(title)s.%(ext)s"),
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
            title = (info or {}).get("title", "") or _safe_name("media")
            artist = (info or {}).get("artist") or (info or {}).get("uploader") or ""
            path = _newest_audio(dest_dir)
            prov = "url"
            if not path:
                return UrlDownloadResult(ok=False, title=title, artist=artist,
                                         message="no audio produced", provider=prov)
            return UrlDownloadResult(ok=True, path=path, title=title, artist=artist,
                                     provider=prov,
                                     webpage=(info or {}).get("webpage_url", url))
        except Exception as exc:
            return UrlDownloadResult(ok=False, message=str(exc)[:160], provider="url",
                                     title="", artist="")


def _newest_audio(dest_dir: str) -> str:
    """Return the most recently created audio file in dest_dir."""
    best, best_t = "", -1
    if not os.path.isdir(dest_dir):
        return best
    for f in os.listdir(dest_dir):
        p = os.path.join(dest_dir, f)
        if f.rsplit(".", 1)[-1] in ("mp3", "m4a", "opus", "webm") and os.path.isfile(p):
            t = os.path.getmtime(p)
            if t > best_t:
                best, best_t = p, t
    return best


@dataclass
class UrlDownloadResult:
    ok: bool
    path: str = ""
    provider: str = ""
    title: str = ""
    artist: str = ""
    webpage: str = ""
    message: str = ""


class InstagramReelProvider:
    """Best-effort: download an actual Instagram reel and extract its audio.

    Needs either a public reel URL, or a logged-in session (cookies.json).
    Anonymous IG access is usually 403 unless cookies are provided.
    """

    name = "instagram"

    def __init__(self, cookies_file: str | None = None, ffmpeg: str | None = None):
        self.cookies = cookies_file
        self.ffmpeg = ffmpeg or "ffmpeg"

    def download(self, reel_url: str, track, dest_dir: str) -> DownloadResult:
        safe = _safe_name(track.title, track.artist)
        opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(dest_dir, f"IG-{safe}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "ffmpeg_location": self.ffmpeg,
        }
        if self.cookies:
            opts["cookiefile"] = self.cookies
        mp3 = os.path.join(dest_dir, f"IG-{safe}.mp3")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(reel_url, download=True)
            if os.path.exists(mp3):
                return DownloadResult(ok=True, path=mp3, provider=self.name)
            return DownloadResult(ok=False, message="no audio produced",
                                  provider=self.name)
        except Exception as exc:
            log.debug("IG download failed: %s", exc)
            return DownloadResult(ok=False, message=str(exc)[:120], provider=self.name)
