"""The kind of a lore connection held to its own quote (#3780). Pure.

The readers keep a connection only when its quote holds both names, but which KIND it is was the
AI's pick, and the same text read twice can come back as two kinds (step 9b and step 10 of the
sample run read one document with one build). The AI's sampling can't be turned down (the models
refuse a non-default temperature), so a few readings the quote's own words rule out are corrected
or dropped here, the same way every time:

- **A contract against someone is no service.** "She gives the party a contract to capture or
  eliminate Sephek" came back as Hlin *works for* Sephek. A `serves` line whose quote is about
  eliminating, killing, capturing, hunting or murdering is `knows` with the feeling "hostile" (the
  old `enemy_of`, #3175).
- **An apprentice is mentored.** A `serves` line whose quote calls one of them an apprentice is
  "the master mentors the apprentice", the way `LORE_REVERSED` keeps "apprentice".
- **The kind the quote names.** The catch-all with the words "bodyguard for" ("employed as a
  bodyguard for Torrga") is `serves`; `leads` for "the owner and proprietor of The Blue Clam" is
  `owns`.
- **Knowing a fact is not knowing a person.** "The Brotherhood knows who you are" is no `knows`
  between the Brotherhood and Danneth, the other name in that note.
- **An origin in an apposition belongs to the first name.** "Griz, son of Lee, an Ursine warrior
  from the Spine of the World": the place tie the AI put on Lee is Griz's.

Each change is counted in the read's `dropped` counts (counts only, never a word). A connection
none of these touch comes back unchanged.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING

from dmbot.memory import reputation_words as feeling_words
from dmbot.memory.ontology import FALLBACK_PREDICATE
from dmbot.memory.scan import COMMON

if TYPE_CHECKING:
    from dmbot.memory.background import Connection

HOSTILE = re.compile(
    r"\b(eliminat\w*|kill\w*|captur\w*|hunt\w*|murder\w*|assassinat\w*|slay\w*|bount(y|ies))\b",
    re.I,
)
APPRENTICE = re.compile(r"\bapprentices?\b", re.I)
SERVICE_WORDS = re.compile(r"\b(bodyguard|employed|employee|hired|servant|works for)\b", re.I)
OWNER = re.compile(r"\b(owner|proprietor|owns)\b", re.I)
LEADER = re.compile(r"\b(lead\w*|captain|chief|head|commander|boss)\b", re.I)
KNOWS_A_FACT = re.compile(r"\bkn(?:ows?|ew)\s+(who|what|where|when|why|how|that)\b", re.I)
KNOWS = re.compile(r"\bkn(?:ows?|ew)\s+(\S+(?:\s+\S+){0,3})", re.I)
# "A, son of B, a/an … from …": the apposition after B describes A (the head of the phrase)
APPOSITION = re.compile(
    r"(?P<head>[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*)*),\s+(?:son|daughter|child)\s+of\s+"
    r"(?P<of>[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*)*),\s+an?\s+[^,.;]*?\bfrom\b"
)
FROM_WORDS = frozenset({"comes from", "is from", "from", "born in"})


def _head(words: str) -> str:
    """The name at the head of an apposition, without a capitalised word that only opens the
    sentence ("Originally Griz" is Griz; "The" and "-ly" words are no part of a name here)."""
    parts = words.split()
    while len(parts) > 1 and (parts[0].casefold() in COMMON or parts[0].casefold().endswith("ly")):
        parts = parts[1:]
    return " ".join(parts)


def _has_name(text: str, name: str) -> bool:
    return bool(name) and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.I) is not None


def held(made: Connection, lost: Counter[str]) -> Connection | None:
    """`made` with its kind held to its quote, or None when the quote rules it out."""
    quote = made.quote
    if made.how == "serves" and HOSTILE.search(quote):
        feeling, _stepped = feeling_words.held_to_the_quote("hostile", quote)
        label = "" if feeling in (None, feeling_words.NEUTRAL) else feeling
        lost["kind_contract_against"] += 1
        return replace(made, how="knows", words=label)
    if made.how == "serves" and APPRENTICE.search(quote):
        lost["kind_apprentice"] += 1
        return replace(  # (a serves b: a is the apprentice, so b mentors a)
            made, how="mentor_of", a=made.b, b=made.a, variant_a=made.variant_b,
            variant_b=made.variant_a,
        )  # fmt: skip
    if made.how == FALLBACK_PREDICATE and SERVICE_WORDS.search(made.words):
        lost["kind_named_service"] += 1
        return replace(made, how="serves", words="")
    if made.how == "leads" and OWNER.search(quote) and not LEADER.search(quote):
        lost["kind_named_owner"] += 1
        return replace(made, how="owns")
    if made.how == "knows" and KNOWS_A_FACT.search(quote):
        objects = [m.group(1) for m in KNOWS.finditer(quote)]
        names = [n for n in (made.b, made.variant_b, made.a, made.variant_a) if n]
        if not any(_has_name(obj, n) for obj in objects for n in names):
            lost["kind_knows_a_fact"] += 1
            return None
    if made.how == "located_in" and made.words in FROM_WORDS:
        for hit in APPOSITION.finditer(quote):
            head, of = _head(hit.group("head")), hit.group("of")
            if _has_name(of, made.a) or (made.variant_a and _has_name(of, made.variant_a)):
                lost["kind_origin_moved"] += 1
                return replace(made, a=head, variant_a="")
    return made
