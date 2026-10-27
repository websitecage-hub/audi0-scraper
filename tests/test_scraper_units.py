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


# ---------------------------------------------------------------------------
# pool sampling: the same keyword must not return the same videos
# ---------------------------------------------------------------------------

def _video_n(i):
    return {"id": f"v{i}", "music": {"id": f"m{i}", "title": f"T{i}",
                                     "author_name": "A", "duration_seconds": 15,
                                     "play_link": f"https://cdn/{i}.mp3"}}


def _patch_sources(monkeypatch, n_hashtag=30, n_trending=20, tags=3):
    """Fake the two video sources and disable live audio-facet probing."""
    def fake_hashtag_videos(tag, deadline, country=None):
        return [_video_n(f"{tag}-{i}") for i in range(n_hashtag)]

    def fake_trending(deadline, country=None):
        return [_video_n(f"trend-{i}") for i in range(n_trending)]

    monkeypatch.setattr(scraper, "hashtag_videos", fake_hashtag_videos)
    monkeypatch.setattr(scraper, "trending_videos", fake_trending)
    monkeypatch.setattr(scraper, "music_details", lambda *a, **k: None)
    monkeypatch.setattr(scraper, "audio_facets", lambda *a, **k: [])


def test_pool_is_sampled_not_read_whole(monkeypatch):
    """The fix for 'the same songs every time': fetch a deep pool, read a
    random sample of it."""
    _patch_sources(monkeypatch)
    monkeypatch.setattr(scraper, "POOL_SAMPLE", 35)
    r = scraper.fetch_trending_sounds("sad", limit=20)
    m = r["meta"]
    assert m["videos_fetched"] == 3 * 30 + 20, "the whole pool must be fetched"
    assert m["videos_sampled"] == 35, "only the sample is read for sounds"
    assert m["pool_sample"] == 35


def test_pool_sampling_can_be_disabled(monkeypatch):
    _patch_sources(monkeypatch)
    monkeypatch.setattr(scraper, "POOL_SAMPLE", 0)
    m = scraper.fetch_trending_sounds("sad", limit=20)["meta"]
    assert m["videos_sampled"] == m["videos_fetched"] == 110
    assert m["pool_sample"] is None


def test_audio_facets_extend_the_pool_and_are_labelled(monkeypatch):
    """The semantic bridge: {kw}music/{kw}songs tags must be mined alongside the
    niche tags, attributed as their own source, and reported in meta."""
    _patch_sources(monkeypatch)

    def facets(kw, deadline, country=None, want=2):
        return [("sadmusic", 530_050), ("sadsongs", 907_855)][:want]

    monkeypatch.setattr(scraper, "audio_facets", facets)
    monkeypatch.setattr(scraper, "AUDIO_FACETS", 2)
    r = scraper.fetch_trending_sounds("sad", limit=50)
    m = r["meta"]
    labels = " ".join(m["sources"])
    assert "hashtag:sadmusic" in labels and "hashtag:sadsongs" in labels
    assert [f["tag"] for f in m["audio_facets"]] == ["sadmusic", "sadsongs"]
    # 3 niche tags + 2 facets, each 30, + trending 20
    assert m["videos_fetched"] == 5 * 30 + 20


def test_audio_facets_can_be_disabled(monkeypatch):
    _patch_sources(monkeypatch)
    monkeypatch.setattr(scraper, "AUDIO_FACETS", 0)
    m = scraper.fetch_trending_sounds("sad", limit=50)["meta"]
    assert m["audio_facets"] == []
    assert m["videos_fetched"] == 3 * 30 + 20


def test_explicit_hashtag_requests_skip_facets(monkeypatch):
    """A caller who names their tags has spoken; do not add ours."""
    _patch_sources(monkeypatch)
    called = []
    monkeypatch.setattr(scraper, "audio_facets",
                        lambda *a, **k: called.append(1) or [])
    m = scraper.fetch_trending_sounds("x", limit=50, hashtag="sad,sadmusic")["meta"]
    assert called == []
    assert m["audio_facets"] == []


