"""Regression: the deployed image had no `requests`, so /v1/song 500'd.

Guards the fallback HTTP layer that makes the audio sources work with or without
`requests` installed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trend_scraper import std_http  # noqa: E402


def test_get_works_with_or_without_requests():
    """A real GET must succeed regardless of which transport is available."""
    r = std_http.get("https://archive.org/advancedsearch.php",
                     params={"q": "mediatype:audio", "fl[]": "identifier",
                             "rows": 1, "output": "json"}, timeout=90)
    assert r.status_code == 200, f"status {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert isinstance(body, dict) and body, "expected a JSON body"


def test_response_survives_non_json_bodies():
    r = std_http.Response(500, "<html>boom</html>")
    assert r.json() == {}, "non-JSON must not raise"


def test_no_module_named_requests_is_handled(monkeypatch=None):
    """Simulate the slim image: requests absent -> urllib path still fetches."""
    saved = std_http._requests
    try:
        std_http._requests = None
        assert std_http.available() is False
        r = std_http.get("https://archive.org/advancedsearch.php",
                         params={"q": "mediatype:audio", "rows": 1, "output": "json"},
                         timeout=90)
        assert r.status_code == 200, f"urllib fallback failed: {r.status_code}"
    finally:
        std_http._requests = saved


def test_provider_imports_without_requests():
    """SongSearchProvider must not import requests at module scope."""
    src = (Path(__file__).resolve().parent /
           "trend_scraper" / "audio" / "providers.py").read_text()
    assert "import requests" not in src, \
        "providers.py must use the std_http fallback, not a hard requests import"


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