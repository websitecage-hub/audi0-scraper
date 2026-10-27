# Probe results

Every claim in the README that sounds like a constraint — "this surface is
blocked", "this measurement is X" — was tested, not assumed. This file records
the tests, the raw observations, and the date, so a future session can tell a
genuine regression from a stale assumption.

**Probe date:** 2026-10-25
**Probe host:** non-residential (datacenter) egress — the same class of network
as Render. Dev interpreter 3.14.7; deploy target 3.11.
**Upstream:** `omkarcloud/tiktok-scraper` @ `6839593` (2026-10-23)

---

## CORRECTION — the first revision of this file was wrong

**It claimed TikTok's signed endpoints are blocked from a datacenter IP, and
that the global usage count was therefore unobtainable. Both claims were false.**

They came from a single probe (`probe_paths.py`) that passed `signed=False` for
**every** case — including one it *labelled* `"music/detail (signed)"`. The
signed path was never exercised, and the empty body it saw was the documented
signature refusal, not an IP block.

Re-tested with real signatures, from the same datacenter IP:

```
challenge/detail #sad            SIGNED -> id=5927               status=0
challenge/detail #gymtok         SIGNED -> id=1618938325795845    status=0
challenge/item_list #sad         SIGNED -> 20 videos, 453 KB      status=0
music/detail 7694217687444605702 SIGNED -> videoCount=1256, duration=15, playUrl ✅
music/detail 7697580291170372360 SIGNED -> videoCount=346
recommend/item_list              SIGNED -> 490 KB   (unsigned -> 18 B)
search/item/full                 SIGNED -> status_code 2483 "Please login"  (still dead)
```

| Old claim | Truth |
|---|---|
| `/api/music/detail/` unreachable → usage count unobtainable | **WRONG** — answers normally when signed; returns the real `videoCount` |
| `/api/recommend/item_list/` a dead 18-byte stub | **WRONG for signed calls** — 490 KB; the 18-byte stub was the unsigned call |
| `/api/challenge/*` (hashtags) unusable | **never actually tested** — works signed |
| `/api/search/item/full/` unusable | **STILL TRUE** — needs cookies; the boot hits the WAF |
| Unsigned Explore is the only usable surface | **FALSE** — it is the only usable *unsigned* surface |

Consequences now reflected in the code: the service mines hashtags, merges the
trending feed, and reports a real `video_count`. `usage_counts_available()` no
longer returns `False`. Sections 3 and 4 below are corrected inline; section 2
(search) stands.

The transferable lesson: a probe that varies the wrong parameter can produce a
confident, reproducible, *completely wrong* architectural constraint. The label
said "signed"; the argument said `signed=False`.

---

## 1. The scraper library exists, and is not pip-installable

| Claim | Verdict |
|---|---|
| `omkarcloud/tiktok-scraper` exists, free, open source | **TRUE** |
| Install via `pip install git+https://github.com/omkarcloud/tiktok-scraper.git` | **FALSE** |
| "41 endpoints" | **TRUE** — `routes.py` mounts exactly 41 |
| `/search/videos`, `/music/details`, `/music/videos` exist | **TRUE** |
| Field names `play_link`, `author_name`, `duration_seconds`, `video_count` | **TRUE** |
| No browser automation | **TRUE** — `requirements.txt` is `bottle, cheroot, curl_cffi, marshmallow, pytest`. No botasaurus/selenium/playwright. Transport is `curl_cffi` with impersonated TLS. |
| No proxies required | **TRUE but conditional** — `TIKTOK_PROXY` defaults to unset and everything runs direct. |

```
$ ls tiktok-scraper/ | head
config.py  docker-compose.yaml  Dockerfile  main.py  README.md
requirements.txt  routes.py  run.py  schema_fields.py  scraper_errors.py  tiktok/

$ ls tiktok-scraper/ | grep -E "pyproject|setup"
(none)          <-- no packaging metadata => the VCS pip install line cannot work
```

Consequence: the dependency was **vendored** (9 files, 2993 lines) rather than
pip-installed. See `vendor/UPSTREAM.md`.

---

