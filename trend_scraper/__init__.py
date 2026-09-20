# trend_scraper - a niche-aware Instagram Reels audio trend scraper.
#
# Built on Scrapling (stealth anti-bot browser + fast curl_cffi fetcher).
# Strategy: Instagram itself is login-walled and heavily anti-bot, so this
# system gathers trending audio from public, scrapable aggregator & content
# sources, runs a niche-targeted search pass, then niche-scores every track.

from .fetcher import HTTPFetcher, StealthFetcher
from .searcher import ddg_lite_search
from .extract import extract_tracks, Niche
from .engine import TrendingAudioEngine

__all__ = [
    "HTTPFetcher", "StealthFetcher",
    "ddg_lite_search",
    "extract_tracks", "Niche",
    "TrendingAudioEngine",
]
