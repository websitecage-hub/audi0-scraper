"""Audio library: an on-disk, indexed music library organized by niche and date.

Each download writes:
    library/<niche>/<audio-file>            the mp3/m4a
    library/<niche>/manifest.json           index of every saved track + file path

Idempotent: re-running with --if-missing skips already-saved tracks.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import date

from .providers import (YouTubeAudioProvider, SoundCloudAudioProvider,
                        InstagramReelProvider, UrlAudioProvider)

log = logging.getLogger("trend.audio.library")

# A single lock per-process keeps manifest reads/writes atomic under concurrency,
# protects download_url (used by the threaded API server) from races.
_MANIFEST_LOCK = threading.Lock()


def _safe_folder(name: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip().lower().replace(" ", "-")
    return s[:60] or "niche"


class AudioLibrary:
    def __init__(self, root: str, *, provider: str = "auto",
                 cookies_file: str | None = None, quality: int = 192,
                 max_bytes: int | None = None):
        self.root = root
        self.provider_name = provider
        self.cookies = cookies_file
        self.quality = quality
        self.max_bytes = max_bytes or int(os.getenv("TL_MAX_LIBRARY_MB", "700")) * 1024 * 1024
        os.makedirs(root, exist_ok=True)
        # build an ordered list of providers to try (auto = youtube then soundcloud)
        self.providers: list = []
        if provider in ("auto", "youtube"):
            self.providers.append(YouTubeAudioProvider(quality=quality))
        if provider in ("auto", "soundcloud"):
            self.providers.append(SoundCloudAudioProvider(quality=quality))
        if provider == "instagram":
            self.providers.append(InstagramReelProvider(cookies_file=cookies_file))

    # ---- manifest helpers --------------------------------------------------
    def _manifest_path(self, niche: str) -> str:
        return os.path.join(self.root, _safe_folder(niche), "manifest.json")

    def _load_manifest(self, niche: str) -> list[dict]:
        p = self._manifest_path(niche)
        if os.path.exists(p):
            try:
                return json.load(open(p))
            except Exception:
                return []
        return []

    def _save_manifest(self, niche: str, entries: list[dict]) -> None:
        p = self._manifest_path(niche)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            json.dump(entries, fh, indent=2, ensure_ascii=False)

    def _already_have(self, entries: list[dict], title: str, artist: str) -> bool:
        key = (title.strip().lower(), artist.strip().lower())
        for e in entries:
            if (e.get("title", "").strip().lower(), e.get("artist", "").strip().lower()) == key:
                return os.path.exists(e.get("path", ""))
        return False

    # ---- the main action -----------------------------------------------------
    def save(self, niche: str, track, *, if_missing: bool = True,
             reel_url: str | None = None) -> dict:
        """Download one track into the niche library; return a manifest entry."""
        folder = os.path.join(self.root, _safe_folder(niche))
        os.makedirs(folder, exist_ok=True)
        entries = self._load_manifest(niche)

        if if_missing and self._already_have(entries, track.title, track.artist):
            return {"skipped": True, "title": track.title, "artist": track.artist}

        # try each provider in order until one succeeds
        res = None
        for prov in self.providers:
            if isinstance(prov, InstagramReelProvider):
                if reel_url:
                    res = prov.download(reel_url, track, folder)
            elif hasattr(prov, "download"):
                res = prov.download(track, folder)
            if res and res.ok:
                break

        entry = {
            "title": track.title,
            "artist": track.artist,
            "niche": niche,
            "path": res.path if res else "",
            "provider": (res.provider if res else "") or self.provider_name,
            "ok": bool(res and res.ok),
            "message": (res.message if res else "no provider"),
            "niche_score": getattr(track, "niche_score", 0),
            "origin": getattr(track, "origin_label", ""),
            "downloaded": date.today().isoformat(),
        }
        if res and res.ok:
            with _MANIFEST_LOCK:
                entries = self._load_manifest(niche)
                entries.append(entry)
                self._save_manifest(niche, entries)
            self._prune()
        return entry

    def sync(self, niche: str, tracks, *, limit: int | None = None,
             if_missing: bool = True) -> dict:
        """Download several tracks; returns summary counts."""
        results = []
        for tr in (tracks[:limit] if limit else tracks):
            results.append(self.save(niche, tr, if_missing=if_missing))
        ok = sum(1 for r in results if r.get("ok"))
        skipped = sum(1 for r in results if r.get("skipped"))
        return {"total": len(results), "downloaded_ok": ok, "skipped": skipped,
                "failed": len(results) - ok - skipped, "items": results}

    # ---- URL-based download (for content-automation systems) ----
    def download_url(self, url: str, niche: str = "manual",
                     title: str | None = None, artist: str | None = None) -> dict:
        """Download the audio of any media URL; record it in the niche manifest.

        Returns a manifest entry dict. If title/artist are not supplied they're
        read from the media's metadata.
        """
        folder = os.path.join(self.root, _safe_folder(niche))
        os.makedirs(folder, exist_ok=True)
        prov = UrlAudioProvider(cookies_file=self.cookies, quality=self.quality)
        res = prov.download(url, folder)
        entry = {
            "title": title or res.title,
            "artist": artist or res.artist,
            "niche": niche,
            "path": res.path,
            "provider": res.provider,
            "ok": res.ok,
            "message": res.message,
            "webpage": res.webpage,
            "source_url": url,
            "downloaded": date.today().isoformat(),
        }
        if res.ok:
            with _MANIFEST_LOCK:
                entries = self._load_manifest(niche)
                entries.append(entry)
                self._save_manifest(niche, entries)
            self._prune()
        return entry

    # -- storage cap (keep the free-tier disk from filling up) ------------------
    def _prune(self) -> None:
        """If the library exceeds max_bytes, delete the oldest audio files until under.

        Free tiers (Render free) have no persistent disk — this stops the ephemeral
        disk from filling and crashing the service. Adjust the cap via TL_MAX_LIBRARY_MB.
        """
        files = []
        total = 0
        for dirpath, _, fnames in os.walk(self.root):
            for f in fnames:
                p = os.path.join(dirpath, f)
                try:
                    sz = os.path.getsize(p)
                except OSError:
                    continue
                files.append((os.path.getmtime(p), p, sz))
                total += sz
        if total <= self.max_bytes:
            return
        files.sort()  # oldest first by mtime
        for _, path, sz in files:
            if total <= self.max_bytes:
                break
            try:
                os.remove(path)
                total -= sz
                log.info("pruned %s to stay under library cap", path)
            except OSError:
                continue
