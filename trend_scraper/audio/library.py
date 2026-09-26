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
                        InstagramReelProvider, UrlAudioProvider, SongSearchProvider,
                        probe_duration, source_key, AUDIO_EXTS)

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

        Storage discipline: one entry per source URL. Re-requesting a URL that is
        already on disk (and still valid) returns the existing entry without
        downloading or adding a manifest row — previously every call appended a
        duplicate row plus a duplicate file, which is how the library filled up.
        """
        folder = os.path.join(self.root, _safe_folder(niche))
        os.makedirs(folder, exist_ok=True)

        from .providers import source_sha256 as _sha

        sha = _sha(url)
        with _MANIFEST_LOCK:
            entries = self._load_manifest(niche)
            for e in entries:
                if e.get("sha256") == sha and e.get("ok") and os.path.isfile(e.get("path", "")):
                    return {**e, "reused": True}

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
            # provenance: lets callers (and this library) prove which source a
            # file came from instead of trusting a filename
            "sha256": res.source_sha256,
            "source_key": res.source_key,
            "bytes": res.bytes,
            "duration_s": round(res.duration_s, 2),
            "cached": res.cached,
            "downloaded": date.today().isoformat(),
        }
        if res.ok:
            with _MANIFEST_LOCK:
                entries = self._load_manifest(niche)
                # replace any stale row for this source rather than appending a dup
                entries = [e for e in entries if e.get("sha256") != res.source_sha256]
                entries.append(entry)
                self._save_manifest(niche, entries)
            self._prune()
        return entry

    # ---- song-by-name (trending track -> audio, Instagram-first) -------------
    def download_song(self, title: str, artist: str = "", niche: str = "reels",
                      instagram_url: str | None = None) -> dict:
        """Resolve a song NAME (e.g. straight from /v1/trending) to audio.

        Order:
          1. an explicit Instagram URL/reel when the caller has one
          2. Instagram audio-page search for the trending sound name
          3. SoundCloud, then YouTube, for the plain song name

        Step 1-2 need session cookies; without them Instagram is skipped rather
        than burning the request, and the name search carries it.
        """
        folder = os.path.join(self.root, _safe_folder(niche))
        os.makedirs(folder, exist_ok=True)
        attempts = []

        if instagram_url:
            ig = InstagramReelProvider(cookies_file=self.cookies)
            res = ig.download(instagram_url, None, folder)
            attempts.append({"source": "instagram_url", "ok": res.ok,
                             "message": res.message})
            if res.ok:
                return self._record_entry(niche, title, artist, res, instagram_url)

        if self.cookies:
            ig = InstagramReelProvider(cookies_file=self.cookies)
            res = ig.download_sound(title, artist, folder)
            attempts.append({"source": "instagram_search", "ok": res.ok,
                             "message": res.message})
            if res.ok:
                return self._record_entry(niche, title, artist, res, "")
        else:
            attempts.append({"source": "instagram_search", "ok": False,
                             "message": "skipped: no cookies supplied"})

        from .providers import SongTrack

        res = SongSearchProvider(quality=self.quality).download(
            SongTrack(title=title, artist=artist), folder)
        attempts.append({"source": "songsearch", "ok": res.ok,
                         "message": res.message})
        entry = self._record_entry(niche, title, artist, res, "")
        entry["attempts"] = attempts
        return entry

    def _record_entry(self, niche: str, title: str, artist: str, res, source: str) -> dict:
        path = getattr(res, "path", "") or ""
        size = 0
        if path and os.path.isfile(path):
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
        # Measure provenance here rather than trusting the provider: the search
        # providers don't carry bytes/duration, and a manifest with a 0-byte or
        # 0-second row is how "storage in mind" silently breaks.
        dur = probe_duration(path) if path and os.path.isfile(path) else 0.0
        entry = {
            "title": title, "artist": artist, "niche": niche,
            "path": path, "provider": getattr(res, "provider", ""),
            "ok": bool(getattr(res, "ok", False)),
            "message": getattr(res, "message", ""),
            "source_url": source or "",
            "sha256": getattr(res, "source_sha256", ""),
            "source_key": getattr(res, "source_key", "") or (source_key(source) if source else ""),
            "bytes": size or getattr(res, "bytes", 0),
            "duration_s": round(dur or (getattr(res, "duration_s", 0.0) or 0.0), 2),
            "downloaded": date.today().isoformat(),
        }
        if entry["ok"]:
            with _MANIFEST_LOCK:
                entries = self._load_manifest(niche)
                entries.append(entry)
                self._save_manifest(niche, entries)
            self._prune()
        return entry

    # -- storage cap (keep the free-tier disk from filling up) ------------------
    def _audio_files(self) -> list[tuple[float, str, int]]:
        """Every audio file under the library root: (mtime, path, bytes)."""
        files = []
        for dirpath, _, fnames in os.walk(self.root):
            for f in fnames:
                if f.rsplit(".", 1)[-1].lower() not in AUDIO_EXTS:
                    continue
                p = os.path.join(dirpath, f)
                try:
                    files.append((os.path.getmtime(p), p, os.path.getsize(p)))
                except OSError:
                    continue
        return files

    def stats(self) -> dict:
        """Disk accounting for the library — used by /health and before downloads."""
        files = self._audio_files()
        total = sum(sz for _, _, sz in files)
        return {"files": len(files), "bytes": total,
                "mb": round(total / 1048576, 2),
                "cap_mb": round(self.max_bytes / 1048576, 2),
                "over_cap": total > self.max_bytes}

    def _drop_manifest_rows(self, *, paths: set[str] | None = None,
                            keep: set[str] | None = None) -> None:
        """Drop manifest rows whose file is gone (or that aren't in `keep`)."""
        with _MANIFEST_LOCK:
            for dirpath, _, fnames in os.walk(self.root):
                if "manifest.json" not in fnames:
                    continue
                mp = os.path.join(dirpath, "manifest.json")
                try:
                    rows = json.load(open(mp))
                except Exception:
                    continue
                kept = [e for e in rows
                        if (keep is not None and e.get("path") in keep)
                        or (keep is None and e.get("path")
                            and os.path.isfile(e.get("path", "")))]
                if len(kept) != len(rows):
                    with open(mp, "w") as fh:
                        json.dump(kept, fh, indent=2, ensure_ascii=False)

    def _prune(self) -> None:
        """Keep the library under `max_bytes` by dropping the OLDEST tracks.

        Prunes whole tracks (file + its manifest row) oldest-first until the cap
        is met, and always removes manifest rows whose file has vanished. The cap
        must leave headroom: this runs mid-request, so a runaway library would
        otherwise fill the disk before the next prune.
        """
        files = self._audio_files()
        total = sum(sz for _, _, sz in files)
        removed = 0
        if total > self.max_bytes:
            # Never prune the file we just wrote: newest mtime is kept.
            files.sort()                      # oldest first
            for _, path, sz in files[:-1]:    # last entry = newest = keep
                if total <= self.max_bytes:
                    break
                try:
                    os.remove(path)
                    total -= sz
                    removed += 1
                    log.info("pruned %s to stay under library cap", path)
                except OSError:
                    continue
        # Drop manifest rows pointing at files that no longer exist.
        with _MANIFEST_LOCK:
            for dirpath, _, fnames in os.walk(self.root):
                if "manifest.json" not in fnames:
                    continue
                mp = os.path.join(dirpath, "manifest.json")
                try:
                    rows = json.load(open(mp))
                except Exception:
                    continue
                kept = [e for e in rows
                        if e.get("path") and os.path.isfile(e.get("path", ""))]
                if len(kept) != len(rows):
                    with open(mp, "w") as fh:
                        json.dump(kept, fh, indent=2, ensure_ascii=False)
        if removed:
            log.info("prune removed %d file(s); library now %.1fMB",
                     removed, total / 1048576)