## 2. The keyword-search path is unreachable from a datacenter IP

### 2a. Search requires a booted session; the boot is WAF-blocked

```
$ python probe_live.py
TIKTOK_USE_PROXY = False
TIKTOK_PROXY     = None
----------------------------------------------------------------------
[search_videos] FAILED after 0.5s: TikTokBlocked: /@tiktok came back without page data
```

The failure is in `_load_page("https://www.tiktok.com/@tiktok")` — the cookie
bootstrap that search depends on. What the page load actually returns:

```
$ python probe_page.py
URL: https://www.tiktok.com/@tiktok
  status: 200 | final url: https://www.tiktok.com/@tiktok
  content-type: text/html
  body bytes: 1462
  cookies: []                                  <-- nothing issued
  contains '__UNIVERSAL_DATA_FOR_REHYDRATION__': False
  first 400 chars: <!DOCTYPE html><html lang="en"><head><link rel="icon"
    href="data:;base64,=" /><script id="slardar-config"
    type="application/json">{"slardarClient":"SlardarWAF", ... "bid":"slardar_us_waf" ...}
```

HTTP **200** with a 1462-byte **SlardarWAF** JavaScript challenge, zero cookies,
no rehydration payload. Not a UA problem — `curl_cffi` was impersonating Chrome.
It is an IP/ASN-class bot wall, and solving it requires executing the challenge
JS — i.e. a browser, which is the one thing this project forbids (and which does
not fit 512 MB anyway).

### 2b. The spec's "fallback" is itself blocked

The spec proposed falling back to a raw request to
`https://www.tiktok.com/api/search/general/full/` with a mobile UA and parsing
the JSON manually. Measured:

```
[unsigned search/general/full] TikTokBlocked: /api/search/general/full/ answered with an empty body
```

HTTP 200 with a **0-byte body**. TikTok only answers that endpoint when the
query carries a valid `webmssdk` signature (`X-Bogus`, `msToken`, `X-Dynosaur`,
`X-Gnarly` — reproduced in `tiktok/signer.py`). An unsigned call is silently
refused, by design. This is not a fallback; it is a second failure path.

---

## 3. Which surfaces actually answer (unsigned, from a datacenter IP)

```
$ python probe_paths.py
[search/preview (unsigned, no boot)] OK 0.5s  4712 bytes  sug_list: 8 rows
[explore (unsigned)]                 OK 0.7s  679239 bytes  itemList: 29 rows
[recommend/trending]                 OK 0.2s  18 bytes   top-keys=['status_code']
[music/detail (signed)]              TikTokBlocked: answered with an empty body
```

| Endpoint | Result | Meaning |
|---|---|---|
| `/api/explore/item_list/` | **works**, ~680 KB, ~30 videos | the only usable **unsigned** surface |
| `/api/search/general/preview/` | works, 8 suggestions | keyword expansion only |
| `/api/recommend/item_list/` | 18-byte stub **when unsigned** | signed it returns 490 KB (see CORRECTION) |
| `/api/music/detail/` | empty body **when unsigned** | signed it returns the real usage count |

**This table was the source of the error corrected above.** Every row was
produced with `signed=False`, so the last two rows were never a test of those
endpoints at all — they were a test of what TikTok does with an unsigned
request. The signed behaviour is in the CORRECTION section and section 9.

---

## 4. What the Explore feed gives you per video

```
FULL raw music object keys: ['authorName','coverLarge','coverMedium','coverThumb',
  'duration','id','isCopyrighted','is_commerce_music','is_unlimited_music',
  'original','playUrl','private','shoot_duration','title','tt2dsp']
```

`playUrl` is a direct, absolute audio URL. There is **no `stats`/`videoCount`
key** in this object — the global usage count is simply not present on the
unsigned surface, which is why `/v1/trending` returns `video_count: null`
instead of fabricating `0`.

Parsed by the library's own `P.music()`:

