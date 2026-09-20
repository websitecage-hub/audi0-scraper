#!/usr/bin/env python3
"""trend-audio — pull trending Instagram Reels audio by niche, save the data, and
download the actual audio into a local library.

Commands:
    fetch   "niche" [--top N] [--json]        discover + save trending audio data
    download "Title — Artist" [--niche X] [--reel-url URL]
    sync    "niche" [--top N] [--provider]    download top N tracks for a niche
    library "niche"                           show what's already downloaded

Examples:
    python cli.py fetch "self improvement" --json
    python cli.py sync gym --top 10
    python cli.py download "Inner Light — Elderbrook & Bob Moses" --niche self-improvement
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from trend_scraper import Niche, TrendingAudioEngine
from trend_scraper.audio import AudioLibrary

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE = os.path.join(ROOT, "data")
DEFAULT_LIBRARY = os.path.join(ROOT, "library")


def _split_track(s: str):
    """Parse 'Title — Artist' / 'Title - Artist' -> (title, artist)."""
    for sep in ("\u2014", "\u2013", "\u2015", "—", "–", "-"):
        if sep in s:
            t, a = s.rsplit(sep, 1)
            return t.strip(), a.strip()
    return s.strip(), ""


def _print_table(tracks, top: int) -> None:
    print(f"\n  TOP {min(top, len(tracks))} TRENDING AUDIO (niche-scored)\n")
    print(f"  {'#':<3}{'SCORE':<6}{'TRACK':<62}ORIGIN")
    print("  " + "-" * 96)
    for i, t in enumerate(tracks[:top], 1):
        print(f"  {i:<3}{t.niche_score:<6}{t.display[:60]:<62}{t.origin_label or 'general'}")
    print(f"\n  ({len(tracks)} unique tracks. Cache: data/  |  Audio: library/)")


def cmd_fetch(args) -> int:
    engine = TrendingAudioEngine(
        cache_dir=None if args.no_cache else DEFAULT_CACHE,
        use_evergreen=not args.no_evergreen,
    )
    niche = Niche.from_name(args.niche)
    tracks = engine.collect(niche, use_cache=not args.no_cache)
    if args.json:
        print(json.dumps([t.__dict__ for t in tracks[: args.top]], indent=2))
        return 0
    _print_table(tracks, args.top)
    return 0


def cmd_download(args) -> int:
    from types import SimpleNamespace
    lib = AudioLibrary(DEFAULT_LIBRARY, provider=args.provider,
                       cookies_file=args.cookies, quality=args.quality)
    if getattr(args, "url", None):
        entry = lib.download_url(args.url, niche=args.niche or "manual",
                                 title=args.title, artist=args.artist)
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        return 0 if entry.get("ok") else 2
    title, artist = _split_track(args.track)
    if not title:
        print("Supply 'Title — Artist' OR a --url.")
        return 1
    tr = SimpleNamespace(title=title, artist=artist, niche_score=0, origin_label="manual")
    entry = lib.save(args.niche or "manual", tr, if_missing=not args.overwrite,
                     reel_url=args.reel_url)
    print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0 if entry.get("ok") else 2


def cmd_sync(args) -> int:
    engine = TrendingAudioEngine(cache_dir=DEFAULT_CACHE,
                                 use_evergreen=not args.no_evergreen)
    niche = Niche.from_name(args.niche)
    tracks = engine.collect(niche, use_cache=not args.no_cache)
    lib = AudioLibrary(DEFAULT_LIBRARY, provider=args.provider,
                       cookies_file=args.cookies, quality=args.quality)
    print(f"\n  Syncing up to {args.top} tracks for niche '{args.niche}'...\n")
    start = time.time()
    summary = lib.sync(args.niche, tracks, limit=args.top,
                       if_missing=not args.overwrite)
    print(json.dumps({k: v for k, v in summary.items() if k != "items"}, indent=2))
    print(f"\n  done in {time.time() - start:.1f}s -> {DEFAULT_LIBRARY}")
    return 0


def cmd_library(args) -> int:
    p = os.path.join(DEFAULT_LIBRARY, args.niche.strip().lower().replace(" ", "-"),
                     "manifest.json")
    if not os.path.exists(p):
        print(f"no library for '{args.niche}' — run sync first.")
        return 1
    entries = json.load(open(p))
    print(f"\n  {len(entries)} saved track(s) for '{args.niche}':\n")
    for e in entries:
        mark = "OK " if e.get("ok") else "FAIL"
        print(f"  [{mark}] {e['title']} — {e.get('artist','')}  ({e.get('path','')})")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Trending Reels audio (Scrapling) + downloader.")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="discover + save trending audio data")
    f.add_argument("niche")
    f.add_argument("--top", type=int, default=20)
    f.add_argument("--json", action="store_true")
    f.add_argument("--no-cache", action="store_true")
    f.add_argument("--no-evergreen", action="store_true")
    f.set_defaults(fn=cmd_fetch)

    d = sub.add_parser("download", help="download one track's audio (or a media URL)")
    d.add_argument("track", nargs="?", help="'Title — Artist' (omit if --url given)")
    d.add_argument("--url", help="media URL: Instagram reel / YouTube / SoundCloud")
    d.add_argument("--title", help="override title (with --url)")
    d.add_argument("--artist", help="override artist (with --url)")
    d.add_argument("--niche", default="manual")
    d.add_argument("--reel-url", help="real Instagram reel URL (provider=instagram)")
    d.add_argument("--provider",
                   choices=["auto", "youtube", "soundcloud", "instagram"],
                   default="auto")
    d.add_argument("--cookies", help="path to cookies.json for Instagram")
    d.add_argument("--quality", type=int, default=192)
    d.add_argument("--overwrite", action="store_true")
    d.set_defaults(fn=cmd_download)

    s = sub.add_parser("sync", help="download top N tracks for a niche")
    s.add_argument("niche")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--provider",
                   choices=["auto", "youtube", "soundcloud", "instagram"],
                   default="auto")
    s.add_argument("--cookies")
    s.add_argument("--quality", type=int, default=192)
    s.add_argument("--overwrite", action="store_true")
    s.add_argument("--no-cache", action="store_true")
    s.add_argument("--no-evergreen", action="store_true")
    s.set_defaults(fn=cmd_sync)

    l = sub.add_parser("library", help="list downloaded tracks for a niche")
    l.add_argument("niche")
    l.set_defaults(fn=cmd_library)

    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