def test_repeated_scrapes_return_a_different_mix(monkeypatch):
    """The user-visible outcome: two scrapes of the same keyword differ."""
    _patch_sources(monkeypatch)
    monkeypatch.setattr(scraper, "POOL_SAMPLE", 35)
    mixes = []
    for _ in range(3):
        mixes.append({s["id"] for s in scraper.fetch_trending_sounds("sad", limit=20)["sounds"]})
    assert len({frozenset(m) for m in mixes}) >= 2, \
        f"every scrape returned the same sounds: {mixes[0]}"
    # and each sample really is a subset of the pool
    assert all(m for m in mixes)


def test_sampling_preserves_source_attribution(monkeypatch):
    """A sampled video must keep its label, or is_niche would be wrong."""
    _patch_sources(monkeypatch)
    monkeypatch.setattr(scraper, "POOL_SAMPLE", 40)
    sounds = scraper.fetch_trending_sounds("sad", limit=40)["sounds"]
    for s in sounds:
        assert s["sources"], s["id"]
        assert s["is_niche"] == any(x.startswith("hashtag:") for x in s["sources"]), s


# ---------------------------------------------------------------------------
# suggestion-based expansion: any keyword must get a deep pool
# ---------------------------------------------------------------------------

def test_tagify_makes_a_hashtag_name():
    assert scraper._tagify("Anxiety Attacks") == "anxietyattacks"
    assert scraper._tagify("Anxiety  Songs!") == "anxietysongs"
    assert scraper._tagify("#Deep Feelings") == "deepfeelings"
    assert scraper._tagify("") == ""


def test_unmapped_keyword_is_expanded_with_suggestions(monkeypatch):
    """The general fix: an unmapped keyword must mine several hashtags, not one."""
    monkeypatch.setattr(scraper, "suggested_terms",
                        lambda kw, country=None, limit=12: ["Skateboarding Tricks",
                                                            "Skateboarding Songs",
                                                            "Skateboarding Falls"])
    monkeypatch.setattr(scraper, "hashtag_id", lambda tag, deadline, country=None:
                        (str(abs(hash(tag)) % 99999), {}))
    tags, how = scraper.expand_hashtags("skateboarding")
    assert how == "derived+suggested"
    assert tags[0] == "skateboarding", "the keyword's own tag stays first"
    assert "skateboardingtricks" in tags
    assert len(tags) <= scraper.HASHTAG_FANOUT, "the fanout is a cap, never exceeded"


def test_expansion_skips_tags_that_do_not_exist(monkeypatch):
    """A suggested phrase is not automatically a hashtag; non-existent ones are
    dropped rather than mined and reported as failures."""
    def only_tricks(tag, deadline, country=None):
        if tag != "skateboardingtricks":
            raise scraper.TikTokNotFound(f"#{tag} does not exist")
        return ("1", {})

    monkeypatch.setattr(scraper, "suggested_terms",
                        lambda kw, country=None, limit=12: ["Skateboarding Tricks",
                                                            "Not A Real Tag Here",
                                                            "Skateboarding Songs"])
    monkeypatch.setattr(scraper, "hashtag_id", only_tricks)
    tags, how = scraper.expand_hashtags("skateboarding")
    assert tags == ["skateboarding", "skateboardingtricks"]
    assert how == "derived+suggested"


def test_expansion_falls_back_when_suggestions_are_empty(monkeypatch):
    monkeypatch.setattr(scraper, "suggested_terms", lambda kw, country=None, limit=12: [])
    tags, how = scraper.expand_hashtags("zzqxniche")
    assert how == "derived"
    assert "zzqxniche" in tags


def test_mapped_keywords_do_not_call_the_suggestion_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(scraper, "suggested_terms",
                        lambda *a, **k: calls.append(1) or ["whatever"])
    tags, how = scraper.expand_hashtags("gym")
    assert how == "mapped"
    assert calls == [], "a curated mapping must not spend a suggestions call"


