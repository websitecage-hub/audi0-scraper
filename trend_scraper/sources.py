"""Known public aggregators of current trending Reels audio + their parsers.

These are the 'always-on' current-trending feeds. The engine fetches them and
reuses the generic extractor; this module only declares the URLs and which type
of fetch each needs.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Source:
    name: str
    url: str
    use_stealth: bool = False   # some are behind JS/bot walls


# Current trending Reels audio (global, general niche).
CURRENT_TRENDING: list[Source] = [
    Source("metricool", "https://metricool.com/trending-instagram-songs/", use_stealth=True),
    Source("vaizle", "https://insights.vaizle.com/trending-instagram-songs/",
           use_stealth=True),
    Source("scottsocial",
           "https://www.scottsocialmarketing.com/blog/"
           "trending-reels-audio-this-week-on-instagram"),
]

# Stable 'evergreen' motivation lists (ratelimited/bot-walled -> fetch via Wayback).
EVERGREEN: list[Source] = [
    Source("hubpages-motivation",
           "discover.hubpages.com/entertainment/motivational-songs-for-ig-reels-stories",
           use_stealth=False),
]
