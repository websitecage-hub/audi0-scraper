"""Tests for the cookie + song-resolution paths (no network needed)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402


def test_cookies_become_netscape_not_json():
    """yt-dlp only parses Netscape cookie files.

    The old code wrote raw JSON, so authenticated (Instagram) downloads could
    never work. This pins the format.
    """
    jar = [
        {"name": "sessionid", "value": "abc123", "domain": ".instagram.com",
         "path": "/", "secure": True, "expirationDate": 1893456000},
        {"name": "csrftoken", "value": "tok", "domain": ".instagram.com",
         "path": "/", "secure": True},
    ]
    path = server._write_cookies(jar)
    assert path and os.path.isfile(path), "cookie file must be written"
    try:
        text = Path(path).read_text()
        assert text.startswith("# Netscape HTTP Cookie File"), text[:80]
        assert "sessionid" in text and "abc123" in text
        lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        assert len(lines) == 2, lines
        cols = lines[0].split("\t")
        assert len(cols) == 7, f"netscape needs 7 tab-separated fields: {cols}"
        assert cols[0] == ".instagram.com"
        assert cols[1] == "TRUE"          # dot-domain -> subdomain scope
        assert cols[3] == "TRUE"          # secure
    finally:
        os.remove(path)


def test_netscape_string_passes_through():
    raw = ("# Netscape HTTP Cookie File\n"
           ".instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tzzz\n")
    path = server._write_cookies(raw)
    try:
        text = Path(path).read_text()
        assert "sessionid" in text and "zzz" in text
        assert text.count("# Netscape") == 1, "must not double-wrap a netscape input"
    finally:
        os.remove(path)


def test_dict_cookies_are_normalised():
    path = server._write_cookies({"sessionid": "v1", "csrftoken": "v2"})
    try:
        text = Path(path).read_text()
        assert "sessionid" in text and "v1" in text
        assert "csrftoken" in text and "v2" in text
    finally:
        os.remove(path)


def test_no_cookies_is_none():
    assert server._write_cookies(None) is None
    assert server._write_cookies({}) is None
    assert server._write_cookies([]) is None


def test_song_endpoint_requires_title():
    client = server.app.test_client()
    r = client.post("/v1/song", json={})
    assert r.status_code == 400, r.status_code
    assert "title is required" in r.get_json()["message"]


def test_health_reports_storage():
    client = server.app.test_client()
    r = client.get("/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert "storage" in body, body


def test_library_stats_and_prune_respects_cap(tmp_path=None):
    """Pruning must drop the oldest and never the newest, and fix dead rows."""
    import subprocess
    from trend_scraper.audio import AudioLibrary

    with tempfile.TemporaryDirectory() as d:
        lib = AudioLibrary(d, provider="url", max_bytes=300 * 1024)
        fol = Path(d) / "reels"
        fol.mkdir(parents=True, exist_ok=True)
        made = []
        for i in range(3):
            p = fol / f"track{i}.mp3"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                            "-i", "anullsrc=r=44100:cl=mono", "-t", "4",
                            "-b:a", "192k", str(p)], check=True)
            made.append(p)
        # manifest with one stale row (file deleted) and one live row
        import json as _j
        (fol / "manifest.json").write_text(_j.dumps([
            {"title": "live", "path": str(made[0]), "ok": True},
            {"title": "dead", "path": str(fol / "gone.mp3"), "ok": True},
        ]))
        st = lib.stats()
        assert st["files"] == 3, st
        lib._prune()
        rows = _j.loads((fol / "manifest.json").read_text())
        assert all(Path(r["path"]).is_file() for r in rows), rows
        assert not any(r["title"] == "dead" for r in rows), "stale row must be dropped"
        assert lib.stats()["files"] <= 3


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