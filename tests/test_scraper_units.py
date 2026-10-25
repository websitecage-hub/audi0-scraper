"""Offline unit tests for the scraper's parsing / ranking / mapping logic.

These run with no network: they exercise the pure functions that decide what a
client actually receives.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scraper  # noqa: E402


def _music(**over):
    """Shape of a parsed music object. When it comes from /api/music/details
    (via P.music with stats passed) it also carries `video_count`."""
    base = {
        "id": "123",
        "title": "Some Sound",
        "author_name": "Some Artist",
        "duration_seconds": 15,
        "play_link": "https://v16m.tiktokcdn.com/a.mp3",
        "cover": "https://p16.tiktokcdn.com/c.jpg",
        "link": "https://www.tiktok.com/music/Some-Sound-123",
        "is_original_sound": False,
        "is_commercial_use_allowed": True,
        "is_copyrighted": True,
        "streaming_links": [],
    }
    base.update(over)
    return base


def _video(plays=100, likes=10):
    return {"stats": {"play_count": plays, "like_count": likes}}


# ---------------------------------------------------------------------------
# URL normalisation (spec: relative play_link must be made absolute)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://v16m.tiktokcdn.com/a.mp3", "https://v16m.tiktokcdn.com/a.mp3"),
    ("http://cdn.example/a.mp3", "http://cdn.example/a.mp3"),
    ("//v16m.tiktokcdn.com/a.mp3", "https://v16m.tiktokcdn.com/a.mp3"),
    ("/aweme/v1/play/?id=1", "https://www.tiktok.com/aweme/v1/play/?id=1"),
    ("aweme/v1/play/?id=1", "https://www.tiktok.com/aweme/v1/play/?id=1"),
    ("", None),
    (None, None),
    ("   ", None),
])
def test_absolute_url(raw, expected):
    assert scraper._absolute_url(raw) == expected


# ---------------------------------------------------------------------------
# record shape: the real usage count
# ---------------------------------------------------------------------------

def test_enriched_record_carries_the_real_usage_count():
    """The pivot's core claim: video_count is a real number from
    /api/music/details, not a null placeholder."""
    rec = scraper._record(_music(video_count=1256), [_video(), _video()],
                          {"hashtag:sad"}, enriched=True)
    assert rec["video_count"] == 1256
    assert rec["usage_basis"] == "upstream_music_details"
    assert rec["sample_frequency"] == 2
    assert rec["sample_plays"] == 200
    assert rec["sample_likes"] == 20
    assert rec["duration_seconds"] == 15
    assert rec["id"] == "123"
    assert rec["title"] == "Some Sound"
    assert rec["artist"] == "Some Artist"
    assert rec["sources"] == ["hashtag:sad"]


def test_usage_count_is_null_never_zero_when_enrichment_failed():
    """null, never 0: 0 is indistinguishable from a real zero and would sort a
    huge sound to the bottom."""
    rec = scraper._record(_music(), [_video()], {"hashtag:sad"}, enriched=True)
    assert rec["video_count"] is None
    assert rec["usage_basis"] == "unavailable_enrichment_failed"


def test_unenriched_record_says_so():
    rec = scraper._record(_music(), [_video()], {"trending:US"}, enriched=False)
    assert rec["video_count"] is None
    assert rec["usage_basis"] == "not_enriched"


def test_record_flattens_relative_play_link():
    rec = scraper._record(_music(play_link="/aweme/v1/play/?id=9"), [_video()],
                          {"hashtag:sad"}, enriched=False)
    assert rec["play_link"] == "https://www.tiktok.com/aweme/v1/play/?id=9"


def test_record_drops_sounds_without_audio_url():
    assert scraper._record(_music(play_link=None), [_video()], {"x"}, False) is None
    assert scraper._record(_music(play_link=""), [_video()], {"x"}, False) is None


def test_record_tolerates_missing_stats():
    rec = scraper._record(_music(), [{}], {"x"}, enriched=False)
    assert rec["sample_plays"] == 0 and rec["sample_likes"] == 0
    assert rec["sample_frequency"] == 1


def test_record_tolerates_missing_duration():
    rec = scraper._record(_music(duration_seconds=None), [_video()], {"x"}, enriched=False)
    assert rec["duration_seconds"] is None


# ---------------------------------------------------------------------------
# ranking: real usage count first
# ---------------------------------------------------------------------------

def _niche(**over):
    return scraper._record(_music(**over), [_video()], {"hashtag:sad"}, True)


def _generic(**over):
    return scraper._record(_music(**over), [_video()], {"trending:US"}, True)

def test_ranking_puts_known_usage_counts_first_descending():
    big = scraper._record(_music(id="big", title="B", video_count=90000), [_video()], {"x"}, True)
    mid = scraper._record(_music(id="mid", title="M", video_count=500), [_video()], {"x"}, True)
    unknown = scraper._record(_music(id="unk", title="U", video_count=None), [_video()], {"x"}, True)
    ranked = sorted([mid, unknown, big], key=scraper._order_key)
    assert [r["id"] for r in ranked] == ["big", "mid", "unk"]


# ---------------------------------------------------------------------------
# is_niche + NICHE_PRIORITY ranking
# ---------------------------------------------------------------------------

def test_record_marks_niche_sounds():
    niche = _niche(id="n")
    generic = _generic(id="g")
    both = scraper._record(_music(id="b"), [_video()],
                           {"hashtag:sad", "trending:US"}, True)
    assert niche["is_niche"] is True
    assert generic["is_niche"] is False
    assert both["is_niche"] is True, "a hashtag source makes it niche even if trending also carried it"


def test_niche_priority_puts_a_small_niche_sound_above_a_big_generic_one():
    """The reason this knob exists: a niche sound with 5,000 uses must outrank a
    generic trending sound with 200,000 uses."""
    big_generic = _generic(id="big", video_count=200_000)
    small_niche = _niche(id="small", video_count=5_000)
    ranked = sorted([big_generic, small_niche], key=scraper._order_key)
    assert [r["id"] for r in ranked] == ["small", "big"]


def test_niche_priority_off_ranks_purely_by_usage_count(monkeypatch):
    monkeypatch.setattr(scraper, "NICHE_PRIORITY", False)
    big_generic = _generic(id="big", video_count=200_000)
    small_niche = _niche(id="small", video_count=5_000)
    ranked = sorted([big_generic, small_niche], key=scraper._order_key)
    assert [r["id"] for r in ranked] == ["big", "small"]


def test_all_niche_results_keep_pure_count_ordering():
    """Within the niche tier the order is still the real usage count."""
    a = _niche(id="a", video_count=10)
    b = _niche(id="b", video_count=1000)
    c = _niche(id="c", video_count=None)
    ranked = sorted([c, a, b], key=scraper._order_key)
    assert [r["id"] for r in ranked] == ["b", "a", "c"]


def test_unknown_usage_counts_sort_last_even_with_big_engagement():
    known = scraper._record(_music(id="known", video_count=10), [_video(1)], {"x"}, True)
    unknown = scraper._record(_music(id="unknown"), [_video(50_000_000)], {"x"}, True)
    ranked = sorted([unknown, known], key=scraper._order_key)
    assert [r["id"] for r in ranked] == ["known", "unknown"], \
        "a sound with no usage count must not outrank one with a real count"


def test_equal_counts_fall_back_to_in_sample_recurrence():
    one = scraper._record(_music(id="one", video_count=10), [_video()], {"x"}, True)
    three = scraper._record(_music(id="three", video_count=10),
                            [_video(), _video(), _video()], {"x"}, True)
    ranked = sorted([one, three], key=scraper._order_key)
    assert [r["id"] for r in ranked] == ["three", "one"]


# ---------------------------------------------------------------------------
# keyword -> hashtag mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("keyword,must_include", [
    ("sad", "sadmusic"),
    ("sadness", "sadmusic"),
    ("gym", "gymtok"),
    ("workout", "gymtok"),
    ("relationship", "couplegoals"),
    ("gaming", "gamingontiktok"),
    ("skincare", "skincareroutine"),
])
def test_mapping_hits_the_curated_table(keyword, must_include):
    tags, how = scraper.resolve_hashtags(keyword)
    assert how == "mapped"
    assert must_include in tags


def test_mapping_fans_out_to_at_most_three_tags():
    tags, how = scraper.resolve_hashtags("sad")
    assert how == "mapped"
    assert len(tags) == 3
    assert tags == ["sad", "sadmusic", "emotional"]


def test_token_overlap_still_finds_the_mapping():
    tags, how = scraper.resolve_hashtags("gym motivation playlist")
    assert how == "mapped"
    assert "gymtok" in tags


def test_unmapped_keyword_is_derived_not_silently_dropped():
    tags, how = scraper.resolve_hashtags("zzqxniche")
    assert how == "derived"
    assert "zzqxniche" in tags


def test_derived_strips_stopwords_but_keeps_a_usable_tag():
    tags, how = scraper.resolve_hashtags("sounds of the ocean")
    assert how == "derived"
    assert "ocean" in tags
    assert not any(t in ("sounds", "the", "of") for t in tags)


def test_blank_keyword_still_yields_a_tag():
    tags, how = scraper.resolve_hashtags("   ")
    assert tags and how == "empty-keyword"


def test_explicit_hashtags_bypass_the_mapping():
    tags, how = scraper.resolve_hashtags("sad", explicit="#SadMusic, sad")
    assert how == "explicit"
    assert tags == ["sadmusic", "sad"], "must de-dupe and strip the leading #"


def test_explicit_hashtags_respect_fanout():
    tags, _ = scraper.resolve_hashtags("sad", explicit="a,b,c,d,e", fanout=2)
    assert tags == ["a", "b"]


def test_catalog_exposes_the_curated_map():
    cat = scraper.hashtag_catalog()
    assert cat["sad"] == ["sad", "sadmusic", "emotional"]
    assert cat["relationship"] == ["relationship", "couplegoals", "dating"]
    # no keyword may map to duplicate tags
    for kw, tags in cat.items():
        assert len(tags) == len(set(tags)), kw


# ---------------------------------------------------------------------------
# capability + config
# ---------------------------------------------------------------------------

def test_usage_counts_are_reported_available():
    """Regression guard on the pivot's corrected claim. The old build returned
    False here because its probe never sent a signature."""
    assert scraper.usage_counts_available() is True


def test_supported_countries_are_unique_two_letter():
    countries = scraper.supported_countries()
    assert len(countries) == len(set(countries))
    assert all(len(c) == 2 and c.isupper() for c in countries)
    assert "US" in countries


def test_upstream_paths_are_the_hashtag_ones():
    """Guard against silently reverting to the Explore-only design."""
    assert scraper.HASHTAG_DETAIL_PATH == "/api/challenge/detail/"
    assert scraper.HASHTAG_VIDEOS_PATH == "/api/challenge/item_list/"
    assert scraper.MUSIC_DETAIL_PATH == "/api/music/detail/"
    assert scraper.TRENDING_PATH == "/api/recommend/item_list/"
