"""Niche registry — query sets + lexicons used to decide relevance and discovery.

Each niche carries three things:
  queries        search phrasings that surface pages about this niche's reels audio
  mood_words     words signalling the *musical mood* this niche uses (title/artist)
  context_words  words a page uses when it is really about this niche

`Niche.from_name` matches free-form user input ("gym", "fitness reels",
"workout motivation") onto the closest registry entry, so callers can pass
anything reasonable instead of an exact key.

Adding a niche is one dict entry — no code changes elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Niche:
    name: str
    queries: list[str] = field(default_factory=list)
    mood_words: list[str] = field(default_factory=list)
    context_words: list[str] = field(default_factory=list)

    @classmethod
    def default(cls) -> "Niche":
        return cls(name="motivation", queries=["motivation", "motivational"])

    @classmethod
    def from_name(cls, name: str) -> "Niche":
        return resolve(name)


# ---- registry --------------------------------------------------------------
# keywords -> canonical niche key. First match wins (order matters: put the more
# specific keys first).
_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("self-improvement", ("self improvement", "self-improvement", "self help", "selfhelp",
                          "personal growth", "growth mindset", "habit", "discipline",
                          "journal", "mindset")),
    ("gym", ("gym", "fitness", "workout", "body", "sport", "training", "lifting",
             "aesthetic", "physique", "gains")),
    ("money", ("money", "business", "entrepreneur", "finance", "hustle", "startup",
               "wealth", "invest", "millionaire", "side hustle")),
    ("motivation", ("motivation", "motivational", "inspire", "inspiration", "grind",
                    "success", "winner", "never give up")),
    ("travel", ("travel", "wanderlust", "adventure", "roadtrip", "road trip",
                "explore", "vanlife", "backpack")),
    ("fashion", ("fashion", "style", "outfit", "ootd", "lookbook", "streetwear")),
    ("beauty", ("beauty", "makeup", "skincare", "glow", "cosmetic", "grwm")),
    ("food", ("food", "cooking", "recipe", "baking", "chef", "cafe", "restaurant",
              "foodie")),
    ("nature", ("nature", "mountains", "ocean", "sunset", "landscape", "aesthetic nature",
                "outdoors", "forest")),
    ("cinematic", ("cinematic", "film", "movie", "trailer", "epic", "orchestral",
                   "score", "drama")),
    ("love", ("love", "romance", "relationship", "couple", "wedding", "heartbreak",
              "breakup")),
    ("sad", ("sad", "emotional", "melancholy", "deep", "lofi sad", "hurt")),
    ("summer", ("summer", "beach", "vacation", "tropical", "holiday")),
    ("party", ("party", "club", "edm", "dance", "festival", "rave", "nightlife")),
    ("rap", ("rap", "hip hop", "hiphop", "trap", "drill", "bars", "freestyle")),
    ("lofi", ("lofi", "lo-fi", "chill", "chillhop", "study", "relax", "sleep")),
    ("anime", ("anime", "otaku", "manga", "japanese", "shonen")),
    ("gaming", ("gaming", "gamer", "gameplay", "twitch", "esports", "valorant",
                "fortnite")),
    ("crypto", ("crypto", "bitcoin", "trading", "forex", "stocks", "nft")),
    ("real-estate", ("real estate", "realestate", "property", "realtor", "housing")),
    ("podcast", ("podcast", "interview", "talk", "conversation")),
    ("comedy", ("comedy", "funny", "meme", "humor", "meme audio")),
]

_NICHES: dict[str, Niche] = {
    "self-improvement": Niche(
        name="self-improvement",
        queries=["self improvement", "personal growth", "discipline", "mindset"],
        mood_words=["rise", "strong", "better", "believe", "glorious", "fire",
                    "mountain", "dream", "high", "best", "inner", "light",
                    "calm", "reflect", "good", "unstoppable", "fearless"],
        context_words=["self improvement", "personal growth", "habit", "discipline",
                       "mindset", "routine", "growth", "motivation"],
    ),
    "gym": Niche(
        name="gym",
        queries=["gym reels audio", "workout motivation music", "fitness reels songs"],
        mood_words=["tiger", "eye", "fight", "strong", "titanium", "beast",
                    "champion", "level", "power", "hustle", "grind", "dope",
                    "animal", "legend", "warrior", "unstoppable"],
        context_words=["gym", "workout", "fitness", "gains", "training", "grind",
                       "lifting", "bodybuilding"],
    ),
    "money": Niche(
        name="money",
        queries=["business motivation reels audio", "hustle reels songs",
                 "entrepreneur reels music"],
        mood_words=["money", "hustle", "grind", "empire", "boss", "maker", "level",
                    "champion", "success", "flex", "millionaire", "rich", "gold"],
        context_words=["business", "money", "entrepreneur", "hustle", "success",
                       "grind", "startup", "wealth"],
    ),
    "motivation": Niche(
        name="motivation",
        queries=["motivation reels audio", "motivational songs reels",
                 "best motivation songs reels"],
        mood_words=["rise", "strong", "believer", "fire", "mountain", "fight",
                    "tiger", "dream", "glorious", "great", "power", "level",
                    "unstoppable", "champion", "warrior"],
        context_words=["motivation", "motivational", "inspire", "inspiration",
                       "success", "never give up"],
    ),
    "travel": Niche(
        name="travel",
        queries=["travel reels audio", "wanderlust reels songs", "travel vlog music"],
        mood_words=["wander", "journey", "paradise", "adventure", "horizon", "sky",
                    "world", "sun", "ocean", "free", "escape", "home"],
        context_words=["travel", "wanderlust", "adventure", "backpack", "vanlife",
                       "explore", "road trip"],
    ),
    "fashion": Niche(
        name="fashion",
        queries=["fashion reels audio", "outfit transition reels songs",
                 "streetwear reels music"],
        mood_words=["style", "vogue", "luxury", "expensive", "drip", "fashion",
                    "runway", "glamour"],
        context_words=["fashion", "style", "outfit", "ootd", "lookbook", "streetwear"],
    ),
    "beauty": Niche(
        name="beauty",
        queries=["beauty reels audio", "makeup transition reels songs", "grwm reels music"],
        mood_words=["glow", "pretty", "gorgeous", "shine", "diamond", "cute", "soft"],
        context_words=["beauty", "makeup", "skincare", "glow", "grwm", "cosmetic"],
    ),
    "food": Niche(
        name="food",
        queries=["food reels audio", "cooking reels songs", "recipe reels music"],
        mood_words=["sugar", "sweet", "tasty", "delicious", "hungry", "cook", "yummy"],
        context_words=["food", "cooking", "recipe", "baking", "chef", "foodie"],
    ),
    "nature": Niche(
        name="nature",
        queries=["nature reels audio", "aesthetic nature reels songs", "sunset reels music"],
        mood_words=["sun", "ocean", "river", "mountain", "sky", "wind", "earth",
                    "wild", "forest", "light"],
        context_words=["nature", "mountains", "ocean", "sunset", "landscape",
                       "outdoors", "forest"],
    ),
    "cinematic": Niche(
        name="cinematic",
        queries=["cinematic reels audio", "epic trailer music reels",
                 "cinematic film reels songs"],
        mood_words=["cinematic", "epic", "interstellar", "requiem", "orchestra",
                    "rise", "time", "dark", "hero", "battle"],
        context_words=["cinematic", "film", "trailer", "epic", "orchestral", "score"],
    ),
    "love": Niche(
        name="love",
        queries=["love reels audio", "romantic reels songs", "couple reels music"],
        mood_words=["love", "heart", "baby", "forever", "kiss", "beautiful", "stay",
                    "darling", "honey", "together"],
        context_words=["love", "romance", "relationship", "couple", "wedding"],
    ),
    "sad": Niche(
        name="sad",
        queries=["sad reels audio", "emotional reels songs", "sad aesthetic reels music"],
        mood_words=["sad", "tears", "cry", "lonely", "goodbye", "hurt", "broken",
                    "empty", "miss", "pain"],
        context_words=["sad", "emotional", "melancholy", "heartbreak", "breakup"],
    ),
    "summer": Niche(
        name="summer",
        queries=["summer reels audio", "summer vibes reels songs", "beach reels music"],
        mood_words=["summer", "sun", "beach", "hot", "california", "cruel", "tonight",
                    "dance", "party", "waves"],
        context_words=["summer", "beach", "vacation", "tropical", "holiday"],
    ),
    "party": Niche(
        name="party",
        queries=["party reels audio", "edm reels songs", "club dance reels music"],
        mood_words=["tonight", "dance", "party", "night", "fire", "lose", "control",
                    "crazy", "wild", "alive"],
        context_words=["party", "club", "edm", "dance", "festival", "rave"],
    ),
    "rap": Niche(
        name="rap",
        queries=["rap reels audio", "hip hop reels songs", "trap reels music"],
        mood_words=["money", "gang", "street", "real", "block", "pain", "loyal",
                    "savage", "goat", "flex"],
        context_words=["rap", "hip hop", "trap", "drill", "freestyle"],
    ),
    "lofi": Niche(
        name="lofi",
        queries=["lofi reels audio", "chill reels songs", "study lofi reels music"],
        mood_words=["chill", "lofi", "sleep", "dream", "rain", "calm", "quiet",
                    "coffee", "midnight", "slow"],
        context_words=["lofi", "chill", "chillhop", "study", "relax", "sleep"],
    ),
    "anime": Niche(
        name="anime",
        queries=["anime reels audio", "anime edit reels songs", "anime ost reels music"],
        mood_words=["anime", "sakura", "hero", "opening", "ost", "senpai", "dream",
                    "kawaii", "shonen"],
        context_words=["anime", "otaku", "manga", "japanese", "shonen"],
    ),
    "gaming": Niche(
        name="gaming",
        queries=["gaming reels audio", "gamer edit songs", "gameplay reels music"],
        mood_words=["game", "player", "level", "boss", "victory", "legend", "epic",
                    "win", "fight"],
        context_words=["gaming", "gamer", "gameplay", "twitch", "esports"],
    ),
    "crypto": Niche(
        name="crypto",
        queries=["crypto reels audio", "trading reels songs", "bitcoin reels music"],
        mood_words=["money", "moon", "rich", "gold", "diamond", "empire", "future"],
        context_words=["crypto", "bitcoin", "trading", "forex", "stocks"],
    ),
}


def resolve(name: str) -> Niche:
    """Map free-form input onto the closest registry niche."""
    n = (name or "").strip().lower()
    if n in _NICHES:
        return _NICHES[n]
    for key, kws in _ALIASES:
        if any(k in n for k in kws):
            return _NICHES[key]
    # unknown niche: build a generic entry from the words the caller gave us
    words = [w for w in n.replace("-", " ").split() if len(w) > 2]
    return Niche(
        name=name or "motivation",
        queries=[n, f"{n} reels audio", f"{n} reels songs"] if n else ["motivation"],
        mood_words=words,
        context_words=words or ["motivation", "inspire"],
    )


def all_niches() -> list[str]:
    return sorted(_NICHES)