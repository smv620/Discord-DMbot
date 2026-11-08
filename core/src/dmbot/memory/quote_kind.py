"""The kind of a lore connection held to its own quote (#3780). Pure.

The readers keep a connection only when its quote holds both names, but which KIND it is was the
AI's pick, and the same text read twice can come back as two kinds (step 9b and step 10 of the
sample run read one document with one build). The AI's sampling can't be turned down (the models
refuse a non-default temperature), so a few readings the quote's own words rule out are corrected
or dropped here, the same way every time. Each rule is tied to the two names, so a word elsewhere
in the quote changes nothing:

- **A contract against someone is no service.** "She gives the party a contract to capture or
  eliminate Sephek Kaltro" came back as Hlin *works for* Sephek. A `serves` line whose quote has a
  contract, bounty or payment, then a word for ending someone, then one of the two names, is `knows`
  with the feeling "hostile" (the old `enemy_of`, #3175). "Works for the Bounty Hunters' Guild" or
  "an assassin who kills for the Zhentarim" have no such chain and stay as they are.
- **An apprentice is mentored.** A `serves` line whose quote calls its first name an apprentice
  ("rival apprentices Emmerich and Wit", "Wit, apprentice of Syllar") is "the master mentors the
  apprentice", the way `LORE_REVERSED` keeps "apprentice".
- **The kind the quote names.** The catch-all with words that read "a serves b" ("bodyguard for",
  "works for", "employed by", "hired by") is `serves`; `leads` for "the owner and proprietor of The
  Blue Clam" is `owns` (when no word for leading is there).
- **Knowing a fact is not knowing a person.** "The Brotherhood knows who you are" is no `knows`
  between the Brotherhood and Danneth, the other name in that note. Dropped only when every "know"
  in the quote is followed by a fact word and what is known holds neither name nor a pronoun that
  could be one, and never for a line still waiting for the who-is-it check.
- **An origin in an apposition belongs to the first name.** "Griz, son of Lee, an Ursine warrior
  from the Spine of the World": the place tie the AI put on Lee is Griz's. English allows either
  reading; the head of the phrase is the one the sample's key holds (Supervisor's #3780 cause note),
  and the tie moves only when its place is the one after "from".

Corrections are counted as `kind_*` in the read's `dropped` counts (kept lines, logged apart from
drops); the one real drop is counted as `knows_a_fact`. Counts only, never a word.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING

from dmbot.memory.models import name_key
from dmbot.memory.ontology import FALLBACK_PREDICATE

if TYPE_CHECKING:
    from dmbot.memory.background import Connection

NAME_CHARS = 80  # as background.NAME_CHARS (not imported: background imports this module)
END_WORDS = r"(?:eliminat\w*|kill\w*|captur\w*|murder\w*|assassinat\w*|slay|slain)"
CONTRACT = re.compile(
    rf"\b(?:contract\w*|bount(?:y|ies)|paid|pays|hired|hires|offer\w*)\b[^.;!?]{{0,40}}?\b{END_WORDS}\b",
    re.I,
)
SERVES_LABEL = re.compile(
    r"^(?:an?\s+|the\s+)?(?:(?:body)?guard|servant|employee|retainer)\s+(?:for|to|of)\b|"
    r"^works?\s+for\b|^employed\s+(?:by|as)\b|^hired\s+by\b|^serves?\b",
    re.I,
)
OWNER_OF = r"\b(?:owner|proprietor)\s+(?:and\s+\w+\s+)?of\s+(?:the\s+)?"
LEADER = re.compile(r"\b(?:lead\w*|captain|chief|head|commander|boss|rules?|ruler)\b", re.I)
KNOW = re.compile(r"\bkn(?:ow|ows|ew|own|owing)\b", re.I)
FACT = re.compile(r"\s+(?:who|what|where|when|why|how|that|whether|if)\b", re.I)
PRONOUN = re.compile(r"\b(?:he|she|him|her|they|them|it|his|hers|its|their)\b", re.I)
# "A, son of B, a/an … from …": the apposition after B describes A (the head of the phrase)
APPOSITION = re.compile(
    r"(?P<head>[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){0,3}),\s+(?:son|daughter|child)\s+of\s+"
    r"(?P<of>[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){0,3}),\s+an?\s+[^,.;]*?\bfrom\s+(?P<place>[^,.;]+)"
)
OPENERS = frozenset(
    {"originally", "formerly", "once", "recently", "later", "then", "now", "born", "in", "at", "on",
     "the", "a", "an", "and", "but", "when", "after", "before", "as",
     "sir", "lady", "lord", "master", "mistress", "dame", "king", "queen", "prince", "princess"}
)  # fmt: skip
FROM_WORDS = frozenset({"comes from"})  # (the one wording `place_words` keeps for an origin)


def _name(name: str) -> str:
    """A name as a pattern that matches it as whole words."""
    return rf"(?<!\w){re.escape(name)}(?!\w)"


def _has_name(text: str, *names: str) -> bool:
    """Whether `text` holds any of `names` as whole words (case aside)."""
    return any(n and re.search(_name(n), text, re.I) for n in names)


def _ends(made: Connection) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return (made.a, made.variant_a), (made.b, made.variant_b)


def _contract_against(quote: str, made: Connection) -> bool:
    """A contract, bounty or payment, then a word for ending someone, then one of the two names."""
    names = [n for end in _ends(made) for n in end if n]
    for hit in CONTRACT.finditer(quote):
        # (the name must be what is to be ended, "capture or eliminate Sephek Kaltro"; in "hired
        # Sephek to kill her rivals" the named one is the one hired, and that is service)
        after = quote[hit.end() :]
        if any(re.match(rf"\s+(?:or\s+\w+\s+)?(?:the\s+)?{_name(n)}", after, re.I) for n in names):
            return True
    return False


def _apprentice(quote: str, a: tuple[str, ...]) -> bool:
    """Whether the quote calls `a` an apprentice: "apprentices Emmerich and Wit" (the word, then
    the name within a short list) or "Wit, apprentice of …"."""
    for name in (n for n in a if n):
        # (the list straight after the word: "the apprentices of Syllar" names the master)
        if re.search(rf"\bapprentices?\s+(?!of\b|to\b)[^,.;!?]{{0,40}}?{_name(name)}", quote, re.I):
            return True
        if re.search(rf"{_name(name)},?\s+(?:an?\s+|the\s+)?apprentice\s+(?:of|to)\b", quote, re.I):
            return True
    return False


def _knows_a_fact(quote: str, made: Connection) -> bool:
    """Every "know" in the quote is followed by a fact word, and what is known (to the clause's
    end) holds neither name nor a pronoun that could stand for one."""
    hits = list(KNOW.finditer(quote))
    if not hits:
        return False
    names = [n for end in _ends(made) for n in end if n]
    for hit in hits:
        rest = quote[hit.end() :]
        if not FACT.match(rest):
            return False
        known = re.split(r"[.;!?]", rest, maxsplit=1)[0]
        if _has_name(known, *names) or PRONOUN.search(known):
            return False
    return True


def _head(words: str) -> str:
    """The name at the head of an apposition, without the words that only open the sentence
    ("Originally Griz" is Griz, "In Neverwinter Griz" is Griz... as far as the words show)."""
    parts = words.split()
    while len(parts) > 1 and parts[0].casefold() in OPENERS:
        parts = parts[1:]
    return " ".join(parts)


def held(made: Connection, lost: Counter[str], *, waiting: bool = False) -> Connection | None:
    """`made` with its kind held to its quote, or None when the quote rules it out. `waiting` is
    True for a line whose pronoun the who-is-it check has still to settle: it is never dropped."""
    quote = made.quote
    a, b = _ends(made)
    if made.how == FALLBACK_PREDICATE and SERVES_LABEL.search(made.words):
        lost["kind_named_service"] += 1
        made = replace(made, how="serves", words="")
    if made.how == "serves" and _contract_against(quote, made):
        lost["kind_contract_against"] += 1
        return replace(made, how="knows", words="hostile")  # (the chain itself is the cue)
    if made.how == "serves" and _apprentice(quote, a):
        lost["kind_apprentice"] += 1
        return replace(  # (a serves b: a is the apprentice, so b mentors a)
            made, how="mentor_of", a=made.b, b=made.a, variant_a=made.variant_b,
            variant_b=made.variant_a,
        )  # fmt: skip
    if made.how == "leads" and not LEADER.search(quote):
        owned = [n for n in b if n]
        if any(re.search(OWNER_OF + _name(n), quote, re.I) for n in owned):
            lost["kind_named_owner"] += 1
            return replace(made, how="owns")
    if made.how == "knows" and not waiting and _knows_a_fact(quote, made):
        lost["knows_a_fact"] += 1
        return None
    if made.how == "located_in" and made.words in FROM_WORDS:
        for hit in APPOSITION.finditer(quote):
            head = _head(hit.group("head"))
            if (
                _has_name(hit.group("of"), *a)
                and _has_name(hit.group("place"), *b)
                and len(head) <= NAME_CHARS
                and name_key(head) not in {name_key(made.b), name_key(made.a)}
            ):
                lost["kind_origin_moved"] += 1
                return replace(made, a=head, variant_a="")
    return made
