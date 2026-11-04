"""Only unique items are tracked (#3373, owner 2026-11-04): "Garret's sled", not "a sled". An item
gets an entry in the story graph only when it is one particular thing; ordinary gear and
quantities of common things (a dagger, arrows, rations, a sled) stay in the transcript and the
recap. There is no question to the DM: when the reader is not sure, it judges, and keeps the item
if it is more likely than not one particular thing, else drops it.

An item is kept when any one of these holds:
1. it has a proper name ("Heartseeker", "Wand of Conducting"): a capitalised word that is not just
   the first word of a sentence-case phrase ("Wyvern poison" is a kind of thing);
2. it belongs to one named owner ("Garret's sled", "the Korran family journal");
3. a description ties it to one place, person or event ("the crystal from the mind flayer's
   skull");
4. the story turns on it (sought, stolen, delivered, fought over, or tied to a villain's plan):
   one consignment on one ferry is one thing, even when it is described by weight.
Otherwise a small score of signs decides. Pure and word-based: no database, no AI. The closed
lists are plain words, so the judgement can be read and argued with.

`models.looks_named` is the older, looser guess behind an existing entry's `unique_item` (any
capital letter counts). It is left as it is: stored entries are not touched by this rule."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Everyday gear with no identity of its own. Alone, or with an owner or a count, it is not tracked.
COMMON_KINDS = frozenset({
    "dagger", "daggers", "sword", "swords", "shortsword", "longsword", "greatsword", "bow",
    "bows", "longbow", "shortbow", "crossbow", "arrow", "arrows", "bolt", "bolts", "axe", "axes",
    "spear", "spears", "club", "mace", "staff", "shield", "armor", "armour", "helmet", "boots",
    "cloak", "rope", "torch", "torches", "lantern", "tinderbox", "ration", "rations", "waterskin",
    "potion", "potions", "vial", "vials", "poison", "scroll", "scrolls", "bag", "backpack",
    "pack", "sack", "sled", "sleds", "sledge", "wagon", "cart", "coin", "coins", "gold",
    "silver", "gem", "gems", "oil", "flask", "flasks", "bedroll", "tent", "pick", "shovel",
    "hammer", "key", "keys", "dog", "dogs", "horse", "horses",
})  # fmt: skip
# Words that start a phrase in capitals only because it starts the sentence or the list line: a
# creature, a material, an adjective. "Wyvern poison" is a kind of thing; "Sunbrand sword" is named.
PLAIN_CAPITALS = frozenset({
    "wyvern", "dragon", "goblin", "orc", "kobold", "troll", "giant", "ogre", "wolf", "bear",
    "owl", "elven", "dwarven", "iron", "steel", "silver", "golden", "gold", "wooden", "leather",
    "healing", "magic", "magical", "old", "ancient", "small", "large", "heavy", "light", "dog",
    "horse", "ice", "frost", "snow", "fire", "oil", "holy", "rusty", "broken", "plain",
})  # fmt: skip
_SMALL = frozenset({"a", "an", "the", "of", "from", "in", "on", "at", "for", "and", "to", "with"})
_QUANTITY = re.compile(
    r"\b(\d{1,3}(?:,\d{3})*|two|three|four|five|six|seven|eight|nine|ten|several|some|many|pair of|"
    r"pairs of|bunch of|pile of|handful of|stack of)\b",
    re.IGNORECASE,
)
# "from the mind flayer's skull", "built for Boy", "found in the Beautiful Mine"
_CLAUSE = re.compile(
    r"\b(from|of|built for|made for|found (?:in|under|on|beneath)|taken (?:from|by)|"
    r"belonging to|that|which)\b\s+(?:the\s+|a\s+|an\s+)?(?:[A-Z]|(?:\w+\s+){0,2}\w+'s\b)"
)
_POSSESSIVE = re.compile(r"^(?:the\s+)?([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)'s\s+(.+)$")
# What makes an item the story turns on: sought, stolen, delivered, fought over, a villain's plan.
_SIGNIFICANT = re.compile(
    r"\b(stole|stolen|steal|steals|theft|delivered|delivery|deliver|consignment|shipment|tribute|"
    r"smuggl\w*|sought|seeking|ransom|bounty|meant for|intended for|fought over|fight over|"
    r"villain's plan|the plan|plot thread|the ritual|cursed|corrupt\w*|madness|poisoned|tainted)\b",
    re.IGNORECASE,
)
# Words the text itself uses to say one particular thing is one of a kind: "a unique wolf figurine
# inlaid with black gemstones, etched with runes". Read only in the sentences that speak of the
# item (`_near`), so a rune in another sentence says nothing about it.
_MARK_WORDS = 5  # how close a word must be to the item's own last word
_MARKED = re.compile(
    r"\b(unique|one of a kind|one-of-a-kind|heirloom|relic|legendary|"
    r"(?:inlaid|etched|engraved|carved|set|studded) (?:with|in)|runes?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Verdict:
    keep: bool
    why: str  # the test that decided, a fixed word (never the campaign's own words): for tests


_SINGLE_PAIRS = frozenset(
    {"boots", "gloves", "glasses", "spectacles", "bracers", "greaves", "pants"}
)


def _words(name: str) -> list[str]:
    return re.findall(r"[\w'’-]+", name)


def _core(name: str) -> list[str]:
    words = _words(name)
    if words and words[0].casefold() in {"a", "an", "the"} and len(words) > 1:
        words = words[1:]
    return words


def _plural_head(words: list[str]) -> bool:
    """Does the thing named end in a plural ("arrows", "two sleds", "rations")?"""
    head = words[-1].casefold() if words else ""
    if head in _SINGLE_PAIRS:
        return False  # one pair of boots is one thing
    return head.endswith("s") and not head.endswith(("ss", "us", "is")) and len(head) > 3


def _proper(words: list[str]) -> bool:
    """A proper name: a capitalised word that is not merely the first word of a sentence-case
    phrase. A single capitalised word counts unless it is one of the common kinds."""
    if not words:
        return False
    if len(words) == 1:
        return words[0][:1].isupper() and words[0].casefold() not in COMMON_KINDS
    if any(w[:1].isupper() and w.casefold() not in _SMALL for w in words[1:]):
        return True
    # "Sunbrand sword", "Vorpal sword": a capitalised first word before a common kind is a name,
    # unless it is only a creature, a material or an adjective ("Wyvern poison", "Iron sword")
    first = words[0]
    return (
        first[:1].isupper()
        and not first.endswith(("'s", "’s"))  # (an owner: the possessive rule judges that)
        and not _plural_head(words)
        and words[-1].casefold() in COMMON_KINDS
        and first.casefold() not in PLAIN_CAPITALS
        and first.casefold() not in COMMON_KINDS
        and first.casefold() not in _SMALL
    )


def _marked_near(words: list[str], text: str) -> bool:
    """Is a one-of-a-kind word within a few words of the item's own last word? A sentence that
    lists two things ("waterproof backpacks ... a wolf figurine etched with runes") must not make
    the plain one unique."""
    if not words:
        return False
    head = words[-1].casefold().rstrip("s")
    tokens = re.findall(r"[\w'-]+", text)
    for i, token in enumerate(tokens):
        if token.casefold().rstrip("s") == head:
            window = " ".join(tokens[max(0, i - _MARK_WORDS) : i + _MARK_WORDS + 1])
            if _MARKED.search(window):
                return True
    return False


def _near(name: str, context: str) -> str:
    """The sentences of `context` that speak of this item (its last word): a word like "plan" in
    another sentence says nothing about it. A short context (one quote) is taken whole."""
    if len(context) < 200:
        return context
    head = re.escape(_core(name)[-1].casefold()) if _core(name) else ""
    parts = re.split(r"(?<=[.!?])\s+|\n+", context)
    return " ".join(p for p in parts if head and re.search(rf"\b{head}", p.casefold()))


_STANDARD_KINDS = ("item", "gear", "weapon", "armor", "tool")
_THE_FORM = re.compile(r"^(?:the|a|an|some|\d+|two|three|four|five|six)\s+", re.IGNORECASE)
_SCROLL = re.compile(r"^scrolls?\s+of\s+(.+)$", re.IGNORECASE)


def standard_item(name: str) -> bool:
    """Is this an item of the rulebook, one of many in the world ("Potion of Healing", "Bag of
    Holding", "Scroll of Identify")? The rules data DMbot already holds is asked, in both its
    editions: a magic item, potion or gear entry, or a scroll of any spell. A named artifact the
    rules do not hold ("Heartseeker") is not standard. Such an item is common like any other
    gear: tracked only when another test makes it one particular thing."""
    from dmbot.rules import index  # late: the rules data loads on first use

    text = _THE_FORM.sub("", " ".join(name.split()))
    if not text:
        return False
    rules = index.srd()
    scroll = _SCROLL.match(text)
    if scroll is not None and rules.lookup(scroll.group(1), "2024", "2014", kind="spell"):
        return True
    return any(
        rules.lookup(candidate, "2024", "2014", kind=kind) is not None
        for candidate in {text, re.sub(r"(?i)^(\w+)s\b", r"\1", text, count=1)}
        for kind in _STANDARD_KINDS
    )


def judge(name: str, context: str = "") -> Verdict:
    """Is this item one particular thing? `context` is the words the item was said in (a quote, the
    cited lines): it only ever adds a reason to keep, through the story-significance test."""
    text = " ".join(name.split())
    words = _core(text)
    if not words:
        return Verdict(False, "empty")
    standard = standard_item(text)
    if _proper(words) and not standard:
        return Verdict(True, "name")
    owner_thing = _POSSESSIVE.match(text.replace("’", "'"))
    if owner_thing is not None:
        thing = owner_thing.group(2).split()
        if thing and not _plural_head(thing) and not _QUANTITY.search(owner_thing.group(2)):
            return Verdict(True, "owner")
    if not standard and _CLAUSE.search(text.replace("’", "'")) and not _QUANTITY.search(text):
        return Verdict(True, "description")
    context = _near(text, context)
    if _SIGNIFICANT.search(f"{text} {context}"):
        return Verdict(True, "story")
    if _marked_near(words, f"{text} {context}"):
        return Verdict(True, "marked")
    if standard:
        return Verdict(False, "standard")  # a rulebook item is ordinary unless a test above held it
    # A judgement of the signs left: keep when more likely than not one particular thing.
    plain = [w.casefold() for w in words]
    score = 0
    if _words(text)[0].casefold() == "the":
        score += 1  # "the Sealed Letter": a definite thing
    if len(plain) >= 3 and "of" in plain:
        score += 1  # "heart of chardalyn": a described kind with a specific stuff or place
    if any(w[:1].isupper() for w in words):
        score += 1
    if re.search(rf"\bthe\s+{re.escape(' '.join(plain))}\b", context.casefold()):
        score += 1  # the text speaks of it as THE one ("the psi crystal")
    if plain[-1] not in COMMON_KINDS and not _plural_head(words) and len(plain) <= 3:
        score += 1  # not everyday gear: a crystal, a figurine, a tome is more often one thing
    if _words(text)[0].casefold() in {"a", "an"}:
        score -= 1
    if _plural_head(words) or _QUANTITY.search(text):
        score -= 2
    if plain[-1] in COMMON_KINDS:
        score -= 1
    if all(w in COMMON_KINDS or w in _SMALL for w in plain):
        score -= 2
    return Verdict(score >= 2, "signs")


def keeps(name: str, context: str = "") -> bool:
    return judge(name, context).keep
