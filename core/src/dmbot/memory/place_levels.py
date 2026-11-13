"""How big a place is (#4173 slice 1; Supervisor's yes and the owner's, 2026-11-11): plane, world,
region, settlement, district, street, building or room, largest first. It lets DMbot tell a
building from the town it stands in without being told that one is inside the other: two places of
different sizes are never rivals for "where someone is", and two of the same size are (the bank and
the bakery, Waterdeep and Neverwinter).

The words that give a level are fixed here (Supervisor's ruling: a campaign-extendable list only
once the sample shows words this misses; `level_unknown` is counted in the read report so they show
up). A level is read from the name itself ("Market Square") or from the words right beside the name
in the text ("the town of Targos", "Targos, a fishing town"). Nothing is guessed from a bare name:
no word, no level. The DM sets or changes it in one tap. Pure: no Discord, no database."""

from __future__ import annotations

import re

# Largest first: the order is what "bigger than" means.
LEVELS = ("plane", "world", "region", "settlement", "district", "street", "building", "room")
RANK = {level: i for i, level in enumerate(LEVELS)}
# The sizes the campaign's setting may be (#4173 slice 2): a region or bigger
SETTING_LEVELS = ("plane", "world", "region")
# Who said how big a place is: DMbot read it from the words, or the DM set it (the DM wins).
READ, BY_DM = "read", "dm"
FROM = (READ, BY_DM)
# A place's form (#4173 slice 6a): water (a lake, bay, sea or river) or a route (a named road,
# trade route or pass); None is an area, every other place. Water and route places stand outside
# the size order above, so being "on the Eastway" is never a rival of being in a town.
WATER, ROUTE = "water", "route"
FORMS = (WATER, ROUTE)

# What the DM's menu calls each level (plain words; "settlement" is a town or city to a DM).
LABELS = {
    "plane": "a plane",
    "world": "a world or continent",
    "region": "a region",
    "settlement": "a town or city",
    "district": "a district",
    "street": "a street or square",
    "building": "a building",
    "room": "a room",
}

# The words that say a level. A word is matched whole, singular or plural, in any case.
_WORDS: dict[str, tuple[str, ...]] = {
    "plane": ("plane", "demiplane"),
    "world": ("world", "continent"),
    "region": (
        "region", "kingdom", "realm", "dale", "valley", "province", "land", "lands", "wilderness",
        "wastes", "wasteland", "tundra", "forest", "wood", "mountains", "range", "island", "isle",
        "coast", "desert", "empire", "territory", "frontier",
    ),
    "settlement": (
        "town", "city", "village", "hamlet", "settlement", "port", "outpost", "capital",
        "fishing town", "trading post",
    ),
    "district": (
        "district", "quarter", "ward", "neighbourhood", "neighborhood", "docks",
    ),
    "street": (
        "street", "lane", "alley", "road", "avenue", "square", "plaza", "market", "marketplace",
        "bazaar", "quay", "wharf",
    ),
    "building": (
        "tavern", "inn", "shop", "store", "smithy", "forge", "temple", "shrine", "church",
        "keep", "castle", "tower", "manor", "mansion", "house", "hall", "lodge", "bank",
        "bakery", "brewery", "warehouse", "guildhall", "library", "palace", "fortress", "fort",
        "mill", "stable", "stables", "barracks", "prison", "jail", "lighthouse", "cabin", "hut",
    ),
    "room": (
        "room", "chamber", "cellar", "basement", "attic", "hallway", "corridor", "vault",
        "common room", "throne room", "kitchen", "bedroom", "study", "crypt",
    ),
}  # fmt: skip

# Every word to its level; a longer phrase is tried first ("common room" before "room").
_BY_WORD = {w: level for level, words in _WORDS.items() for w in words}
_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(_BY_WORD, key=len, reverse=True)) + r")s?\b",
    re.IGNORECASE,
)
# Words left out on purpose: "harbour" (Minstral Harbor is a city) and others that are as often
# part of a settlement's own name. A missing level is the safe side: a wrong one turns off a check.
_OF = re.compile(r"\s+of\s+", re.IGNORECASE)  # "Spine of the World": the name before "of"
_WINDOW = 120  # characters read either side of a name: a short look, never the whole upload
_SENTENCE_END = re.compile(r"[.!?;:\n]")
_ARTICLES = ("the", "a", "an")


def level_of_word(word: str) -> str | None:
    """The level a single word says ("tavern" → building), or None."""
    w = " ".join(word.split()).casefold()
    return _BY_WORD.get(w) or _BY_WORD.get(w.removesuffix("s"))


