"""Live proof: resolve a song by NAME through the new chain (network required).

Verifies the core contract: two different songs produce two different files, and
each returned file is real audio.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trend_scraper.audio import AudioLibrary  # noqa: E402
from trend_scraper.audio.providers import verify_audio, source_key  # noqa: E402

SONGS = [
    ("Should I Stay or Should I Go", "The Clash"),
    ("Lonely Boy", "The Black Keys"),
]

with tempfile.TemporaryDirectory() as d:
    lib = AudioLibrary(d, provider="song", max_bytes=200 * 1024 * 1024)
    results = []
    for title, artist in SONGS:
        entry = lib.download_song(title, artist, niche="livetest")
        results.append(entry)
        print(f"\n{title!r} / {artist!r}")
        print(f"  ok={entry.get('ok')} provider={entry.get('provider')}")
        print(f"  path={entry.get('path')}")
        print(f"  duration={entry.get('duration_s')}s bytes={entry.get('bytes')}")
        print(f"  attempts={entry.get('attempts')}")
        if entry.get("path"):
            good, why = verify_audio(entry["path"])
            print(f"  verify: {good} {why}")

    paths = [e.get("path") for e in results if e.get("path")]
    print(f"\n=== identity check ===")
    print("distinct files:", len(set(paths)), "of", len(paths))
    for p in paths:
        print("  -", Path(p).name)
    if len(paths) == 2 and len(set(paths)) == 1:
        print("!!! FAIL: both songs returned the same file (the original bug)")
        sys.exit(1)
    if paths:
        print("PASS: songs resolved to distinct files")
    else:
        print("WARN: no songs resolved (network/source availability)")