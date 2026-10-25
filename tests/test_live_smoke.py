"""Live smoke test: hits TikTok for real. Requires network.

Run directly:   python tests/test_live_smoke.py
Or via pytest:   pytest -m live
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from scraper import fetch_trending_sounds  # noqa: E402


def _run(keyword, **kw):
    return fetch_trending_sounds(keyword, **kw)


def _dump(result, note=""):
    meta = result["meta"]
    print(f"\n--- {note} ---")
    print(json.dumps({k: v for k, v in meta.items() if k != "notes"}, indent=2))
    for s in result["sounds"]:
        vc = s["video_count"]
        print(f"  uses={vc if vc is not None else 'n/a':>9}  seen={s['sample_frequency']:<2} "
              f"{(s['title'] or '')[:40]!r} / {(s['artist'] or '')[:16]!r} {s['duration_seconds']}s")
        print(f"      {str(s['play_link'])[:96]}")


@pytest.mark.live
def test_live_sad_hashtag_mining():
    """The pivot's acceptance test: hashtag mining, ranked by a REAL usage count."""
    result = _run("sad", limit=10)
    sounds, meta = result["sounds"], result["meta"]
    _dump(result, "keyword='sad'")

    assert sounds, "expected at least one sound"
    assert len(sounds) <= 10

    # hashtag mining, not the generic Explore feed
    assert meta["source"] == "tiktok_hashtag_mining"
    assert meta["hashtag_resolution"] == "mapped"
    assert meta["hashtags_requested"] == ["sad", "sadmusic", "emotional"]
    assert meta["hashtags_sampled"], meta["sources_failed"]
    assert "hashtag:sad" in meta["sources"]
    assert meta["source_is_keyword_search"] is False

    # the whole point of the pivot: real usage counts
    assert meta["usage_counts_available"] is True
    assert meta["sounds_enriched"] > 0, "enrichment produced nothing"
    assert meta["signed_surface"] == "ok"

    for s in sounds:
        assert s["play_link"].startswith("https://")
        assert s["id"]
        assert s["duration_seconds"] is not None

    # ranking: known counts first, descending; unknown last
    counts = [s["video_count"] for s in sounds]
    known = [c for c in counts if c is not None]
    assert known, "no sound came back with a real usage count"
    assert known == sorted(known, reverse=True), known
    first_unknown = next((i for i, c in enumerate(counts) if c is None), len(counts))
    assert all(c is None for c in counts[first_unknown:]), "unknown counts must sort last"

    print(f"\nOK: {len(sounds)} sounds, {len(known)} with real counts, "
          f"top={known[0]}, in {meta['elapsed_seconds']}s")


@pytest.mark.live
def test_live_explicit_hashtags():
    result = _run("ignored", limit=5, hashtag="gymtok,workout")
    meta = result["meta"]
    assert meta["hashtag_resolution"] == "explicit"
    assert "hashtag:gymtok" in meta["sources"]
    assert result["sounds"]
    print(f"\nOK explicit hashtags: {len(result['sounds'])} sounds, sources={meta['sources']}")


@pytest.mark.live
def test_live_unmapped_keyword_derives_and_still_returns():
    result = _run("zzqxniche9182", limit=5)
    meta = result["meta"]
    assert meta["hashtag_resolution"] == "derived"
    # a nonexistent hashtag must be reported, not fatal; the trending feed saves it
    assert meta["sources"], meta["sources_failed"]
    assert result["sounds"]
    print(f"\nOK unmapped keyword: sources={meta['sources']}, "
          f"failed={[f['source'] for f in meta['sources_failed']]}")


@pytest.mark.live
def test_live_ids_differ_from_a_different_niche():
    """The pivot's own acceptance test: 'sad' must not return the same sounds as
    another niche. Wholesale identity would mean hashtag mining is silently
    falling back to a generic feed."""
    sad = {s["id"] for s in _run("sad", limit=20)["sounds"]}
    gym = {s["id"] for s in _run("gym", limit=20)["sounds"]}
    overlap = sad & gym
    print(f"\nsad={len(sad)} gym={len(gym)} overlap={len(overlap)}")
    assert len(sad) > 3 and len(gym) > 3
    # some overlap is legitimate (a sound can trend in both niches); wholesale
    # identity is not.
    assert len(overlap) < min(len(sad), len(gym)), (
        f"nearly identical results -- hashtag mining is not differentiating: {overlap}")


if __name__ == "__main__":
    test_live_sad_hashtag_mining()
    test_live_explicit_hashtags()
    test_live_unmapped_keyword_derives_and_still_returns()
    test_live_ids_differ_from_a_different_niche()
    print("\nall live smoke checks passed")
