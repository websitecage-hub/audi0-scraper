"""Known public aggregators of current trending Reels audio + their parsers.

These are the 'always-on' current-trending feeds. The engine fetches them and
reuses the generic extractor; this module only declares URLs and which fetch each
needs. More independent CURRENT feeds == stronger corroboration signal in the
trend score, which is the whole point: a track on three independent this-week
lists is really trending; one on a single blog is noise.

`kind` marks the feed family so the engine can weight momentum:
  current   -> this-week / live trending lists (momentum evidence)
  evergreen -> stable 'best of' lists (popularity, not momentum)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Source:
    name: str
    url: str
    use_stealth: bool = False   # some are behind JS/bot walls
    kind: str = "current"       # "current" | "evergreen"


# Current trending Reels audio (global, general niche). These are the momentum
# feeds: being listed here means the audio is hot *now*.
CURRENT_TRENDING: list[Source] = [
    Source("metricool", "https://metricool.com/trending-instagram-songs/", use_stealth=True),
    Source("vaizle", "https://insights.vaizle.com/trending-instagram-songs/",
           use_stealth=True),
    Source("scottsocial",
           "https://www.scottsocialmarketing.com/blog/"
           "trending-reels-audio-this-week-on-instagram"),
    Source("later", "https://later.com/blog/trending-instagram-songs/", use_stealth=True),
    Source("hootsuite", "https://www.hootsuite.com/research/social-trends",
           use_stealth=True),
    Source("sproutsocial", "https://sproutsocial.com/insights/instagram-trends/",
           use_stealth=True),
    Source("buffer", "https://buffer.com/resources/instagram-trends/", use_stealth=True),
    Source("socialpilot", "https://www.socialpilot.co/blog/trending-instagram-songs",
           use_stealth=True),
    Source("planoly", "https://www.planoly.com/blog/trending-instagram-songs/",
           use_stealth=True),
    Source("statusbrew", "https://statusbrew.com/insights/trending-instagram-songs/",
           use_stealth=True),
]

# Stable 'evergreen' lists: popular songs, fetched to widen coverage. Being here
# is popularity evidence, not momentum, so it does not feed the momentum signal.
EVERGREEN: list[Source] = [
    Source("hubpages-motivation",
           "discover.hubpages.com/entertainment/motivational-songs-for-ig-reels-stories",
           use_stealth=False, kind="evergreen"),
]


def all_sources() -> list[Source]:
    return list(CURRENT_TRENDING) + list(EVERGREEN)
