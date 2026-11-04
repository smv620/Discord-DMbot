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
lists are plain words, so the judgement can be read and argued with."""

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
_SMALL = frozenset({"a", "an", "the", "of", "from", "in", "on", "at", "for", "and", "to", "with"})
_QUANTITY = re.compile(
    r"\b(\d[\d,]*|two|three|four|five|six|seven|eight|nine|ten|several|some|many|pair of|"
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
    r"\b(stole|stolen|steal|steals|theft|deliver|delivered|delivery|consignment|shipment|"
    r"tribute|smuggl\w*|sought|seeking|seek|hunt(?:s|ed|ing)? for|quest|bounty|ransom|"
    r"fought over|fight over|race to|plot|plan|scheme|ritual|villain|meant for|intended for|"
    r"destined|forge[sd]?|everyone wants|wants it|after it|cursed|corrupt\w*|madness|whisper\w*)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Verdict:
    keep: bool
    why: str  # the test that decided, a fixed word (never the campaign's own words): for tests


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
    return head.endswith("s") and not head.endswith(("ss", "us", "is")) and len(head) > 3


def _proper(words: list[str]) -> bool:
    """A proper name: a capitalised word that is not merely the first word of a sentence-case
    phrase. A single capitalised word counts unless it is one of the common kinds."""
    if not words:
        return False
    if len(words) == 1:
        return words[0][:1].isupper() and words[0].casefold() not in COMMON_KINDS
    return any(w[:1].isupper() and w.casefold() not in _SMALL for w in words[1:])


def judge(name: str, context: str = "") -> Verdict:
    """Is this item one particular thing? `context` is the words the item was said in (a quote, the
    cited lines): it only ever adds a reason to keep, through the story-significance test."""
    text = " ".join(name.split())
    words = _core(text)
    if not words:
        return Verdict(False, "empty")
    if _proper(words):
        return Verdict(True, "name")
    owner_thing = _POSSESSIVE.match(text.replace("’", "'"))
    if owner_thing is not None:
        thing = owner_thing.group(2).split()
        if thing and not _plural_head(thing) and not _QUANTITY.search(owner_thing.group(2)):
            return Verdict(True, "owner")
    if _CLAUSE.search(text.replace("’", "'")) and not _QUANTITY.search(text):
        return Verdict(True, "description")
    if _SIGNIFICANT.search(f"{text} {context}"):
        return Verdict(True, "story")
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
