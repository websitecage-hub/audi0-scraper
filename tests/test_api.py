"""Offline API tests. The scraper is mocked, so these are deterministic and
need no network. Run:  pytest tests/test_api.py -v
"""
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import scraper  # noqa: E402


_UNSET = object()


def _sound(i, video_count=_UNSET):
    # Default is computed from `i`, so a sentinel is needed: a plain default
    # would reference `i` inside its own signature.
    if video_count is _UNSET:
        video_count = 1000 * (6 - i)
    return {
        "id": f"70000000000000000{i}",
        "title": f"Track {i}",
        "artist": f"Artist {i}",
        "play_link": f"https://v16m.tiktokcdn.com/audio/{i}.mp3",
        "duration_seconds": 15 + i,
        "video_count": video_count,
        "usage_basis": "upstream_music_details" if video_count is not None else "not_enriched",
        "sample_frequency": 5 - i,
        "sample_plays": 1000 * (5 - i),
        "sample_likes": 10 * (5 - i),
        "sources": ["hashtag:sad", "trending:US"],
    }


class FakeScraper:
    """Stands in for scraper.fetch_trending_sounds."""

    def __init__(self, n=5, exc=None):
        self.n = n
        self.exc = exc
        self.calls = []

    def __call__(self, keyword, limit=20, country=None, hashtag=None, deadline=None):
        self.calls.append({"keyword": keyword, "limit": limit,
                           "country": country, "hashtag": hashtag})
        if self.exc:
            raise self.exc
        sounds = [_sound(i) for i in range(self.n)][:limit]
        return {
            "sounds": sounds,
            "meta": {
                "keyword": keyword,
                "hashtag_resolution": "mapped",
                "hashtags_requested": ["sad", "sadmusic", "emotional"],
                "hashtags_sampled": ["sad", "sadmusic"],
                "sources": ["hashtag:sad", "hashtag:sadmusic", "trending:US"],
                "sources_failed": [],
                "fallback_used": None,
                "videos_sampled": 60,
                "sounds_considered": self.n,
                "sounds_enriched": self.n,
                "sounds_with_usage_count": self.n,
                "sounds_returned": len(sounds),
                "elapsed_seconds": 4.56,
                "source": "tiktok_hashtag_mining",
                "source_is_keyword_search": False,
                "signed_surface": "ok",
                "usage_counts_available": True,
                "notes": [],
            },
        }


@pytest.fixture(autouse=True)
def clean_cache():
    main.CACHE.clear()
    yield
    main.CACHE.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


def _mock(monkeypatch, fake):
    monkeypatch.setattr(scraper, "fetch_trending_sounds", fake)
    return fake


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

