"""Manual smoke test: fetch a few real sources, extract, print tracks.

NOT a pytest module — it hits the live network. Run it directly:

    .venv/bin/python test_extract.py

Kept out of `pytest` collection by the `__main__` guard (and the name check in
`pytest.ini`), so `python -m pytest` stays fast and offline.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trend_scraper import extract_tracks  # noqa: E402
from trend_scraper.fetcher import HTTPFetcher  # noqa: E402
from trend_scraper.sources import CURRENT_TRENDING  # noqa: E402


def fetch_tracks(url, use_stealth=False, tag=""):
    http = HTTPFetcher()
    st, body = http.fetch(url)
    if not body or not (st and 200 <= st < 300):
        print(f"  ! {tag}{url} status={st}")
        return []
    ts = extract_tracks(body, source=tag or url)
    print(f"== {tag or url} ({len(ts)} tracks)")
    for t in ts[:12]:
        print("     ", t.display)
    return ts


def main() -> int:
    for src in CURRENT_TRENDING:
        fetch_tracks(src.url, use_stealth=src.use_stealth, tag=src.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())