def level_from_name(name: str) -> str | None:
    """A level the name itself says: "Market Square", "the Yawning Portal Inn". The last level word
    wins ("Castle Street" is a street). Two kinds of name say nothing (Supervisor, #4186):
    - Only the part before the first "of" counts: "Tower of Kord" is a building, but "Spine of the
      World" is a mountain range, not a world.
    - A word read only through a plural s gives no level: "Ten-Towns" is a region of ten towns, not
      a town (a word that is itself on the list, such as "Docks" or "Stables", still counts)."""
    head = _OF.split(name, maxsplit=1)[0]
    found = [m.group(0) for m in _PATTERN.finditer(head)]
    whole = [w for w in found if " ".join(w.split()).casefold() in _BY_WORD]
    return _BY_WORD[" ".join(whole[-1].split()).casefold()] if whole else None


def level_beside(name: str, text: str) -> str | None:
    """A level the words right beside the name say, in `text` (a quote, a line about it), in one of
    three set shapes only: "the town of Targos", "a tavern called the Three Flags Sailing" and
    "Targos, a fishing town". Any other word beside a name says nothing ("the road to Targos" is not
    a street, "the guards keep Targos" not a building): a wrong size switches off a check (#4173),
    so a missing one is the safe side. Read within the name's own sentence, at its first whole-word
    mention. None when the name is not in the text or no shape fits."""
    if not name.strip() or not text:
        return None
    found = re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE)
    if found is None:
        return None
    # (only the name's own sentence, and only a short window of it: no read across a full stop)
    before = _SENTENCE_END.split(text[max(0, found.start() - _WINDOW) : found.start()])[-1]
    after = _SENTENCE_END.split(text[found.end() : found.end() + _WINDOW])[0]
    back = [w for w in (_bare(w) for w in before.split()[::-1]) if w]  # nearest first
    if back and back[0] in _ARTICLES:
        back = back[1:]
    if len(back) >= 2 and back[0] in ("of", "called", "named"):
        # "the town of", "the woodland village of", "a tavern called (the)"
        # "the woodland village of": the phrase first, then the word right before "of"
        for words in (f"{back[2]} {back[1]}" if len(back) > 2 else "", back[1]):
            level = level_of_word(words) if words else None
            if level is not None:
                return level
    if after.startswith(","):  # "Targos, a fishing town": an article, then a word or two
        ahead = [w for w in (_bare(w) for w in after[1:].split()) if w][:3]
        if len(ahead) >= 2 and ahead[0] in _ARTICLES:
            third = ahead[2] if len(ahead) > 2 else ""
            # "a fishing town", "a town", "an old tower": the phrase, its first word, its second
            for words in (f"{ahead[1]} {third}", ahead[1], third):
                level = level_of_word(words) if words.strip() else None
                if level is not None:
                    return level
    return None


def _bare(word: str) -> str:
    return re.sub(r"[^\w']", "", word).casefold()


def read_level(name: str, *texts: str) -> str | None:
    """The level DMbot reads for a place: its name first, then each text in turn. None: unknown."""
    level = level_from_name(name)
    if level is not None:
        return level
    for text in texts:
        level = level_beside(name, text)
        if level is not None:
            return level
    return None


# The words in a place's own name that say its form (#4173 slice 6a), matched whole, in any case.
# Kept short on purpose, as the levels are: "road" stays a street (a town has roads), and "sound",
# "pass" or "way" are as often part of another name. A missing form is an area, the safe side; the
# DM changes it in one tap.
_FORM_WORDS: dict[str, tuple[str, ...]] = {
    # ("mere" is left out: the Mere of Dead Men is a marsh)
    WATER: ("lake", "lac", "maer", "loch", "sea", "ocean", "bay", "gulf", "river",
            "strait", "fjord"),
    ROUTE: ("highway", "trail", "trade way", "trade route", "trade road"),
}  # fmt: skip
_FORM_BY_WORD = {w: form for form, words in _FORM_WORDS.items() for w in words}
_FORM_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(_FORM_BY_WORD, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def read_form(name: str) -> str | None:
    """The form a place's own name says: "Maer Dualdon" and "Lac Dinneshere" are water, "the
    Northern Trail" a route, and "the Black Road" a street still; None is an area. Only the part
    before the first "of" counts, as for a level ("Bay of Chult" is water, "Lord of the Lake" says
    nothing), and the first word found wins. A name that also says a size says no form ("Lake
    Town" is a town). Nothing is read from the words around the name: a town "by the lake" is no
    lake."""
    if level_from_name(name) is not None:
        return None
    head = _OF.split(name, maxsplit=1)[0]
    found = _FORM_PATTERN.search(head)
    return _FORM_BY_WORD[" ".join(found.group(0).split()).casefold()] if found else None


def bigger(a: str, b: str) -> bool:
    """Is level `a` larger than level `b`? (For the "where the story is" chain, slices 3 and 4.)"""
    return RANK[a] < RANK[b]


def different(a: str | None, b: str | None) -> bool:
    """Are these two levels both known and not the same? Then a mover in one is never "in two places
    at once" for being in the other too: a building and the town it may stand in. Two places of the
    same level, or any place whose level nobody knows, follow the rules as before this slice."""
    return a is not None and b is not None and a != b
