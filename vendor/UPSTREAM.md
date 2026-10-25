# Vendored upstream code

These files are a **verbatim, unmodified subset** of
[`omkarcloud/tiktok-scraper`](https://github.com/omkarcloud/tiktok-scraper).

| | |
|---|---|
| Upstream repo | https://github.com/omkarcloud/tiktok-scraper |
| Commit | `6839593` (2026-10-23, "Fix AliExpress Scraper link") |
| License | MIT, Copyright (c) 2026 Chetan Jain — see `upstream-LICENSE` |
| Vendored | 2026-10-25 |

## Why vendored instead of `pip install`

Upstream ships **no `setup.py` and no `pyproject.toml`**, so
`pip install git+https://github.com/omkarcloud/tiktok-scraper.git` — the install
line this project was originally specced with — cannot work. The alternatives
were a build-time `git clone` (unpinned: upstream changes silently break the
deploy) or vendoring a pinned subset. This project vendors.

## What is included

```
config.py                 # env-driven config (PORT, TIKTOK_PROXY, ...)
scraper_errors.py         # shared failure taxonomy
tiktok/__init__.py
tiktok/fetch.py           # transport: curl_cffi impersonated TLS + signer
tiktok/signer.py          # X-Bogus / msToken / X-Dynosaur / X-Gnarly signing
tiktok/parsers.py         # raw upstream JSON -> clean dicts
tiktok/refs.py            # id/link normalisation
tiktok/shared.py          # shared page helpers used by discovery
tiktok/discovery.py       # music / hashtag / feed endpoints + EXPLORE_CATEGORIES
```

## What was deliberately left out

| Omitted | Reason |
|---|---|
| `routes.py`, `schema_fields.py`, `tiktok/schemas.py`, `run.py` | The Bottle HTTP API server. This service imports the library directly, so a second server process is pure overhead — and on a 512 MB free tier it is overhead that does not fit. |
| `tiktok/test_parsers.py`, `tiktok/fixtures/` | Upstream's own test suite and ~40 JSON fixtures. Not needed at runtime. |
| `tiktok/users.py`, `videos.py`, `live.py`, `ads.py` | Surfaces this API does not expose. |
| `requirements.txt`'s `bottle`, `cheroot`, `marshmallow` | Only needed by the omitted server/routes. Verified removable: the vendored subset imports and runs with **`curl_cffi` alone**. |

## Runtime dependency

The vendored subset needs `curl_cffi` (plus its transitive `cffi`, `certifi`).
Nothing else.

## Modifying it

Do not. Keep the subset byte-identical to upstream so the pinned commit stays
meaningful, and re-vendor deliberately (updating this file) if upstream's
signer breaks — which it eventually will, since it reproduces TikTok's
`webmssdk` signature in pure Python.
