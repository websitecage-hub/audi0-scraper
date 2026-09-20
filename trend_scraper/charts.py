"""Chart backends — real, ranked, machine-readable music charts.

These are the *strongest* trend evidence in the system: a chart is an explicit,
ranked, currently-updated popularity list. Unlike scraping a blog, a chart gives
us a position, a genre, and (iTunes) a release date — so corroboration, rank and
momentum signals are grounded in real data instead of HTML guesswork.

Backends
--------
  iTunes RSS   most-played songs per storefront; genre tags + releaseDate.
               https://rss.applemarketingtools.com  (JSON, no auth)
  Deezer       global + per-genre charts with explicit genre ids.
               https://api.deezer.com/chart  (JSON, no auth)

Each entry is normalized to `ChartEntry` so the engine treats every backend the
same. `mood_genres` maps a niche to the genre charts worth pulling.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .fetcher import HTTPFetcher

log = logging.getLogger("trend.audio.charts")

_ITUNES_MOSTPLAYED = ("https://rss.applemarketingtools.com/api/v2/{cc}/music/"
                      "most-played/100/songs.json")
_DEEZER_CHART = "https://api.deezer.com/chart/{gid}/tracks?limit={n}"


@dataclass
class ChartEntry:
    title: str
    artist: str
    rank: int                    # 1-based chart position (real data)
    chart: str                   # backend/source name
    list_size: int               # how many entries the chart had
    genres: list[str] = field(default_factory=list)
    release_date: str = ""
    url: str = ""


# --- niche -> chart sources -------------------------------------------------
# Deezer genre ids (stable). Selecting a niche's genre charts is what makes a
# global chart answer a *niche* question: a gym edit wants rock/dance/electro,
# a love edit wants rnb/pop.
DEEZER_GENRES: dict[str, int] = {
    "pop": 132, "rap": 116, "rock": 152, "dance": 113, "rnb": 165,
    "electro": 106, "latin": 466, "indie": 85, "metal": 464, "jazz": 129,
    "classical": 98, "soul": 169, "folk": 466, "alternative": 152,
}

NICHE_GENRES: dict[str, list[str]] = {
    "gym": ["rock", "dance", "rap"],
    "motivation": ["rock", "pop", "dance"],
    "self-improvement": ["pop", "indie", "alternative"],
    "money": ["rap", "pop"],
    "rap": ["rap"],
    "party": ["dance", "electro", "latin"],
    "summer": ["pop", "latin", "dance"],
    "lofi": ["jazz", "soul", "alternative"],
    "love": ["rnb", "pop"],
    "sad": ["rnb", "indie", "alternative"],
    "cinematic": ["classical", "alternative"],
    "nature": ["indie", "folk", "alternative"],
    "fashion": ["pop", "electro"],
    "beauty": ["pop", "rnb"],
    "travel": ["pop", "indie", "latin"],
    "food": ["pop", "jazz"],
    "anime": ["pop", "alternative"],
    "gaming": ["electro", "dance", "rock"],
    "crypto": ["rap", "electro"],
}

# Default genre spread for an unknown niche — broad, still current.
DEFAULT_GENRES = ["pop", "rock", "dance", "rap"]


def genres_for_niche(niche_name: str) -> list[str]:
    return NICHE_GENRES.get(niche_name, DEFAULT_GENRES)


# --- backends ---------------------------------------------------------------

def fetch_itunes_charts(http: HTTPFetcher | None = None, *, country: str = "us",
                        limit: int = 100) -> list[ChartEntry]:
    """Apple 'most-played' songs — a real ranked chart with genre tags."""
    http = http or HTTPFetcher()
    url = _ITUNES_MOSTPLAYED.format(cc=country)
    status, body = http.fetch(url, retries=2)
    if not status or not (200 <= status < 300) or not body:
        log.warning("itunes chart unavailable (status=%s)", status)
        return []
    try:
        results = json.loads(body)["feed"]["results"]
    except Exception as exc:
        log.warning("itunes chart parse failed: %s", exc)
        return []
    out: list[ChartEntry] = []
    size = min(len(results), limit)
    for i, r in enumerate(results[:limit], 1):
        name = (r.get("name") or "").strip()
        artist = (r.get("artistName") or "").strip()
        if not name:
            continue
        out.append(ChartEntry(
            title=name, artist=artist, rank=i, chart="itunes-most-played",
            list_size=size, genres=_genre_names(r.get("genres", [])),
            release_date=(r.get("releaseDate") or "")[:10], url=r.get("url", ""),
        ))
    return out


def _genre_names(raw: list) -> list[str]:
    """iTunes returns genres as either strings or {'name': ...} dicts.

    Names are normalized onto the canonical keys used by NICHE_GENRES so that a
    real tag like "Hip-Hop/Rap" matches the niche key "rap".
    """
    out: list[str] = []
    for g in raw or []:
        if isinstance(g, dict):
            g = g.get("name", "")
        if isinstance(g, str) and g.strip():
            out.append(_canon_genre(g))
    return [g for g in out if g]


# iTunes/Deezer genre labels -> the canonical keys in NICHE_GENRES.
_GENRE_CANON = {
    "hip-hop/rap": "rap", "hip hop/rap": "rap", "hip-hop": "rap", "rap": "rap",
    "r&b/soul": "rnb", "r&b": "rnb", "soul": "soul", "rnb": "rnb",
    "dance": "dance", "electronic": "electro", "electro": "electro",
    "alternative": "alternative", "indie": "indie", "rock": "rock",
    "pop": "pop", "latin": "latin", "metal": "metal", "jazz": "jazz",
    "classical": "classical", "folk": "folk", "singer/songwriter": "folk",
    "house": "dance", "techno": "electro", "edm": "dance",
}


def _canon_genre(name: str) -> str:
    n = name.strip().lower()
    return _GENRE_CANON.get(n, n)


def _deezer_tracks(gid: int, n: int) -> list[dict]:
    http = HTTPFetcher()
    status, body = http.fetch(_DEEZER_CHART.format(gid=gid, n=n), retries=2)
    if not status or not (200 <= status < 300) or not body:
        return []
    try:
        return json.loads(body).get("data", [])
    except Exception:
        return []


def fetch_deezer_genre_charts(genre_names: list[str], *,
                              limit: int = 50) -> list[ChartEntry]:
    """Global + per-genre Deezer charts, normalized to ChartEntry."""
    out: list[ChartEntry] = []
    # global chart first (gid=0)
    for gid, label in [(0, "deezer-global")] + [
            (DEEZER_GENRES[g], f"deezer-{g}") for g in genre_names
            if g in DEEZER_GENRES]:
        tracks = _deezer_tracks(gid, limit)
        size = len(tracks)
        for i, t in enumerate(tracks, 1):
            title = (t.get("title") or "").strip()
            artist = ((t.get("artist") or {}).get("name") or "").strip()
            if not title:
                continue
            out.append(ChartEntry(
                title=title, artist=artist, rank=i, chart=label, list_size=size,
                genres=[g for g in genre_names if g in label] or [],
                url=(t.get("link") or ""),
            ))
    return out


def fetch_all_charts(niche_name: str, *, itunes_limit: int = 100,
                     deezer_limit: int = 50,
                     http: HTTPFetcher | None = None) -> list[ChartEntry]:
    """Every chart backend for a niche, merged into one list."""
    entries: list[ChartEntry] = []
    entries += fetch_itunes_charts(http, limit=itunes_limit)
    entries += fetch_deezer_genre_charts(genres_for_niche(niche_name),
                                         limit=deezer_limit)
    log.info("charts for %s -> %d entries", niche_name, len(entries))
    return entries