```json
{"id":"7696165884869774100","title":"原聲 - 🖤","author_name":"🖤",
 "duration_seconds":16,"play_link":"https://v16m.tiktokcdn.com/...",
 "is_original_sound":true,"streaming_links":[
   {"platform":"apple_music","song_id":"1854824119"},
   {"platform":"spotify","song_id":"0Wey6kYwllTZvkvq0DZLVx"}]}
```

(No `video_count` key at all — it is only added when the caller passes `stats`,
which only `/music/details` provides.)

---

## 5. Cost profile (decides the free-tier fit)

```
$ python test_vendor.py
5 categories: ok=5 fail=0
unique sounds: 132
WALL 3.12s   CPU 0.264s   CPU/wall ratio 0.085
per-category: wall 0.62s  cpu 0.053s
```

**CPU/wall = 0.085–0.101**: the workload is ~90% network I/O. That is the single
most important number here — it means a 0.1 vCPU instance is not CPU-starved by
this workload, and it is why the design tolerates the slowest free CPU. Parsing
680 KB of JSON costs ~0.05 s of CPU per category.

Live server logs confirm the real thing:

```
2026-10-24 20:30:26,057 INFO api: scrape ok key=cars|-|-  sounds=50 in 0.71s
2026-10-24 20:30:26,134 INFO api: scrape ok key=dance|-|- sounds=50 in 0.80s
2026-10-24 20:30:28,640 INFO api: scrape ok key=yoga|-|-  sounds=50 in 0.87s
```

### Memory

Summed over the **whole process tree** (supervisor + worker), not one PID:

```
v1 (Explore feed)    idle 79 MB  ->  87 MB after 8 concurrent cold scrapes
v2 (hashtag mining)  idle 58 MB  -> 172 MB after 6 concurrent cold scrapes
```

Cap is 512 MB. v2 costs more because a scrape holds 30 enriched full music
objects plus every sampled video; `MAX_CONCURRENT_SCRAPES` is what multiplies it.

---

## 6. Reliability

```
=== 12 sequential calls ===
  -> ok=12 empty/failed=0 in 7.4s
```

No rate-limiting at that volume. The library's own notes warn that some
endpoints are retired per-IP after ~17–27 calls (it names `/api/post/item_list`,
not Explore), so the service still implements bounded exponential backoff.

### The `country` parameter is a no-op from a datacenter exit

```
country=None  items=10
country=us    items=10
country=gb    items=10
country=in    items=10
country=jp    items=10        (all identical CDN/region mix)
```

Accepted and forwarded as `region`, but the feed follows the **server's egress
IP**, not the requested value. `/v1/trending/countries` therefore reports
`regionalization_supported: false` rather than implying per-region accuracy.

---

## 7. The audio URL is real, playable audio

Verified by fetching two returned `play_link`s once (the service itself never
downloads audio) and walking the container:

```
video-derived audio (v16m)
  HTTP 200  content-type=video/mp4  bytes=146158
  starts with ID3 tag: True   (ID3v2, 281B, prefixed to the stream)

dedicated music file (sf16-ies-music)
  HTTP 200  content-type=audio/mp4  bytes=486008
  box tree:
    ftyp / moov / mvhd / trak / tkhd / edts / mdia / mdhd
      hdlr  -> handler = 'soun'        <-- SOUND track
      minf / smhd (sound media header) / stbl / stsd ...
```

The `hdlr == 'soun'` handler plus the `smhd` box is proof this is an
**audio-only** media file, not video. Range requests are supported
(`accept-ranges: bytes`, `content-range: bytes 0-255/376688`).

Note for clients: these are **signed CDN URLs that expire**. They are fine to
hand to a player immediately; they are not stable permalinks.

---

## 8. Deployment dependency check (Python 3.11)

Every pin installs from a wheel on 3.11 — nothing compiles from source:

| Package | Wheel on cp311 |
|---|---|
| `fastapi==0.162.0` | `py3-none-any` ✅ |
| `starlette==1.10.0` | `py3-none-any` ✅ (requires-python `>=3.11`) |
| `uvicorn==0.54.0` | `py3-none-any` ✅ |
| `pydantic==2.14.1` / `pydantic_core==2.50.1` | `cp311-manylinux_2_17_x86_64` ✅ |
| `curl_cffi==0.16.3` | `cp310-abi3-manylinux_2_17_x86_64` ✅ (abi3 ⇒ 3.10+) |

