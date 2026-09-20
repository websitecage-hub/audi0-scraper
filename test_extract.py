"""Smoke test: fetch a few real sources, extract, print tracks (no niche scoring)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trend_scraper import extract_tracks
from trend_scraper.fetcher import HTTPFetcher, StealthFetcher
from trend_scraper.sources import CURRENT_TRENDING, EVERGREEN

http = HTTPFetcher()
stealth = StealthFetcher()

def fetch_tracks(url, use_stealth=False, tag=""):
    st, body = (stealth.fetch(url) if use_stealth else http.fetch(url))
    if not body or not (st and 200 <= st < 300):
        print(f"  ! {tag}{url} status={st}")
        return []
    ts = extract_tracks(body, source=tag or url)
    print(f"== {tag or url} ({len(ts)} tracks)")
    for t in ts[:12]:
        print("     ", t.display)
    return ts

for src in CURRENT_TRENDING:
    fetch_tracks(src.url, use_stealth=src.use_stealth, tag=src.name)
