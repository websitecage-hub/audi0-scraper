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

    @property
    def display(self) -> str:
        return f"{self.title} — {self.artist}" if self.artist else self.title

    def __hash__(self):
        return hash((self.title.lower(), self.artist.lower()))

    def __eq__(self, other):
        return isinstance(other, Track) and (self.title.lower(), self.artist.lower()) == (
            other.title.lower(), other.artist.lower())


# ---- niches ----------------------------------------------------------------
@dataclass
class Niche:
    name: str
    queries: list[str] = field(default_factory=list)
    mood_words: list[str] = field(default_factory=list)   # title/artist lexicon boost
    context_words: list[str] = field(default_factory=list)  # words found near tracks on-page

    @classmethod
    def default(cls) -> "Niche":
        return cls(name="motivation", queries=["motivation", "motivational"])

    @classmethod
    def from_name(cls, name: str) -> "Niche":
        n = name.strip().lower()
        if any(k in n for k in ("self", "growth", "improvement", "selfhelp", "habit")):
            return cls(
                name=name,
                queries=[n, "self improvement", "personal growth", "discipline"],
                mood_words=["rise", "strong", "better", "believe", "glorious", "fire",
                            "mountain", "dream", "high", "best", "inner", "light",
                            "calm", "reflect", "good"],
                context_words=["self improvement", "personal growth", "habit", "discipline",
                               "mindset", "routine", "discipline", "growth"],
            )
        if any(k in n for k in ("gym", "fitness", "workout", "body", "sport")):
            return cls(
                name=name,
                queries=[n, "gym reels audio", "workout motivation music"],
                mood_words=["tiger", "eye", "fight", "strong", "titanium", "beast",
                            "champion", "level", "power", "hustle", "grind", "dope"],
                context_words=["gym", "workout", "fitness", "gains", "training", "grind"],
            )
        if any(k in n for k in ("money", "business", "entrepreneur", "finance", "hustle")):
            return cls(
                name=name,
                queries=[n, "business motivation reels audio", "hustle reels songs"],
                mood_words=["money", "hustle", "grind", "empire", "boss", "maker",
                            "level", "champion", "success", "flex"],
                context_words=["business", "money", "entrepreneur", "hustle", "success", "grind"],
            )
        # fall back to generic motivation
        return cls(
            name=name,
            queries=[n, "motivation reels audio", "motivational songs reels"],
            mood_words=["rise", "strong", "believer", "fire", "mountain", "fight",
                        "tiger", "dream", "glorious", "great", "power", "level"],
            context_words=[n, "motivation", "inspire", "success"],
        )


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
_LINE_HEAD = r"(?P<pre>(?:^|[\n\r])\s*(?:\#\d+[\.\):]\s*)?['\"\u201c\u2018]*)"
# NOTE: verbose mode makes bare '#' a comment, so escape it above as literal safely.

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
    + prose-blacklist check so we skip article sentences and headlines.
    """
    text = _to_text(raw)
    found: dict[tuple[str, str], Track] = {}
    for m in _TRACK_RE.finditer(text):
        title = _clean_tok(m.group("title")).strip().rstrip("'\"\u2019\u201d")
        artist = _clean_tok(m.group("artist")).strip()
        if not _looks_like_track(title, artist, min_len, max_len):
            continue
        key = (title.lower(), artist.lower())
        if key not in found:
            found[key] = Track(title=title, artist=artist, source=source)
    return list(found.values())


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
    # every significant word in title should be capitalized => track-like, not prose.
    words = re.findall(r"[A-Za-z][A-Za-z'’]*", title)
    if len(words) >= 3:
        cap = sum(1 for w in words if w[0].isupper())
        if cap < max(2, len(words) - 1):   # e.g. missing on 2+ of several words -> prose
            return False
    # artist should be capitalized too (except known lowercase markers).
    aw = re.findall(r"[A-Za-z][A-Za-z'’]*", artist)
    if aw and aw[0][0].islower() and "original audio" not in artist.lower():
        return False
    return True



def _clean_tok(tok: str) -> str:
    tok = re.sub(r"\s+", " ", tok).strip()
    # drop pure-number / nav headings
    if not tok or tok.lower() in TOKEN_SKIP:
        return ""
    return tok


def score_tracks(tracks: list[Track], niche: Niche) -> list[Track]:
    """Niche-score tracks by title/artist lexicon and fetch each page's context words."""
    scored: list[Track] = []
    for tr in tracks:
        blob = f"{tr.title} {tr.artist}".lower()
        hits = sum(1 for w in niche.mood_words if w in blob)
        ctx_hits = sum(1 for w in niche.context_words if w in blob)
        tr.niche_score = hits * 2 + ctx_hits + (1 if any(w in blob for w in niche.queries) else 0)
        scored.append(tr)
    return scored