def test_expansion_can_be_disabled(monkeypatch):
    monkeypatch.setattr(scraper, "SUGGEST_TAGS", False)
    monkeypatch.setattr(scraper, "suggested_terms",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    tags, how = scraper.expand_hashtags("zzqxniche")
    assert how == "derived"


# ---------------------------------------------------------------------------
# stratified sampling: no hashtag may be squeezed out by chance
# ---------------------------------------------------------------------------

def test_sample_pool_is_stratified_across_sources():
    """A flat random sample of 35 from 110 can miss a whole hashtag; stratifying
    guarantees every working source contributes."""
    sources = [("hashtag:a", [{"id": f"a{i}"} for i in range(30)]),
               ("hashtag:b", [{"id": f"b{i}"} for i in range(30)]),
               ("hashtag:c", [{"id": f"c{i}"} for i in range(30)]),
               ("trending:DEFAULT", [{"id": f"t{i}"} for i in range(20)])]
    for _ in range(20):                      # repeat: sampling is random
        pool = scraper._sample_pool(sources, 35)
        assert len(pool) == 35
        labels = {label for label, _v in pool}
        assert labels == {"hashtag:a", "hashtag:b", "hashtag:c", "trending:DEFAULT"}, labels


def test_sample_pool_keeps_everything_when_pool_is_small():
    sources = [("hashtag:a", [{"id": "1"}, {"id": "2"}])]
    assert len(scraper._sample_pool(sources, 35)) == 2
    assert len(scraper._sample_pool(sources, 0)) == 2


def test_sample_pool_never_duplicates_a_video():
    sources = [("hashtag:a", [{"id": f"a{i}"} for i in range(50)]),
               ("hashtag:b", [{"id": f"b{i}"} for i in range(50)])]
    pool = scraper._sample_pool(sources, 35)
    ids = [v["id"] for _l, v in pool]
    assert len(ids) == len(set(ids)) == 35


# ---------------------------------------------------------------------------
# blended niche ranking: recurrence x log(global usage)
# ---------------------------------------------------------------------------

def _sound(sid, freq, vc, dur=60, original=False, commercial=None, plays=0):
    return {"id": sid, "title": f"t{sid}", "sample_frequency": freq,
            "video_count": vc, "duration_seconds": dur,
            "is_original_sound": original, "is_commercial_use_allowed": commercial,
            "sample_plays": plays, "is_niche": True}


def test_rank_score_is_frequency_times_log_usage():
    import math
    r = scraper._record({"id": "1", "title": "t", "play_link": "http://x/a.mp3",
                         "duration_seconds": 60, "video_count": 60000},
                        [{"stats": {}}] * 8, {"hashtag:gym"}, enriched=True)
    assert r is not None
    assert r["rank_score"] == round(8 * math.log10(60001), 3)


def test_niche_saturation_beats_global_popularity():
    """The whole point: a sound saturating the niche must outrank a global hit
    that merely appeared in it once."""
    niche = _sound("niche", freq=8, vc=60_000)
    global_hit = _sound("global", freq=1, vc=4_000_000)
    assert scraper._rank_score(niche) > scraper._rank_score(global_hit)


def test_rank_score_is_zero_without_measurable_terms():
    assert scraper._rank_score(_sound("a", freq=0, vc=1_000)) == 0.0
    assert scraper._rank_score(_sound("b", freq=5, vc=None)) == 0.0


def test_blended_rank_orders_by_score_not_raw_usage(monkeypatch):
    monkeypatch.setattr(scraper, "BLENDED_RANK", True)
    monkeypatch.setattr(scraper, "NICHE_PRIORITY", True)
    records = [_sound("global", freq=1, vc=4_000_000),
               _sound("niche", freq=8, vc=60_000)]
    for r in records:
        r["rank_score"] = scraper._rank_score(r)
    records.sort(key=scraper._order_key)
    assert [r["id"] for r in records] == ["niche", "global"]


def test_blended_rank_off_restores_raw_usage_order(monkeypatch):
    monkeypatch.setattr(scraper, "BLENDED_RANK", False)
    monkeypatch.setattr(scraper, "NICHE_PRIORITY", True)
    records = [_sound("global", freq=1, vc=4_000_000),
               _sound("niche", freq=8, vc=60_000)]
    for r in records:
        r["rank_score"] = scraper._rank_score(r)
    records.sort(key=scraper._order_key)
    assert [r["id"] for r in records] == ["global", "niche"]


def test_unenriched_sounds_sort_last_under_blended_rank(monkeypatch):
    """No usage count means no score, so it cannot outrank a measured sound."""
    monkeypatch.setattr(scraper, "BLENDED_RANK", True)
    records = [_sound("unknown", freq=9, vc=None), _sound("known", freq=1, vc=100)]
    for r in records:
        r["rank_score"] = scraper._rank_score(r)
    records.sort(key=scraper._order_key)
    assert [r["id"] for r in records] == ["known", "unknown"]


def test_every_record_carries_rank_score():
    r = scraper._record({"id": "1", "title": "t", "play_link": "http://x/a.mp3",
                         "duration_seconds": 30, "video_count": 500},
                        [{"stats": {}}], {"hashtag:gym"}, enriched=True)
    assert r is not None
    assert "rank_score" in r and r["rank_score"] == scraper._rank_score(r)


# ---------------------------------------------------------------------------
# music-first ordering: titled songs above original-sound clips
# ---------------------------------------------------------------------------

def test_music_first_puts_titled_songs_above_original_clips(monkeypatch):
    monkeypatch.setattr(scraper, "MUSIC_FIRST", True)
    monkeypatch.setattr(scraper, "BLENDED_RANK", True)
    monkeypatch.setattr(scraper, "NICHE_PRIORITY", True)
    music = _sound("song", freq=1, vc=5000)
    clip = _sound("clip", freq=3, vc=50000)
    music["is_original_sound"] = False
    clip["is_original_sound"] = True
    for r in (music, clip):
        r["rank_score"] = scraper._rank_score(r)
    assert scraper._order_key(music) < scraper._order_key(clip), \
        "a titled song must outrank a more-used original-sound clip"


def test_music_first_off_restores_score_order(monkeypatch):
    monkeypatch.setattr(scraper, "MUSIC_FIRST", False)
    monkeypatch.setattr(scraper, "BLENDED_RANK", True)
    music = _sound("song", freq=1, vc=5000)
    clip = _sound("clip", freq=3, vc=50000)
    music["is_original_sound"] = False
    clip["is_original_sound"] = True
    for r in (music, clip):
        r["rank_score"] = scraper._rank_score(r)
    assert scraper._order_key(clip) < scraper._order_key(music)


def test_audio_facet_probing_is_bounded_and_sorted(monkeypatch):
    """Facets must come back ranked by real video_count, capped at `want`."""
    def fake_id(tag, deadline, country=None):
        counts = {"sadsongs": 907855, "sadmusic": 530050, "sadvibes": 16364114,
                  "sadaudio": 96546, "sadphonk": None}   # None = does not exist
        if tag not in counts or counts[tag] is None:
            raise scraper.TikTokNotFound(tag)
        return "1", {"video_count": counts[tag]}

    monkeypatch.setattr(scraper, "hashtag_id", fake_id)
    out = scraper.audio_facets("sad", deadline=scraper.time.monotonic() + 30, want=2)
    assert [t for t, _vc in out] == ["sadvibes", "sadsongs"], \
        "ranked by video_count, non-existent tags dropped"


def test_audio_facets_skipped_for_garbage_keywords():
    assert scraper.audio_facets("!!!", deadline=0) == []
    assert scraper.audio_facets("x" * 60, deadline=0) == []


def test_per_hashtag_count_is_capped_at_the_verified_ceiling():
    """count=50 is rejected by TikTok (TikTokBadRequest); never allow it."""
    assert scraper.VIDEOS_PER_HASHTAG <= scraper.MAX_VIDEOS_PER_HASHTAG == 30


# ---------------------------------------------------------------------------
# pool depth: pagination + the depth/variety trade-off
# ---------------------------------------------------------------------------

def test_hashtag_videos_walks_the_cursor(monkeypatch):
    """Two pages of 30 = 60 videos, following upstream's cursor + hasMore."""
    calls = []

    def fake_call(path, params, label=None, deadline=None, country=None):
        calls.append(params["cursor"])
        cursor = params["cursor"]
        if cursor == 0:
            return {"itemList": [{"id": f"p1-{i}"} for i in range(30)],
                    "hasMore": True, "cursor": "30"}
        return {"itemList": [{"id": f"p2-{i}"} for i in range(30)],
                "hasMore": False, "cursor": "60"}

    monkeypatch.setattr(scraper, "hashtag_id", lambda t, d, c=None: ("1", {}))
    monkeypatch.setattr(scraper, "_call", fake_call)
    monkeypatch.setattr(scraper, "HASHTAG_PAGES", 2)
    monkeypatch.setattr(scraper.P, "videos",
                        lambda items: items or [])
    videos = scraper.hashtag_videos("gymtok", deadline=scraper.time.monotonic() + 30)
    assert len(videos) == 60
    assert calls == [0, 30], f"second page must resume from upstream's cursor, got {calls}"


def test_hashtag_videos_stops_when_upstream_says_no_more(monkeypatch):
    def fake_call(path, params, label=None, deadline=None, country=None):
        return {"itemList": [{"id": f"v{i}"} for i in range(30)],
                "hasMore": False, "cursor": "30"}

    monkeypatch.setattr(scraper, "hashtag_id", lambda t, d, c=None: ("1", {}))
    monkeypatch.setattr(scraper, "_call", fake_call)
    monkeypatch.setattr(scraper, "HASHTAG_PAGES", 3)
    monkeypatch.setattr(scraper.P, "videos", lambda items: items or [])
    videos = scraper.hashtag_videos("gymtok", deadline=scraper.time.monotonic() + 30)
    assert len(videos) == 30


def test_hashtag_videos_stops_before_the_deadline(monkeypatch):
    """Depth must never cost the latency bound: no page starts once the scrape
    deadline is within 2 seconds."""
    def fake_call(path, params, label=None, deadline=None, country=None):
        return {"itemList": [{"id": f"v{i}"} for i in range(30)],
                "hasMore": True, "cursor": "30"}

    monkeypatch.setattr(scraper, "hashtag_id", lambda t, d, c=None: ("1", {}))
    monkeypatch.setattr(scraper, "_call", fake_call)
    monkeypatch.setattr(scraper, "HASHTAG_PAGES", 5)
    monkeypatch.setattr(scraper.P, "videos", lambda items: items or [])
    videos = scraper.hashtag_videos("gymtok", deadline=scraper.time.monotonic() + 1)
    assert len(videos) == 30, "page 2 must not start inside the deadline guard"


def test_sample_of_a_deep_pool_stays_a_minority_fraction():
    """The variety contract: sampling ~90 of ~320 (not the whole pool) is what
    keeps repeat-search overlap low. Guard the RATIO, not the absolute number,
    so tuning one knob cannot silently break the other."""
    deep_pool = [{"id": f"v{i}"} for i in range(320)]
    for _ in range(10):
        pool = scraper._sample_pool([("hashtag:gymtok", deep_pool)], scraper.POOL_SAMPLE)
        assert len(pool) == scraper.POOL_SAMPLE
        assert len(pool) / len(deep_pool) <= 0.5


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
    ("anxiety", "mentalhealth"),
    ("overthinking", "anxiety"),
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
