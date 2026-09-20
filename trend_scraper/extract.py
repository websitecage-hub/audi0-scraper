"""Track extraction from raw HTML + niche definition/scoring."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# ---- a trending audio entry -------------------------------------------------
@dataclass
class Track:
    title: str
    artist: str = ""
    source: str = "unknown"
    niche_score: int = 0
    origin_label: str = ""   # e.g. "general-trend" or "niche-search"
    # --- trend-signal fields (populated by the engine / metrics) ---
    sources: list[str] = field(default_factory=list)   # every independent source
    best_rank: int = 0                                  # best list position seen
    list_size: int = 0                                  # size of that list
    current_hits: int = 0                               # hits on CURRENT feeds
    total_hits: int = 1                                 # total mentions
    relevance: float = 0.0                              # 0..1 niche relevance
    trend_score: float = 0.0                            # 0..100 composite
    confidence: float = 0.0                             # 0..1 evidence strength
    last_seen: str = ""                                 # ISO date
    first_seen: str = ""
    sample_url: str = ""                                # a page where it appeared
    category: str = "audio"                             # instagram "reel audio" vs song

    @property
    def display(self) -> str:
        return f"{self.title} — {self.artist}" if self.artist else self.title

    @property
    def source_count(self) -> int:
        return len(self.sources)

    def __hash__(self):
        return hash((self.title.lower(), self.artist.lower()))

    def __eq__(self, other):
        return isinstance(other, Track) and (self.title.lower(), self.artist.lower()) == (
            other.title.lower(), other.artist.lower())


# ---- niches ----------------------------------------------------------------
# Niche now lives in .niches (full registry); re-exported here for compatibility.
from .niches import Niche, resolve  # noqa: E402  (after Track to avoid cycles)


# ---- html -> text ----------------------------------------------------------
_TAG_RE = re.compile(r"<script.*?</script>|<style.*?</style>", re.S)
_ANY_TAG_RE = re.compile(r"<[^>]+>")

TOKEN_SKIP = {
    "about", "home", "blog", "contact", "menu", "skip", "login", "join", "search",
    "related", "articles", "privacy", "terms", "copyright", "sitemap", "newsletter",
}

# Title/artist separator: em-dash, en-dash, hyphen, or feat.
_SEP = r"(?:\u2014|\u2013|–|—|-|ft\.|feat\.|featuring)"

# A track entry lives at the START of a line, optionally prefixed by a list
# number and/or an opening quote:   "8. 'Ain't No Mountain High Enough' — Marvin Gaye"
_LINE_HEAD = r"(?P<pre>(?:^|[\n\r])\s*(?:\#?\d{1,3}[\.\):]\s*)?['\"\u201c\u2018]*)"
# NOTE: verbose mode makes bare '#' a comment, so escape it above as \# .
# Accepts both "1. " and "#1. " list prefixes.

_TRACK_RE = re.compile(
    rf"""(?ix)
    {_LINE_HEAD}
    (?P<title>[A-Za-z][A-Za-z0-9 .'’&!?/()\[\]:+&-]{{2,55}}?)
    \s*{_SEP}\s*
    (?P<artist>[A-Za-z0-9 .'’&\-]{{2,40}}?)
    (?=$|[\r\n]|['\"\u2019\u201d]+$)
    """
)

# Phrases that mark article prose / headlines / nav rather than a real track.
_PROSE_SUBSTR = [
    "best instagram", "songs for", "story songs", "reels songs", "for ig",
    "for instagram", "this week", "why do", "how to", "tiktok and", "that makes",
    "feel like", "want your", "looks like", "and neffex", "the vibe", "high energy",
    "self growth", "pep talk", "no roadmap", "breath of fresh", "early adoption",
    "high impact", "high -", "breath of", "a slightly",
    "video report", "roas", "over-week", "written by", "form video",
    "instagram reels", "instagram songs", "web player", "to-date",
    "film-edit", "grounding technique", "faq", "discovery instagram",
    "man and film", "improvement content",
    "talking shot", "up-to-wide", "social media management", "to suffering of",
    "let's have some fun", "a solution to", "management tool",
    "improvement & personal growth", "best baseball", "trend reports",
    "reels per week", "ai video generator", "tips and tricks",
    "marketing ebook", "bridging fiction", "batch fifty", "jamback",
]


def _to_text(raw: bytes | str) -> str:
    s = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    s = _TAG_RE.sub("", s)
    s = _ANY_TAG_RE.sub("\n", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    return s


def extract_tracks(raw: bytes | str, source: str, *, min_len: int = 3,
                   max_len: int = 60) -> list[Track]:
    """Extract 'Title — Artist' style audio entries from a page's text.

    Matches are anchored to line starts (list entries) and pass a capitalization
    + prose-blacklist check so we skip article sentences and headlines. When an
    entry is prefixed by a list number ("8. Song — Artist") that position is kept
    as `best_rank`, which the scorer rewards.
    """
    text = _to_text(raw)
    found: dict[tuple[str, str], Track] = {}
    for idx, m in enumerate(_TRACK_RE.finditer(text)):
        title = _clean_tok(m.group("title")).strip().rstrip("'\"\u2019\u201d")
        artist = _clean_tok(m.group("artist")).strip()
        if not _looks_like_track(title, artist, min_len, max_len):
            continue
        key = (title.lower(), artist.lower())
        if key not in found:
            tr = Track(title=title, artist=artist, source=source)
            tr.sample_url = source
            rank = _rank_from_prefix(m.group("pre") or "")
            tr.best_rank = rank or (idx + 1)   # fall back to appearance order
            tr.sources = [source]
            found[key] = tr
    return list(found.values())


_NUM_PREFIX_RE = re.compile(r"#?(\d{1,3})\s*[\.\):]")


def _rank_from_prefix(prefix: str) -> int:
    """Pull a list position out of a prefix like '  8. ' or '#12) '."""
    m = _NUM_PREFIX_RE.search(prefix or "")
    return int(m.group(1)) if m else 0


# Lowercase function words that legitimately appear mid-title in real songs
# ("Eye of the Tiger", "Break in the Clouds"). They must not break the
# capitalization gate that separates titles from prose.
_STOPWORDS = {
    "of", "the", "a", "an", "to", "in", "on", "and", "or", "my", "me", "you",
    "your", "is", "it", "at", "for", "with", "by", "from", "as", "be", "we",
    "us", "our", "no", "not", "so", "if", "up", "out", "into", "over", "all",
    "i", "dont", "don't", "cant", "can't", "wont", "won't", "im", "i'm",
}


def _looks_like_track(title: str, artist: str, min_len: int, max_len: int) -> bool:
    """Heuristic gate: is this really "Song — Artist" not an article sentence?"""
    if not title or not artist:
        return False
    if not (min_len <= len(title) <= max_len):
        return False
    if len(artist) > 40 or len(artist) < 3:
        return False
    if not re.search(r"[A-Za-z]{2}", artist):
        return False
    blob = f"{title} {artist}".lower()
    if any(p in blob for p in _PROSE_SUBSTR):
        return False
    # Significant title words (excluding lowercase function words) should be
    # capitalized => track-like, not prose.
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’]*", title)
             if w.lower() not in _STOPWORDS]
    if len(words) >= 2:
        cap = sum(1 for w in words if w[0].isupper())
        if cap < len(words):   # any significant word lowercase => prose
            return False
    # artist should be capitalized too (except known lowercase markers).
    aw = [w for w in re.findall(r"[A-Za-z][A-Za-z'’]*", artist)
          if w.lower() not in _STOPWORDS]
    if aw and aw[0][0].islower() and "original audio" not in artist.lower():
        return False
    return True



def _clean_tok(tok: str) -> str:
    tok = re.sub(r"\s+", " ", tok).strip()
    # drop pure-number / nav headings
    if not tok or tok.lower() in TOKEN_SKIP:
        return ""
    return tok


def relevance_score(track: "Track", niche: Niche) -> float:
    """0..1 niche relevance from the niche's lexicons.

    Weighted: a mood-word hit (the song's vibe matches the niche) counts more
    than a context-word hit (the page mentions the niche). Normalised against a
    reference so a strong match lands near 1.0.
    """
    blob = f"{track.title} {track.artist}".lower()
    mood = sum(1 for w in niche.mood_words if w in blob)
    ctx = sum(1 for w in niche.context_words if w in blob)
    q = 1 if any(w in blob for w in niche.queries if w) else 0
    raw = mood * 2.0 + ctx * 1.0 + q * 1.0
    return min(1.0, raw / 6.0)   # ~3 mood hits => saturated


def score_tracks(tracks: list[Track], niche: Niche) -> list[Track]:
    """Legacy helper: attach an integer niche_score + 0..1 relevance to each track.

    The engine now uses `relevance_score` directly when computing composite
    trend scores; this stays for the CLI's simple niche_score display.
    """
    for tr in tracks:
        tr.relevance = relevance_score(tr, niche)
        tr.niche_score = int(round(tr.relevance * 10))
    return tracks