Dependency set verified minimal by uninstalling everything else and re-running
the scrape:

```
$ pip uninstall -y bottle cheroot marshmallow
--- run with ONLY curl_cffi ---
imported cleanly. categories: 21
5 categories: ok=5 fail=0        <-- bottle/cheroot/marshmallow are NOT needed
```

---

## 9. Hashtag mining and enrichment (the v2 design)

### 9a. Hashtag resolution works signed

```
challenge/detail #sad       -> id=5927               challenge/detail #workout  -> id=5243
challenge/detail #sadmusic  -> id=176342             challenge/detail #relationship -> id=14328
challenge/detail #gymtok    -> id=1618938325795845
```

Each is followed by `/api/challenge/item_list/` returning **20 videos**
(453–603 KB per hashtag).

Hashtag stats live in `statsV2`, not `stats` — read the wrong one and you get
zero:

```
#sad  stats  : {'videoCount': 0,            'viewCount': 1282600000000}
      statsV2: {'videoCount': '116611040',  'viewCount': '1282563196274'}
```

The library's `P.hashtag()` already prefers `statsV2`; a hand-rolled reader did
not, and reported 0 videos for a hashtag with 116 million.

### 9b. Not retired at the volumes this service uses

The library warns that some endpoints go silent after ~17–27 calls per exit IP.
Neither endpoint this build depends on does:

```
25 sequential /music/detail calls -> ok=25 fail=0, mean 0.19s, max 0.24s
   (real counts returned: 19500, 17500, 484800, 5337, 59100, ...)
12 sequential /challenge/item_list -> ok=12 fail=0, ~1.05s each
```

So enrichment at `TIKTOK_ENRICH_TOP_N=30` is safe; the cap is a latency budget,
not evasion.

### 9c. End-to-end cost

```
keyword='sad'  -> videos_sampled=80  sounds_considered=79  sounds_enriched=30
                  sounds_returned=10  elapsed=5.304s
                  sources=[hashtag:emotional, hashtag:sad, hashtag:sadmusic, trending:DEFAULT]
                  signed_surface=ok
keyword='gym'  -> elapsed=10.32s   (one hashtag hit a block and needed the
                                    backoff retry: "videos of #workout:
                                    TikTokBlocked -- retry 1/2 in 5.0s")
```

Worst observed 10.3 s stays inside the 25 s deadline, and it runs as a
BackgroundTask so the 30 s edge timeout is never in play.

### 9c-bis. A retry policy the logs forced a change to

Enrichment originally retried once, like every other call. Live logs showed it
burning the 5 s backoff on a **permanently** dead sound:

```
music 7639631545913952273: TikTokUpstreamError -- retry 1/1 in 5.0s
music/detail unavailable for 7639631545913952273: upstream status 10219
api: scrape ok key=food ... in 15.19s
```

`10219` is not in the library's `MUSIC_NOT_FOUND = (10203, 10218)`, so it was
classified retryable. Since enrichment is best-effort across ~30 sounds, a
retry is the wrong trade: a transient failure is better handled by the next
scrape ten minutes later. Set to `retries=0` and added `10219` to the
not-found set:

```
food  15.19s -> 6.50s   enriched=28/67  with_count=5  signed=ok
tech          10.27s    enriched=29/75  with_count=5  signed=ok
```

The hashtag and trending calls keep their backoff, because those are the
sources the result depends on.

### 9d. Real usage counts, ranked

```
 #     GLOBAL seen       plays  dur  title / artist
 1     524700    1   9,800,000  60s  Je te laisserai des mots / Patrick Watson
 2     484800    1   1,700,000  60s  Hero (Prod. by Primary) (Inst.) / Megoo
 3     215000    1     264,800 475s  sonido original / RONALDO♾
 4     213300    1  10,100,000  60s  Upside Down - Original CHIC Mix / Diana Ross
 5     207300    1     980,500  60s  lost souls prayer / nilöwh.
 ...
20        444    1     608,800 296s  KURANG SAYANG BY RZKY.FDL / Ikki_Team
```

