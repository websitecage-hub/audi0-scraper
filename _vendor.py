"""Put the vendored TikTok scraper on sys.path.

`omkarcloud/tiktok-scraper` ships no `setup.py`/`pyproject.toml`, so
`pip install git+https://github.com/omkarcloud/tiktok-scraper.git` cannot work
(verified: the repo root has no packaging metadata). The minimal set of modules
this service actually needs is vendored under `vendor/` instead, pinned to a
known commit -- see `vendor/UPSTREAM.md`.

Import this module before importing anything from `tiktok`.
"""
import os
import sys

VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")

if VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)
