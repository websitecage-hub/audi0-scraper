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

    # ranking: within each tier (niche first when NICHE_PRIORITY is on), known
    # counts descend; unknowns sort last within their tier
    tiers: list[list] = []
    cur: list = []
    cur_tier = None
    for s in sounds:
        tier = 0 if s.get("is_niche", True) else 1
        if cur and tier != cur_tier:
            tiers.append(cur)
            cur = []
        cur_tier = tier
        cur.append(s["video_count"])
    if cur:
        tiers.append(cur)

    known_any = False
    total_known = 0
    top_known = None
    for tier in tiers:
        known = [c for c in tier if c is not None]
        if known:
            known_any = True
            total_known += len(known)
            top_known = known[0]
            assert known == sorted(known, reverse=True), known
        first_unknown = next((i for i, c in enumerate(tier) if c is None), len(tier))
        assert all(c is None for c in tier[first_unknown:]), "unknown counts must sort last in their tier"
    assert known_any, "no sound came back with a real usage count"

    print(f"\nOK: {len(sounds)} sounds, {total_known} with real counts, "
          f"top={top_known}, in {meta['elapsed_seconds']}s")


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


@pytest.mark.live
def test_live_fresh_scrapes_return_different_ids():
    """The reported problem: the same keyword returned an identical list for
    600s. Two forced scrapes must return a different mix, because each reads a
    random sample of the deeper pool."""
    first = _run("anxiety", limit=20, fresh=True)
    second = _run("anxiety", limit=20, fresh=True)
    ids1 = {s["id"] for s in first["sounds"]}
    ids2 = {s["id"] for s in second["sounds"]}
    m1, m2 = first["meta"], second["meta"]
    print(f"\nfresh scrape 1: pool={m1.get('videos_fetched')} sampled={m1.get('videos_sampled')} "
          f"ids={len(ids1)}")
    print(f"fresh scrape 2: pool={m2.get('videos_fetched')} sampled={m2.get('videos_sampled')} "
          f"ids={len(ids2)}")
    print(f"overlap={len(ids1 & ids2)} of {min(len(ids1), len(ids2))}")
    assert m1.get("fresh") is True and m2.get("fresh") is True
    assert m1.get("pool_sample"), "sampling must be on"
    assert len(ids1) > 3 and len(ids2) > 3
    assert ids1 != ids2, "two fresh scrapes returned an identical list"


if __name__ == "__main__":
    test_live_sad_hashtag_mining()
    test_live_explicit_hashtags()
    test_live_unmapped_keyword_derives_and_still_returns()
    test_live_ids_differ_from_a_different_niche()
    test_live_fresh_scrapes_return_different_ids()
    print("\nall live smoke checks passed")