Strictly descending, all real numbers. Compare the v1 Explore build, which could
only report `video_count: null` plus a fabricated `trend_score`.

### 9e. The pivot actually changed the results

Acceptance test — new `sad` ids vs the old Explore-feed runs still on disk:

```
NEW 'sad' run (hashtag mining)      : 6 ids  source=tiktok_hashtag_mining
OLD relationship run (Explore feed) : 6 ids  source=tiktok_explore_feed
OLD 'sad audios' run (Explore feed) : 6 ids

overlap NEW sad vs OLD relationship : []
overlap NEW sad vs OLD sad audios   : []
```

And across niches, `sad` vs `gym` (20 ids each) overlap by 3 — those three come
from `trending:DEFAULT`, which is merged into every query by design. Wholesale
identity would mean the hashtag path was silently falling back.

That difference is also audible: `"sad"` now returns 60-second full songs
(`Je te laisserai des mots`, `Upside Down`, `lost souls prayer`) instead of the
v1 feed's 9–23 s clips of `原聲` originals.

### 9f. The audio payloads differ by source

```
dedicated music file  -> HTTP 200  content-type=audio/mp4  codec=aac  972,265 B   60.0s
                      -> hdlr 'soun', smhd sound-media header, no video stream
video-derived         -> HTTP 200  content-type=video/mp4  codec=mp3         ...   475s
```

`/api/music/details` returns the **full sound file** (up to 475 s observed, and
`aac` inside `audio/mp4`), whereas the music object embedded in a video carries
that video's trimmed clip. So enrichment is what upgrades both the usage count
*and* the audio quality. Both decode cleanly; both are audio-only.

---

## 10. Pool depth, sampling, and the suggestion endpoint (v2.3)

### 10a. The per-hashtag count ceiling is 30

The pool expansion depends on how deep one hashtag call can go:

```
#anxiety challenge/item_list count=20 -> 20 videos, cursor='20', hasMore=True
                            count=30 -> 30 videos, cursor='30', hasMore=True
                            count=50 -> TikTokBadRequest: tiktok rejected /api/challenge/item_list/
```

