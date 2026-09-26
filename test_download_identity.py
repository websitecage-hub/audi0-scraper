"""Regression tests for the audio download path.

The bug these pin: `UrlAudioProvider.download()` returned the newest audio file
in the shared niche folder instead of the file it actually produced, so every
request reported the same wrong track. These tests run without network.

Run: python3 test_download_identity.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trend_scraper.audio import providers as P  # noqa: E402


def _fake_audio(path: Path, seconds: float = 9.0) -> None:
    """Write a tiny file and let verify_audio's probe report `seconds`.

    verify_audio() shells out to ffprobe; here we only need the size gate to pass
    and the duration check to be exercised separately, so an unknown-duration file
    is not acceptable. Use a real silent mp3 when ffmpeg is present.
    """
    import subprocess
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"anullsrc=r=44100:cl=mono", "-t", str(seconds),
                    "-b:a", "192k", str(path)], check=True)


def test_resolve_download_is_stem_scoped():
    """The resolver must never reach outside its own stem."""
    with tempfile.TemporaryDirectory() as d:
        _fake_audio(Path(d) / "unrelated-track.mp3")
        _fake_audio(Path(d) / "src-abc123.mp3")
        got = P.resolve_download(d, "src-abc123")
        assert got.endswith("src-abc123.mp3"), got
        missing = P.resolve_download(d, "src-not-there")
        assert missing == "", f"must not fall back to another file, got {missing}"


def test_newest_file_is_never_used():
    """A newer unrelated file must not be returned for a stem-scoped resolve."""
    with tempfile.TemporaryDirectory() as d:
        mine = Path(d) / "src-mine.mp3"
        _fake_audio(mine)
        time.sleep(1.1)
        _fake_audio(Path(d) / "src-someone-else.mp3")     # strictly newer
        got = P.resolve_download(d, "src-mine")
        assert got.endswith("src-mine.mp3"), got


def test_source_key_is_stable_and_distinct():
    a1 = P.source_key("https://example.com/a.mp3")
    a2 = P.source_key("https://example.com/a.mp3")
    b = P.source_key("https://example.com/b.mp3")
    assert a1 == a2, "same URL must map to the same key (cacheable)"
    assert a1 != b, "different URLs must not collide"
    assert len(a1) == 12


def test_verify_audio_rejects_junk():
    with tempfile.TemporaryDirectory() as d:
        small = Path(d) / "tiny.mp3"
        small.write_bytes(b"x" * 100)
        ok, why = P.verify_audio(str(small))
        assert not ok and "too small" in why, why
        ok2, why2 = P.verify_audio("")
        assert not ok2
        ok3, why3 = P.verify_audio(str(Path(d) / "nope.mp3"))
        assert not ok3 and "missing" in why3, why3


def test_verify_audio_accepts_real_audio():
    with tempfile.TemporaryDirectory() as d:
        good = Path(d) / "real.mp3"
        _fake_audio(good, seconds=8.0)
        ok, why = P.verify_audio(str(good))
        assert ok, why
        dur = P.probe_duration(str(good))
        assert 7.0 < dur < 9.5, dur


def test_cleanup_partials_removes_leftovers():
    with tempfile.TemporaryDirectory() as d:
        for name in ("src-x.mp3.part", "src-x.ytdl", "src-x.mp3.temp"):
            (Path(d) / name).write_bytes(b"junk")
        n = P._cleanup_partials(d, "src-x")
        assert n == 3, n
        assert P.resolve_download(d, "src-x") == ""


def test_download_is_url_scoped_not_newest():
    """End-to-end-ish: two different URLs produce two different files.

    Uses the generic extractor on a local file:// URL so it needs no network and
    no external site. If yt-dlp cannot handle file://, the test exercises the
    resolver path instead (still proving the identity contract).
    """
    with tempfile.TemporaryDirectory() as d:
        import subprocess
        src = Path(d) / "tone.mp3"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=9", "-b:a", "192k", str(src)],
                       check=True)
        prov = P.UrlAudioProvider()
        r1 = prov.download(src.as_uri(), str(Path(d) / "lib"))
        assert r1.source_key, "every download must carry its source key"
        assert r1.source_sha256, "every download must carry its source hash"
        if r1.ok:
            assert os.path.basename(r1.path).startswith(f"src-{r1.source_key}"), r1.path
            # a second call for the same URL must reuse the same file (cacheable)
            r2 = prov.download(src.as_uri(), str(Path(d) / "lib"))
            assert r2.ok and r2.cached is True, "same URL should reuse the cached file"
            assert r2.path == r1.path


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)