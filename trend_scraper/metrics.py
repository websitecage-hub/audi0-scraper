"""Composite trend metrics — turn raw mentions across many sources into a ranking.

The core idea: a song is NOT "trending" because one blog lists it. It is trending
when MANY INDEPENDENT sources list it *recently* and *high up*, for a niche. So the
score is built from orthogonal signals, each normalised to a weight:

    signal                     weight   why
    -------------------------  -------  -----------------------------------------
    cross-source corroboration   40     the strongest evidence of a real trend
    niche relevance              25     does it belong to THIS niche
    momentum (current feeds)     20     current-week lists vs evergreen lists
    rank quality                 15     appearing high in a list means more

trend_score is 0-100 (sum of the above). confidence (0-1) says how much evidence
actually backed the score, so callers can filter low-evidence rows.

All functions are pure and dependency-free so they are trivially testable.
"""
from __future__ import annotations

import math
from datetime import date, datetime

# ---- normalisation helpers -------------------------------------------------

def corroboration_weight(source_count: int) -> float:
    """0..40. Grows with the number of independent sources listing the track.

    1 source is weak (any list can name any song); 3+ is strong; saturates ~7.
    Diminishing returns via a cube-root curve so the 2nd source matters more
    than the 6th.
    """
    if source_count <= 0:
        return 0.0
    return 40.0 * min(1.0, (source_count ** (1 / 3)) / 2.0)


def niche_relevance_weight(relevance: float) -> float:
    """0..25 from a 0..1 niche-relevance score."""
    return 25.0 * max(0.0, min(1.0, relevance))


def momentum_weight(current_hits: int, total_hits: int) -> float:
    """0..20 — share of mentions coming from CURRENT (this-week) trend feeds.

    A track that only exists on evergreen 'best motivational songs' lists is
    popular, not trending. One that shows up on this-week feeds is trending.
    """
    if total_hits <= 0:
        return 0.0
    share = current_hits / total_hits
    # require some absolute corroboration before momentum can max out
    return 20.0 * share * min(1.0, math.log2(total_hits + 1) / 2.0)


def rank_weight(best_rank: int, total_tracks: int) -> float:
    """0..15 for list position. rank 1 in a 20-list -> full; absent rank -> 0."""
    if not best_rank or best_rank <= 0 or total_tracks <= 0:
        return 0.0
    # position percentile: 1st of N -> 1.0, last -> near 0
    pct = 1.0 - (best_rank - 1) / max(1, total_tracks)
    return 15.0 * max(0.0, min(1.0, pct))


# ---- confidence ------------------------------------------------------------

def confidence(source_count: int, signals_present: int, total_signals: int = 4) -> float:
    """0..1 — how much evidence backs this row.

    Corroboration dominates; a track on 3+ sources is high-confidence even if we
    could not measure every signal (e.g. no rank in a flat list).
    """
    corr = min(1.0, source_count / 3.0)
    coverage = signals_present / max(1, total_signals)
    return round(min(1.0, 0.7 * corr + 0.3 * coverage), 3)


# ---- freshness -------------------------------------------------------------

def recency_boost(last_seen: str | None, *, half_life_days: int = 14) -> float:
    """Multiplicative freshness factor in [0.5, 1.0] applied to the final score.

    Recently-seen trends rank above stale ones without hard-expiring them.
    """
    if not last_seen:
        return 1.0
    try:
        d = datetime.fromisoformat(last_seen).date()
    except Exception:
        try:
            d = date.fromisoformat(last_seen)
        except Exception:
            return 1.0
    age = max(0, (date.today() - d).days)
    return max(0.5, 0.5 ** (age / half_life_days))


def compute_score(
    *,
    source_count: int,
    relevance: float,
    current_hits: int,
    total_hits: int,
    best_rank: int,
    total_tracks: int,
    last_seen: str | None = None,
) -> tuple[float, float]:
    """Return (trend_score 0-100, confidence 0-1) for one track."""
    raw = (
        corroboration_weight(source_count)
        + niche_relevance_weight(relevance)
        + momentum_weight(current_hits, total_hits)
        + rank_weight(best_rank, total_tracks)
    )
    score = raw * recency_boost(last_seen)
    signals = sum(1 for x in (source_count >= 1, relevance > 0,
                              current_hits > 0, best_rank > 0) if x)
    return round(score, 1), confidence(source_count, signals)
