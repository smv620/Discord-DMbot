"""Words that say a moment, not a lasting tie: a hit, a swing, a shot, a sighting, a chase, a
feeding, and a prediction ("will likely go to"). The story graph keeps ties that last (family,
owns, serves, is held by); a blow in one round, or what someone expects to happen, is not one
(#3244, Supervisor's ruling on the single-actions note).

Pure and word-based, so it needs no database. It is deliberately a short closed list: the reader
only drops a phrase it recognises, and a phrase it is unsure of goes on to the DM as before
(a wrongly dropped tie is worse than a wrongly kept one, which the DM can turn down). A verb that
can also name a lasting tie ("seen as a hero", "caught and imprisoned", "fed the orphans") counts
only when it stands alone or with a bare preposition."""

from __future__ import annotations

import re

# A blow or a throw is one moment whatever follows it.
_BLOWS = frozenset({
    "hit", "hits", "hitting", "swing", "swings", "swung", "swinging",
    "shoot", "shoots", "shot", "shooting", "stab", "stabs", "stabbed", "slash", "slashes",
    "slashed", "strike", "strikes", "struck", "punch", "punches", "punched", "kick", "kicks",
    "kicked", "bite", "bites", "bit", "attack", "attacks", "attacked",
    "throw", "throws", "threw", "hurl", "hurls", "shove", "shoves", "push", "pushes",
    "tackle", "tackles", "lunge", "lunges", "slap", "slaps",
    "chase", "chases", "chased",
})  # fmt: skip
# A sighting, a catch or a feeding can also be a lasting tie in other words, so these count only
# alone or with a bare preposition ("can see", "cannot catch", "feeds and waters", "watches at").
_LOOSE = frozenset({
    "see", "sees", "saw", "spot", "spots", "spotted", "notice", "notices", "noticed",
    "glimpse", "glimpses", "glimpsed", "watch", "watches", "watched",
    "catch", "catches", "feed", "feeds", "fed",
})  # fmt: skip
_BARE = frozenset({"at", "on", "by", "up", "and", "water", "waters", "watered", "to"})
# Words that mean the phrase is about something lasting or about the rules, wherever they stand:
# "attacked and kidnapped", "struck a deal with", "has hit points".
_LASTING = frozenset({
    "captive", "prisoner", "kidnapped", "imprisoned", "captured", "enslaved", "deal", "pact",
    "oath", "vow", "points", "point", "die", "dice", "roll", "rolls", "damage", "always",
    "never", "forever", "serve", "serves", "protect", "protects", "care", "cares",
})  # fmt: skip
# Words that only prepare the real one: "can see", "did hit", "is hit by", "just swung at".
_HELPERS = frozenset({
    "can", "cannot", "could", "couldn", "t", "did", "didn", "does", "doesn", "do", "has",
    "have", "had", "hasn", "haven", "hadn", "is", "isn", "are", "aren", "was", "wasn", "were",
    "weren", "been", "being", "got", "gets", "not", "still", "already", "ever", "often",
    "just", "then", "also", "again", "first", "suddenly", "quickly",
})  # fmt: skip
# Words that soften a forecast without changing it: "probably will", "also will".
_SOFTENERS = frozenset({"probably", "likely", "also", "then", "just", "soon", "still"})


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def is_one_off(text: str) -> bool:
    """Does this relationship phrase name a single action or sighting?"""
    words = [w for w in _words(text) if w not in _HELPERS]
    if not words or _LASTING.intersection(words):
        return False
    head, rest = words[0], words[1:]
    if head in _BLOWS:
        return True
    return head in _LOOSE and all(w in _BARE for w in rest)


def is_prediction(text: str) -> bool:
    """Does this relationship phrase say what will happen? Only the future forms ("will",
    "shall", "is going to"): "might" and "may" hedge a present claim and stay with the DM, and
    a promise ("will always serve") is a lasting stance."""
    words = [w for w in _words(text) if w not in _SOFTENERS]
    if not words or _LASTING.intersection(words):
        return False
    if words[0] in {"will", "shall", "gonna"}:
        return True
    # "is going to", "are about to"
    return len(words) > 2 and words[0] in {"is", "are"} and words[1] in {"going", "about"}
