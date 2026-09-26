"""Tests for the trend metrics, niche registry, and extraction rank capture.

Run:  .venv/bin/python -m pytest test_metrics.py -q
"""
from __future__ import annotations

from trend_scraper.extract import Track, extract_tracks, relevance_score
from trend_scraper.metrics import (compute_score, confidence, corroboration_weight,
                                   momentum_weight, rank_weight, recency_boost)
from trend_scraper.niches import resolve


# ---- metrics ---------------------------------------------------------------

def test_corroboration_monotonic_and_capped():
    vals = [corroboration_weight(n) for n in range(0, 10)]
    assert vals[0] == 0.0
    assert all(b >= a for a, b in zip(vals, vals[1:])), "must be non-decreasing"
    assert vals[-1] <= 40.0 + 1e-9
    # 2 sources must beat 1 by a real margin (diminishing returns, not linear)
    assert vals[2] - vals[1] > vals[5] - vals[4]


def test_momentum_needs_current_hits():
    assert momentum_weight(0, 5) == 0.0
    assert momentum_weight(5, 5) > momentum_weight(1, 5)
    assert momentum_weight(5, 5) <= 20.0


def test_rank_weight_best_rank_scores_highest():
    assert rank_weight(1, 10) > rank_weight(9, 10)
    assert rank_weight(0, 10) == 0.0
    assert rank_weight(1, 10) <= 15.0


def test_recency_boost_decays_within_bounds():
    """Recency must decay with age, stay in [0.5, 1.0], and tolerate junk input.

    Uses RELATIVE dates: an absolute "yesterday" assertion decays into failure as
    the calendar moves (this test was red before the fix for exactly that reason).
    """
    from datetime import date, timedelta

    today = date.today().isoformat()
    recent = (date.today() - timedelta(days=1)).isoformat()
    old = (date.today() - timedelta(days=400)).isoformat()

    assert recency_boost(None) == 1.0
    assert recency_boost(today) == 1.0
    # fresher must beat staler, and a recent date must be a large fraction of 1.0
    assert recency_boost(recent, half_life_days=14) > recency_boost(old, half_life_days=14)
    assert recency_boost(recent, half_life_days=14) > 0.9
    # bounds: never below the 0.5 floor, never above 1.0
    assert 0.5 <= recency_boost(old, half_life_days=14) <= 1.0
    assert recency_boost("garbage-date") == 1.0


def test_compute_score_bounds_and_confidence():
    score, conf = compute_score(
        source_count=5, relevance=1.0, current_hits=5, total_hits=5,
        best_rank=1, total_tracks=10, last_seen="2026-09-20")
    assert 0 <= score <= 100
    assert 0 <= conf <= 1
    assert conf > 0.9, "5 sources should be high confidence"

    low_score, low_conf = compute_score(
        source_count=1, relevance=0.0, current_hits=0, total_hits=1,
        best_rank=0, total_tracks=1)
    assert low_score < score
    assert low_conf < conf


def test_confidence_increases_with_sources():
    assert confidence(1, 4) < confidence(3, 4) <= 1.0


# ---- niche registry --------------------------------------------------------

def test_niche_aliases_resolve_to_canonical():
    assert resolve("gym reels").name == "gym"
    assert resolve("fitness workout").name == "gym"
    assert resolve("self improvement").name == "self-improvement"
    assert resolve("personal growth").name == "self-improvement"
    assert resolve("make money online").name == "money"


def test_unknown_niche_still_builds_queries():
    n = resolve("knitting")
    assert n.name == "knitting"
    assert n.queries, "unknown niche must still produce search queries"


def test_relevance_rewards_mood_words():
    gym = resolve("gym")
    on_theme = Track(title="Eye Of The Tiger", artist="Survivor")
    off_theme = Track(title="Random Table", artist="Somebody Else")
    assert relevance_score(on_theme, gym) > relevance_score(off_theme, gym)


# ---- extraction ------------------------------------------------------------

def test_extract_captures_list_rank():
    page = """
    <ul>
      <li>1. Eye of the Tiger — Survivor</li>
      <li>2. Titanium — David Guetta</li>
      <li>3. Stronger — Kanye West</li>
    </ul>
    """
    tracks = extract_tracks(page, source="test")
    by_title = {t.title.lower(): t for t in tracks}
    assert "eye of the tiger" in by_title
    assert by_title["eye of the tiger"].best_rank == 1
    assert by_title["titanium"].best_rank == 2


def test_extract_skips_prose():
    page = "Best Instagram songs for reels this week — written by the team"
    tracks = extract_tracks(page, source="test")
    assert all("best instagram" not in t.title.lower() for t in tracks)


# ---- charts ----------------------------------------------------------------

def test_genre_normalization_onto_niche_keys():
    from trend_scraper.charts import _canon_genre, _genre_names
    assert _canon_genre("Hip-Hop/Rap") == "rap"
    assert _canon_genre("R&B/Soul") == "rnb"
    assert _canon_genre("Electronic") == "electro"
    # dict-form (what iTunes actually returns)
    assert _genre_names([{"genreId": "18", "name": "Dance"}]) == ["dance"]
    assert _genre_names(["Rock", ""]) == ["rock"]


def test_niche_to_genre_mapping():
    from trend_scraper.charts import genres_for_niche, DEFAULT_GENRES
    assert "rock" in genres_for_niche("gym")
    assert "rap" in genres_for_niche("money")
    assert genres_for_niche("totally-unknown-niche") == DEFAULT_GENRES
    # every mapped niche must reference real Deezer genre keys
    from trend_scraper.charts import DEEZER_GENRES, NICHE_GENRES
    for niche, gs in NICHE_GENRES.items():
        for g in gs:
            assert g in DEEZER_GENRES, f"{niche} -> unknown genre {g}"