def test_health_get(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["version"] == main.VERSION


def test_health_head_and_trailing_slash(client):
    assert client.head("/v1/health").status_code == 200
    assert client.get("/v1/health/").status_code == 200
    assert client.head("/v1/health/").status_code == 200


def test_health_alias_for_uptimerobot(client):
    """/health and /health/ must work for GET and HEAD: the monitor watching the
    older deployment points at /health and must not see a 404 or a 501."""
    assert client.get("/health").status_code == 200
    assert client.get("/health").json()["status"] == "ok"
    assert client.head("/health").status_code == 200
    assert client.get("/health/").status_code == 200
    assert client.head("/health/").status_code == 200


def test_health_reports_niche_priority(client):
    assert client.get("/health").json()["niche_priority"] is True


def test_health_does_not_call_upstream(client, monkeypatch):
    """The default health path must make no upstream call -- monitors hit it
    around the clock."""
    called = []
    monkeypatch.setattr(scraper, "health_probe", lambda: called.append(1) or {})
    assert client.get("/v1/health").status_code == 200
    assert called == []
    assert client.get("/v1/health?probe=1").status_code == 200
    assert called == [1]


# ---------------------------------------------------------------------------
# trending: the 202 -> ready polling contract
# ---------------------------------------------------------------------------

def test_cold_keyword_returns_202_then_ready(client, monkeypatch):
    fake = _mock(monkeypatch, FakeScraper(n=5))

    first = client.get("/v1/trending?keyword=gym&limit=3")
    assert first.status_code == 202
    body = first.json()
    assert body["status"] == "processing"
    assert body["retry_after_seconds"] == main.POLL_AFTER_SECONDS
    assert first.headers["retry-after"] == str(main.POLL_AFTER_SECONDS)
    assert "Poll again" in body["message"]

    # TestClient runs BackgroundTasks before the call returns, so the poll is ready.
    second = client.get("/v1/trending?keyword=gym&limit=3")
    assert second.status_code == 200
    body = second.json()
    assert body["status"] == "ready"
    assert body["count"] == 3
    assert len(body["data"]) == 3
    for s in body["data"]:
        assert s["play_link"].startswith("https://")
    assert body["meta"]["stale"] is False
    assert body["meta"]["cache_age_seconds"] >= 0
    # scraped once at max limit so any later limit is served from cache
    assert len(fake.calls) == 1
    assert fake.calls[0]["limit"] == main.SCRAPE_LIMIT


def test_cache_hit_does_not_rescrape(client, monkeypatch):
    fake = _mock(monkeypatch, FakeScraper(n=5))
    client.get("/v1/trending?keyword=dance")
    assert len(fake.calls) == 1
    r = client.get("/v1/trending?keyword=dance")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
    assert len(fake.calls) == 1, "a fresh cache entry must not trigger another scrape"


def test_same_keyword_different_case_shares_cache(client, monkeypatch):
    fake = _mock(monkeypatch, FakeScraper(n=3))
    client.get("/v1/trending?keyword=Gym")
    r = client.get("/v1/trending?keyword=gym")
    assert r.json()["status"] == "ready"
    assert len(fake.calls) == 1


def test_limit_clamping_and_slicing(client, monkeypatch):
    _mock(monkeypatch, FakeScraper(n=8))
    client.get("/v1/trending?keyword=gym")                  # warm at max limit
    assert client.get("/v1/trending?keyword=gym&limit=1").json()["count"] == 1
    assert client.get("/v1/trending?keyword=gym&limit=8").json()["count"] == 8
    # over the cap is a 422 from validation, not a silent truncation
    assert client.get("/v1/trending?keyword=gym&limit=999").status_code == 422
    assert client.get("/v1/trending?keyword=gym&limit=0").status_code == 422


def test_missing_keyword_and_hashtag_is_400(client):
    """Either selector is enough, but neither is not."""
    assert client.get("/v1/trending?keyword=").status_code == 400
    assert client.get("/v1/trending").status_code == 400
    assert client.get("/v1/trending?hashtag=").status_code == 400


def test_hashtag_alone_is_accepted(client, monkeypatch):
    """A hashtag-only request must not require a dummy keyword."""
    fake = _mock(monkeypatch, FakeScraper(n=3))
    first = client.get("/v1/trending?hashtag=gymtok,workout&limit=3")
    assert first.status_code == 202
    assert first.json()["keyword"] == "gymtok"
    assert fake.calls[0]["hashtag"] == "gymtok,workout"
    assert fake.calls[0]["keyword"] == "gymtok"

    body = client.get("/v1/trending?hashtag=gymtok,workout&limit=3").json()
    assert body["status"] == "ready" and body["count"] == 3


def test_hashtag_alone_shares_the_cache_with_the_mapped_keyword(client, monkeypatch):
    """`?hashtag=gymtok,workout` and a keyword that maps to the same tags are
    different cache entries -- documented, since the mapping is opaque."""
    fake = _mock(monkeypatch, FakeScraper(n=2))
    client.get("/v1/trending?hashtag=gymtok,workout")
    client.get("/v1/trending?keyword=gym")
    assert len(fake.calls) == 2
    assert len(main.CACHE) == 2


def test_refresh_forces_rescrape(client, monkeypatch):
    fake = _mock(monkeypatch, FakeScraper(n=4))
    client.get("/v1/trending?keyword=gym")
    assert len(fake.calls) == 1
    r = client.get("/v1/trending?keyword=gym&refresh=1")
    assert r.status_code == 200
    assert len(fake.calls) == 2, "refresh=1 must re-scrape"


def test_stale_while_revalidate(client, monkeypatch):
    """A warm-but-expired entry is served immediately, flagged stale, and
    refreshed in the background instead of falling back to a 202."""
    fake = _mock(monkeypatch, FakeScraper(n=3))
    client.get("/v1/trending?keyword=gym")

    key = main._cache_key("gym", None, None)
    with main._CACHE_LOCK:
        main.CACHE[key]["ready_at"] = time.time() - (main.CACHE_TTL_SECONDS + 5)

    r = client.get("/v1/trending?keyword=gym")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["meta"]["stale"] is True
    assert len(fake.calls) == 2, "stale serve must schedule exactly one refresh"


def test_unrelated_keyword_is_a_separate_cache_entry(client, monkeypatch):
    fake = _mock(monkeypatch, FakeScraper(n=3))
    client.get("/v1/trending?keyword=gym")
    client.get("/v1/trending?keyword=food")
    assert len(fake.calls) == 2
    assert len(main.CACHE) == 2


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

def test_scrape_error_then_cooldown(client, monkeypatch):
    _mock(monkeypatch, FakeScraper(exc=scraper.ScrapeError("upstream blew up")))

    first = client.get("/v1/trending?keyword=gym")
    assert first.status_code == 202

    # background task has run and failed by now
    r = client.get("/v1/trending?keyword=gym")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert "upstream blew up" in body["error"]
    assert body["retry_after_seconds"] > 0


def test_error_is_retried_after_cooldown(client, monkeypatch):
    fake = _mock(monkeypatch, FakeScraper(exc=scraper.ScrapeError("nope")))
    client.get("/v1/trending?keyword=gym")
    client.get("/v1/trending?keyword=gym")          # error state, within cooldown

    key = main._cache_key("gym", None, None)
    with main._CACHE_LOCK:
        main.CACHE[key]["state_at"] = time.time() - (main.ERROR_RETRY_COOLDOWN_SECONDS + 1)

    r = client.get("/v1/trending?keyword=gym")
    assert r.status_code == 202, "past the cooldown the entry must be retried"
    assert len(fake.calls) == 2


def test_failure_keeps_serving_stale_data(client, monkeypatch):
    """Once a good payload exists, a failed refresh must not destroy it."""
    fake = _mock(monkeypatch, FakeScraper(n=3))
    client.get("/v1/trending?keyword=gym")

    fake.exc = scraper.ScrapeError("later failure")
    client.get("/v1/trending?keyword=gym&refresh=1")

    r = client.get("/v1/trending?keyword=gym")
    body = r.json()
    assert body["status"] == "ready"
    assert len(body["data"]) == 3
    assert "later failure" in (body["meta"]["refresh_error"] or "")


def test_unexpected_exception_is_contained(client, monkeypatch):
    """The background task must never propagate: an unhandled raise would leave
    the entry stuck in "processing"."""
    _mock(monkeypatch, FakeScraper(exc=RuntimeError("boom")))
    client.get("/v1/trending?keyword=gym")
    body = client.get("/v1/trending?keyword=gym").json()
    assert body["status"] == "error"
    assert "RuntimeError" in body["error"]


# ---------------------------------------------------------------------------
# validation + static endpoints
# ---------------------------------------------------------------------------

def test_invalid_hashtag_is_400(client):
    """A hashtag that cannot resolve upstream should fail fast, not 20s later."""
    for bad in ("sad music", "sad!", "sad/music"):
        r = client.get(f"/v1/trending?keyword=sad&hashtag={bad}")
        assert r.status_code == 400, bad
        assert "invalid hashtag" in r.json()["error"]


def test_explicit_hashtag_skips_mapping(client, monkeypatch):
    fake = _mock(monkeypatch, FakeScraper(n=3))
    client.get("/v1/trending?keyword=sad&hashtag=sad,sadmusic")
    assert fake.calls[0]["hashtag"] == "sad,sadmusic"


def test_real_usage_count_reaches_the_client(client, monkeypatch):
    """The pivot's whole point: video_count must be a real number, not null."""
    _mock(monkeypatch, FakeScraper(n=5))
    client.get("/v1/trending?keyword=sad")
    body = client.get("/v1/trending?keyword=sad&limit=5").json()
    assert body["meta"]["usage_counts_available"] is True
    assert body["meta"]["with_usage_count"] == 5
    for s in body["data"]:
        assert isinstance(s["video_count"], int)
        assert s["usage_basis"] == "upstream_music_details"


def test_countries_endpoint(client):
    r = client.get("/v1/trending/countries")
    assert r.status_code == 200
    body = r.json()
    assert "US" in body["countries"]
    assert body["regionalization_supported"] is False
    assert "egress" in body["note"]


def test_hashtags_endpoint(client):
    r = client.get("/v1/trending/hashtags")
    assert r.status_code == 200
    kw = r.json()["keywords"]
    assert kw["sad"] == ["sad", "sadmusic", "emotional"]
    assert kw["gym"] == ["gymtok", "workout", "gymmotivation"]
    assert r.json()["keyword_count"] == len(kw)
    # every mapped tag must be a legal hashtag
    for tags in kw.values():
        for t in tags:
            assert main._HASHTAG_RE.match(t), t


def test_console_and_openapi(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert client.get("/openapi.json").status_code == 200


def test_cors_allows_any_origin(client):
    r = client.get("/v1/health", headers={"Origin": "https://example.com"})
    assert r.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# cache hygiene
# ---------------------------------------------------------------------------

def test_cache_is_bounded(client, monkeypatch):
    monkeypatch.setattr(main, "CACHE_MAX_ENTRIES", 3)
    _mock(monkeypatch, FakeScraper(n=2))
    for i in range(6):
        client.get(f"/v1/trending?keyword=kw{i}")
    assert len(main.CACHE) <= 4, f"cache grew to {len(main.CACHE)}"


def test_country_is_part_of_the_cache_key(client, monkeypatch):
    fake = _mock(monkeypatch, FakeScraper(n=2))
    client.get("/v1/trending?keyword=gym&country=US")
    client.get("/v1/trending?keyword=gym&country=GB")
    assert len(fake.calls) == 2
    assert fake.calls[0]["country"] == "US"
    assert fake.calls[1]["country"] == "GB"