So `count` is honoured up to 30 and **rejected** at 50 — `TIKTOK_VIDEOS_PER_HASHTAG`
is hard-capped at 30 in code, not left as a config the operator can raise into a
guaranteed failure. The payload does expose `cursor` + `hasMore`, so pagination is
possible; the design instead gets depth from more hashtags, which costs one
parallel call rather than a serial page walk. (#anxiety itself has 8,071,145 videos.)

### 10b. `/api/search/general/preview/` is the generic expansion source

Verified UNSIGNED, login-free, from a datacenter IP — which makes it the one
discovery surface that keeps working even if the signer rots:

```
GET /api/search/general/preview/?keyword=anxiety&count=10
  -> {"sug_list": [{"content": "Anxiety Attacks", ...},
                   {"content": "Anxiety Songs", ...}, ...]}
  -> P.suggestions(payload["sug_list"]) -> ['Anxiety Attacks', 'Anxiety Songs', ...]
```

Two traps, both hit during probing: the parameter is `keyword` (`keywords`
returns an empty `sug_list` rather than erroring), and the terms live under
`sug_list` (not `data` / `suggestions` / `sugList`). A wrong guess here fails
*silently* as "no suggestions".

Suggestion phrases are not hashtags, so each is tagified (`Anxiety Attacks` ->
`anxietyattacks`) and existence-checked through `challenge/detail` before use.
That check is ~0.19s and cached for an hour, so it is cheaper than mining a tag
that does not exist.

They are also **regional**: TikTok ranks them against the requesting IP, so from
an Asian egress `skateboarding` suggested `skateboardingtaiwan` and
`skateboardingphilippines`. That is consistent with the rest of the design
(content follows egress IP) but it is a real behaviour to know about.

### 10c. What it changed, measured

Before, only hand-mapped keywords got 3 hashtags; an unmapped word derived to
ONE, capping its pool near 50. Across seven keywords (mapped, unmapped,
multi-word) every one now mines 3 tags from a 110-video pool:

```
keyword                resolution           tags  pool sampled sounds enriched  secs
gym                    mapped                  3   110      35     34       30   6.0
anxiety                mapped                  3   110      35     34       30  11.2
skateboarding          derived+suggested       3   110      35     34       29  12.4
sourdough              derived+suggested       3   110      35     30       27  12.4
thrifting              derived+suggested       3   110      35     33       28   7.5
mechanical keyboard    derived                 3   110      35     33       29   6.0
bouldering             derived+suggested       3   110      35     32       30   7.9
```

`mechanical keyboard` is the honest counter-example: suggestions returned nothing
usable, it fell back to plain derivation — and still produced 3 tags from the
multi-word split, so the fallback is not a cliff.

### 10d. Sampling is stratified, not uniform

A flat random draw of 35 from 110 can miss an entire hashtag by chance, leaving a
facet of the niche invisible. `_sample_pool` allocates each source roughly its
share with a floor of one video, so every hashtag that returned videos is always
represented. Verified over 20 consecutive draws: all four sources present, 35
videos out, no duplicates.

### 10e. Fresh by default

`FRESH_BY_DEFAULT=1`: a request without `fresh` forces a new scrape rather than
replaying the cache, and the 202 body returns `poll_with` (the same URL with
`fresh=false`) so a client can always converge. Repeat searches of the same
keyword, measured:

```
thrifting  run1=20 ids  run2=20 ids  overlap=1   identical=False
```

One sound in common out of twenty, against an identical list before this change.

## 11. The semantic-bridge campaign (v3.0.0, 2026-10-28)

Question that started it: the audio a niche runs on is not named like the niche
(gym videos run on phonk and slowed R&B, not "gym music") -- can we find the
niche's music anyway? Eleven experiments, run in order, each deciding the next:

* **exp1 -- co-occurrence mining: DEAD END.** Mined 90 #gymtok/#gym/#workout
  videos for co-occurring audio hashtags (#phonk, #spedup...): found 2 across
  302 distinct tags, and 63/90 videos carried "original sound". The niche does
  not tag its audio.
* **exp2/exp2b -- niche-anchored audio tags: THE BRIDGE, but shapes matter.**
  {kw}music/{kw}songs/{kw}vibes exist as real communities -- #sadsongs has
  907,858 videos, #gymmusic 104,995, #sadvibes 16.4M -- and bring 85-95% sounds
  the plain niche tags never surface. (A first read said "100% non-original
  music" -- that was a probe bug reading the wrong field shape; the true
  original-sound share of #sadsongs is ~98%. Honest correction recorded here.)
* **exp3 -- suggestion second hop for song titles: DEAD END.** Suggestions are
  what people TYPE ("sad songs to cry to"), never song titles, and hop-2 drifts
  into unrelated languages and entities (sad -> sade -> sadevillagelombok).
* **exp4/4b -- observing a sound's usage context: BLOCKED.**
  /api/music/item_list/ answers TikTokBadRequest from this IP in every param
  shape tried; /api/music/videos/ answers "url doesn't match". Direct
  measurement of "is this sound used in this niche" is not available to us.
* **exp5 -- semantic fields in video/music payloads: NULL.**
  suggested_searches is always null; the music payload contributes `album`,
  `is_commerce_music`, `tt2dsp` (streaming links) -- metadata, not semantics.
* **exp6 (corrected shapes) -- facets add 106-120 new sounds per niche**, and
  their music skews TITLED (LAIDTEKK, HUNTER (Ultra Slowed), beamer boy) with
  median durations 22-33s vs 60s+ in niche tags.
* **exp7 -- sibling overlap.** #gymmusic vs #gymsongs share 21/30 videos;
  #sadvibes vs #sadsongs share 0/30. Conclusion: mine the 2 strongest facets
  by video_count, not several siblings.
* **exp9/9b -- facets alone can LOWER strict yield** (gym mean 14.5 -> 7.5 at
  facets=2): frequency ranking still buries titled music under the pool's
  original-sound majority. Facets change WHAT is in the pool; ranking decides
  whether it surfaces.
* **exp11 -- the ranking insight: MUSIC-FIRST.** Re-ranking the same pool with
  titled music first (structural `is_original_sound` flag -- never title
  prefixes, because "original sound" is spelled nha.c ne^`n / suara asli /
  صوت أصلي / 原聲 / son original / оригинальный звук ...) tripled the strict
  yield on every niche: gym 11->28, sad 3->11, coding 5->22, wedding 6->19,
  cooking 8->24.

### Shipped as v3.0.0

`audio_facets()` probes {kw}{songs,music,vibes,audio,phonk,playlist} (parallel
existence checks, ranked by real video_count, top 2 mined alongside the niche
tags; skipped for explicit `?hashtag=` requests); `_order_key` gains the
music-first tier inside the niche tier; `meta.audio_facets`,
`meta.music_first`, `health.music_first` expose all of it. Knobs:
`TIKTOK_AUDIO_FACETS` (2), `MUSIC_FIRST` (1).

### Live result (Render, v3.0.0, 2 runs per niche)

Strict combo `exclude_original=true&duration_min=30&duration_max=90`:

```
gym     18, 18    ('GYM (Ultra Slowed)' 249k uses, 'petal' 267k, 'BRUTALISM (Slowed)')
sad      9,  9    ('August 10' 173k, 'Little Things' 10.5M, 'Harvey' 119k)
coding  14,  8    ('Tonight' 240k, 'Paper Moon Static' 929k, 'Island In The Sun' 650k)
```

All six runs >= 5; scrapes 10.6-15.9s against the 45s background deadline;
the HTTP contract is unchanged (202 -> poll with fresh=false).

### Rejected ideas, with reasons (so future-us does not retry them)

* Suggestion second-hop: drift + zero song titles (exp3).
* music->videos context observation: endpoint blocked (exp4).
* Co-occurrence vocabulary: nothing there (exp1).
* Reading the whole pool: destroys repeat-search variety (measured 13-17/20
  overlap in the v2.4 campaign).
* Title-prefix "original sound" detection: incomplete in every language;
  the structural flag exists and is exact.

## Known unknowns

* **The signer is the fragile part.** `tiktok/signer.py` reimplements TikTok's
  `webmssdk` signature in pure Python, and the v2 design now depends on signed
  endpoints for its core path. When TikTok rotates the signature, hashtag
  mining and enrichment break together. The Explore fallback is kept precisely
  because it needs no signature, and `meta.signed_surface` flips to `degraded`
  so the failure is visible rather than silent.
* **The upstream surface may change without notice.** If `/api/challenge/*` or
  `/api/music/detail/` starts requiring a login, the correct response is to
  re-vendor a newer commit, not to add a browser.
* **Probed from one datacenter ASN.** A different host may be treated
  differently, though all datacenter ranges are challenged in practice.
* **Keyword search remains unavailable** and probably always will from a
  datacenter IP: it needs a cookie session, and the boot is WAF-gated. Hashtag
  mining is the workaround, not a fix for that.
* **The keyword→hashtag map is curated, so it only covers what it covers.** An
  unmapped keyword is now expanded with suggestions rather than derived blindly,
  but if suggestions come back empty or unusable it falls back to a single
  derived tag (observed once, for `mechanical keyboard`, which still produced
  three tags from its multi-word split). What is mined either way is reported in
  `meta`, and a tag that yields nothing lands in `meta.sources_failed`.
* **Suggestions are regional** (section 10b): they reflect what is popular from
  this server's IP, so the tags an unmapped keyword fans out to differ between a
  US and an Asian egress. Same rule as the content itself, but it means two
  deployments in different regions can return different hashtag sets.
* **Fresh-by-default costs a scrape per search.** Every `/v1/trending` call
  without `fresh=false` spends ~5–15s and ~5 upstream calls, where the cache
  would have answered instantly. That is the deliberate trade for always-current
  results; `FRESH_BY_DEFAULT=0` restores cache-first behaviour, and the polling
  contract is unaffected either way.
