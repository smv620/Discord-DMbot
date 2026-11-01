"""Names from a DM's own campaign background (#2167; docs/PLAN.md, "Names at scale").

The DM gives DMbot their story, notes or backstory; the AI lists the proper names in it, and each
becomes a *suggestion* in 📝 Check new names with one short quote as evidence. Nothing is confirmed
without the DM. Pure: building the request, reading and checking the answer, merging the pieces.
The AI call itself is in the caller.

The background is the DM's own writing, read once; the text is never kept. Only the names the DM
says yes to, and the quote until the name is decided, are stored. A published adventure goes the
other way (the Shared story switch, with its rights confirmation).

The answer is untrusted: a name or quote that is not in the text is dropped, so DMbot never invents
a name or a quote.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from dmbot.memory import kin
from dmbot.memory import reputation_words as feeling_words
from dmbot.memory.end_match import FILLER, SHORTEST, Ends
from dmbot.memory.models import name_key
from dmbot.memory.ontology import DISGUISED_AS, FALLBACK_PREDICATE, SYNONYMS
from dmbot.memory.pronouns import PRONOUNS, speaker_end
from dmbot.memory.scan import COMMON, GAME_TERMS
from dmbot.names import not_names
from dmbot.word_end import at_word_end

MAX_NAMES = 200  # suggestions from one document
NAME_WORDS = 8
NAME_CHARS = 80
QUOTE_WORDS = 20
MAX_ALSO = 4
# What the AI calls a kind, and the kind DMbot keeps it as (the same words Add many reads).
KINDS = {
    "person": "npc",  # (the AI's word for any named character; a named monster is one too)
    "place": "place",
    "group": "faction",
    "god": "npc",  # a god is an NPC (#2562)
    "item": "item",
    "event": "event",
    "idea": "concept",
}
LABELS = {  # for "Yes to all 14 places"
    "npc": ("NPC", "NPCs"),
    "place": ("place", "places"),
    "faction": ("group", "groups"),
    "item": ("item", "items"),
    "event": ("event", "events"),
    "concept": ("idea", "ideas"),
}
MAX_CONNECTIONS = 200  # suggestions from one document
# What the AI calls a connection, and the relationship DMbot keeps it as. "a <word> b" reads from
# the first name to the second ("Ulfgar" member "Frostwolf tribe").
CONNECTIONS = {
    "family": "kin_of",
    "member": "member_of",
    "in": "located_in",
    # ("friend" and "enemy" are read as knows with a feeling before this table is looked at, #3175:
    # these two entries only keep the words understood; the readers no longer produce ally_of or
    # enemy_of, and rows stored earlier stay as they are)
    "friend": "ally_of",
    "enemy": "enemy_of",
    "owns": "owns",
    "works for": "serves",
    "knows": "knows",
    "sworn to": "sworn_to",  # an oath, vow, pact or fealty
    # Kinds of their own (#3033, #3035): they used to be kept on the catch-all with fixed words.
    "mentor": "mentor_of",
    "rival": "rival_of",
    "leads": "leads",
    "founded": "founded",
    "captive": "captive_of",  # a is held captive by b
    "contains": "contains",  # a contains b
    # A second character posing as another (a doppelganger, an impostor): two people, never a name
    # of one (#3091). A secret identity of ONE person stays a secret name; this is a connection.
    "disguised as": "disguised_as",  # a is disguised as b
}
# The relationships lore is mostly made of (#2757), built in so the AI never has to word them
# itself (a free label may use only the quote's own words, which "mentors" or "is a rival of"
# rarely are). `serves` and `ally` are kinds DMbot keeps natively; the rest are kept on the
# catch-all relationship with these fixed words as the label, which read from the first name to
# the second ("a mentors b"). Plain words for a DM: nothing here is a technical term.
LORE_KEPT = {"serves": "serves", "ally": "ally_of"}
# Friend, ally and enemy are no kinds any more (#3175): two characters KNOW each other, and the
# feeling is a reputation word. An AI that still says one of these old words is read as knows with
# the feeling it stood for, so no line is lost; they are no longer offered to it.
LEGACY_FEELING = {"friend": "friendly", "ally": "friendly", "enemy": "hostile"}
LORE_WORDS = {
    "worships": "worships",
}
# Family by role: kept as a family connection whose detail is the role (#2457), read "a is the
# parent of b" (the role belongs to the first name, as `kin` reads it).
LORE_KIN = ("parent", "sibling", "spouse", "grandparent", "aunt", "uncle", "niece", "nephew",
            "cousin")  # fmt: skip
# The role words a text uses, in the neutral word DMbot keeps (the gender stays out of the data):
# "his younger sister" is a sibling, "whose husband" a spouse (#3035). A word of `kin.ROLES` that is
# neutral already (aunt, cousin…) stands for itself.
KIN_NEUTRAL = {
    "mother": "parent", "father": "parent", "son": "child", "daughter": "child",
    "sister": "sibling", "brother": "sibling", "wife": "spouse", "husband": "spouse",
    "grandmother": "grandparent", "grandfather": "grandparent",
    "granddaughter": "grandchild", "grandson": "grandchild",
}  # fmt: skip
# "a is an apprentice of b" is "b mentors a", and "a is a child of b" is "b is the parent of a":
# kept that way round, so the two ways of saying it are one fact and are never offered twice.
LORE_REVERSED = {"apprentice": "mentor", "child": "parent", "grandchild": "grandparent"}
ALL_WORDS = (*CONNECTIONS, *LORE_KEPT, *LORE_WORDS, *LORE_KIN, *LORE_REVERSED)  # `how` answers
# What the AI is offered: the old feeling words are still understood but not asked for (#3175).
OFFERED_WORDS = tuple(w for w in ALL_WORDS if w not in LEGACY_FEELING)
MAX_OWN_KINDS = 20  # the campaign's own relationships offered to the AI as well
# A stated relationship no fixed kind and no campaign term fits (#2454): kept on the catch-all
# relationship, with the words the text used as its label ("pays", "sworn on the ashes of").
OTHER = "other"
WORDS_CHARS = 40


def schema(own: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The answer's shape; the campaign's own relationships are allowed kinds too."""
    return {
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": list(KINDS)},
                        "also": {"type": "array", "items": {"type": "string"}},
                        "secret": {"type": "array", "items": {"type": "string"}},
                        "quote": {"type": "string"},
                    },
                    "required": ["name", "kind", "also", "secret", "quote"],
                    "additionalProperties": False,
                },
            },
            "connections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "string"},
                        "how": {
                            "type": "string",
                            "enum": list(
                                dict.fromkeys([*OFFERED_WORDS, *(own or {}), OTHER])
                            ),  # (not the old feeling words)
                        },
                        "words": {"type": "string"},
                        "b": {"type": "string"},
                        "quote": {"type": "string"},
                    },
                    "required": ["a", "how", "words", "b", "quote"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["names", "connections"],
        "additionalProperties": False,
    }


SCHEMA = schema()


@dataclass(frozen=True, slots=True)
class Found:
    name: str
    kind: str  # the stored kind (npc, place, faction…)
    also: tuple[str, ...]
    secret: tuple[str, ...]  # disguises and secret identities: never a public name
    quote: str  # at most QUOTE_WORDS words, as written; "" when the AI's quote was not in the text


@dataclass(frozen=True, slots=True)
class Connection:
    a: str  # a name as written in the text
    how: str  # the relationship (a key of CONNECTIONS.values())
    b: str
    quote: str  # a line of the text that holds both names, as written
    secret: bool = False  # an end was found through a secret spelling: it is kept as secret only
    # The label of the catch-all (`how` is then FALLBACK_PREDICATE), or the family role of a
    # `kin_of` ("parent"), else "".
    words: str = ""
    # The spellings the TEXT used for an end the AI wrote another way (#2757), by end: checked
    # against the campaign's names when the list is kept. A variant that is another name than its
    # own end is no variant, and the line is dropped.
    variant_a: str = ""
    variant_b: str = ""
    decided: str = ""  # how a pronoun was settled (#2790), kept with the waiting connection


@dataclass(frozen=True, slots=True)
class Pick:
    """A connection with one end that fits two or more names equally ("Mara"): it waits for the
    DM to pick (#2650). `fixed` is the end that is a name, `fixed_is_a` whether it is the first
    end; `word` is the other end as written and `candidates` the names it may be."""

    fixed: str
    fixed_is_a: bool
    word: str
    candidates: tuple[str, ...]
    how: str
    quote: str
    secret: bool = False
    words: str = ""
    decided: str = ""  # how the pronoun was checked (#2790), else ""


@dataclass(frozen=True, slots=True)
class Unsettled:
    """A connection whose quote has one pronoun ("she") and holds only one of its two names, where
    the sentence before did not settle who the pronoun means (#2790). It is not dropped when the
    who-is-it check is on: `who_resolve.settle` asks the AI who it is. `missing_is_a` says which end
    the quote lacks, `phrase` is the pronoun as written and `passage` the text up to the end of the
    quote, word for word."""

    made: Connection
    missing_is_a: bool
    phrase: str
    passage: str


MAX_PICK_CHOICES = 3  # the card has a button for each; more is left out as `which_one`
MAX_PICKS = 50  # "Which Mara?" questions from one reading


def cost_text(cents: int) -> str:
    """ "about 3 cents", "about $4.01": dollars once it is a dollar or more."""
    if cents >= 100:
        return f"about ${cents / 100:.2f}"
    return f"about {cents} cent{'' if cents == 1 else 's'}"


def estimate_cents(chars: int) -> int:
    """About what reading `chars` characters costs, rounded up to a whole cent and never below
    one: roughly 4 characters a token. Pass 1 (the careful model reads the text and writes a
    list up to an eighth as long) and pass 2 (the fast model reshapes the list), at the list
    prices of `ai.PRICE_PER_MILLION`, with a margin for the retries."""
    from dmbot.ai import DEFAULT_MODELS, DEFAULT_PRICE, PRICE_PER_MILLION

    careful_in, careful_out = PRICE_PER_MILLION.get(DEFAULT_MODELS.careful, DEFAULT_PRICE)
    fast_in, fast_out = PRICE_PER_MILLION.get(DEFAULT_MODELS.fast, DEFAULT_PRICE)
    tokens_in = chars / 4
    listed = max(600.0, tokens_in / 8)  # the plain list, at its fullest (a quote on every line)
    shaped = listed * 1.5  # the same list as JSON
    # The text goes in three times over: a retry or a continuation sends it again with the list so
    # far, and the second look at names with no relationship (#2757) sends it once more.
    cost = tokens_in * 3 * careful_in + listed * careful_out + listed * fast_in + shaped * fast_out
    cents = cost / 1_000_000 * 100 * 1.5
    return max(1, int(cents) + (1 if cents % 1 else 0))


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _json(reply: str) -> object:
    text = reply.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                return None
    return None


def _has(haystack: str, text: str) -> bool:
    """Is `text` in the (squashed) haystack as whole words? "Ann" is not in "annual"."""
    return re.search(rf"(?<!\w){re.escape(_squash(text))}(?!\w)", haystack) is not None


# What may stand between a first name and a surname it shares (#2841): a list joiner or a bracketed
# aside, and around them only more asides, joiners or capitalised first names. "Brannoc (father) &
# Mira (mother) Ashgrove" holds "Brannoc Ashgrove"; "Mira spoke with Ken Ashgrove" does not hold
# "Mira Ashgrove".
_ASIDE = r"\([^)\n]*\)"
_JOIN = rf"(?:{_ASIDE}|&|,|\band\b|\bor\b)"
_FILL = rf"(?:{_JOIN}|[A-Z][\w'’-]*)"
_SPLIT_GAP = rf"\s*(?:{_FILL}\s*)*?{_JOIN}\s*(?:{_FILL}\s*)*"


def _in_text(haystack: str, name: str, piece: str | None = None) -> bool:
    """Whether the (squashed) text holds this name: whole, or (a surname written once for two
    people, "Brannoc (father) & Mira (mother) Ashgrove", #2841) with its words in order across a
    list joiner or bracketed aside in `piece`, the text as written (capitals tell a first name from
    other words). Only for a name of two or more words, each of some length."""
    if _has(haystack, name):
        return True
    words = name.split()
    if piece is None or len(words) < 2 or any(len(re.sub(r"\W", "", w)) < SHORTEST for w in words):
        return False
    glue = rf"(?:\s+|{_SPLIT_GAP})"
    pattern = glue.join(rf"(?<!\w)(?i:{re.escape(w)})(?!\w)" for w in words)
    return re.search(pattern, piece) is not None


def names_in(text: str, names: Iterable[str]) -> list[str]:
    """Which of these names the text holds as whole words, in the order given."""
    haystack = _squash(text)
    return [n for n in names if _has(haystack, n)]


def quote_holds(quote: str, end: str) -> bool:
    """Whether a quote (as written) holds this end, as `read_connections` asks it."""
    return _in_quote(_squash(quote), end)


def _in_quote(quote: str, end: str) -> bool:
    """Whether the quote holds this end: the name whole, or (the quote says "my brother Ulric" and
    the end is the listed "Ulric Stonehand", #2642) a word of it that is a name's own, not a title
    or a short word. The name itself must stand in the text (checked by the caller)."""
    if _has(quote, end):
        return True
    return any(
        len(word) >= SHORTEST and word not in FILLER and _has(quote, word)
        for word in name_key(end).split()
    )


VARIANT_MIN = 5  # letters a name needs before a spelling one edit away may stand for it


def _one_edit(a: str, b: str) -> bool:
    """Whether two strings differ by exactly one inserted, removed or changed letter."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        return a[i + 1 :] == b[i + 1 :]
    return a[i:] == b[i + 1 :]


def _variant(haystack: str, name: str, cache: dict[str, str | None]) -> str | None:
    """The one spelling in the (squashed) text that is a single letter away from this name, for a
    name of `VARIANT_MIN` letters or more (#2757): a document that spells a name two ways, and an
    AI that writes the other one. None when there is no such spelling or when there are two (which
    one is meant would be a guess). The caller checks the spelling is no other name of the
    campaign; this only finds it."""
    key = _squash(name)
    if key in cache:
        return cache[key]
    found: set[str] = set()
    if len(re.sub(r"\W", "", key)) >= VARIANT_MIN:
        count = len(key.split())
        tokens = re.findall(r"\w+(?:['’-]\w+)*", haystack)
        for i in range(len(tokens) - count + 1):
            candidate = " ".join(tokens[i : i + count])
            if _one_edit(candidate, key):
                found.add(candidate)
    cache[key] = next(iter(found)) if len(found) == 1 else None
    return cache[key]


# The forms of one pronoun, as one person each (he/him/his/himself): a quote with two forms of one
# pronoun still holds one person to settle, not two (#2841).
_PERSON = {
    **dict.fromkeys(("he", "him", "his", "himself"), "he"),
    **dict.fromkeys(("she", "her", "hers", "herself"), "she"),
    **dict.fromkeys(("they", "them", "their", "theirs", "themselves"), "they"),
    **dict.fromkeys(("who", "whose", "whom"), "who"),
}

_SENTENCE_END = re.compile(r"[.!?]+[\"'”’)\]]*\s+")
# Every way a document ends a paragraph or a line, not only "\n" (a pasted text may use the
# Unicode separators).
_BREAK = re.compile(r"[\n\r\u2028\u2029\x85]")


MIN_BORROWED_WORDS = 3  # the paragraph before must end in at least this many words to lend
LONG_QUOTE_WORDS = 2 * QUOTE_WORDS  # a widened quote's most, when 20 cannot hold both ends


def _widen(
    piece: str,
    words: list[str],
    a: str,
    b: str,
    holds: Callable[[str, str], bool] | None = None,
    lost: Dropped | None = None,
) -> list[str] | None:
    """A quote that holds only one of the two names (the sentence says "his sister", #2757) made
    the sentence before it too: the text from the start of the previous sentence to the end of the
    quote, word for word as written. Only for a quote with a pronoun in it ("his", "she", "who").
    The AI's whole quote is always in the result, and it is kept only when the window holds BOTH
    names, so "both names are in the quoted words" stays true without trusting the AI's reading of
    a pronoun. (A trade-off: a window with both names does not prove which one the pronoun means;
    the DM confirms every suggestion before it is kept.) A quote that runs across a line break is
    not looked at.

    The window keeps the FRONT of the earlier sentence, where its name is, and runs to the quote's
    end: up to `QUOTE_WORDS` words, and up to `LONG_QUOTE_WORDS` when 20 cannot hold both names
    (a list sentence "A mentored two apprentices: B... as well as C", #2841). A quote that opens
    its paragraph borrows the last sentence of the paragraph before it. `lost` counts how often
    each of these was needed (`quote_long`, `quote_prev_paragraph`), never a word. None when no
    place in the text gives such a window."""
    if not any(w.strip(".,;:!?\"'“”‘’()").casefold() in PRONOUNS for w in words):
        return None  # (no pronoun: the quote names what it names, and nothing is borrowed)
    holds = holds or _in_quote
    pattern = r"\s+".join(re.escape(w) for w in words)
    for hit in re.finditer(pattern, piece, re.I):  # (a quote may stand in the text more than once)
        start, end = hit.start(), hit.end()
        if _BREAK.search(piece[start:end]):
            continue
        breaks = list(_BREAK.finditer(piece, 0, start))
        paragraph = breaks[-1].end() if breaks else 0
        before = piece[paragraph:start]
        starts = [0] + [m.end() for m in _SENTENCE_END.finditer(before)]
        cut = starts[-2] if len(starts) >= 2 else 0
        origin = paragraph + cut
        borrowed = False
        if not before.strip() and breaks:  # the quote opens its paragraph: the one before ends here
            upto = len(piece[:paragraph].rstrip("\n\r\u2028\u2029\x85 "))  # (blank lines skipped)
            earlier = list(_BREAK.finditer(piece, 0, upto))
            before_par = earlier[-1].end() if earlier else 0
            tail = piece[before_par:upto]
            ends = [m.end() for m in _SENTENCE_END.finditer(tail)]
            origin = before_par + (ends[-1] if ends else 0)
            # (a heading of a word or two is no sentence to borrow)
            borrowed = len(piece[origin:upto].split()) >= MIN_BORROWED_WORDS
            if not borrowed:
                origin = paragraph
        tokens = piece[origin:end].split()
        for limit in (QUOTE_WORDS, LONG_QUOTE_WORDS):
            window = tokens[-limit:]  # (the quote's end is always in it; the front is cut last)
            said = _squash(" ".join(window))
            if holds(said, a) and holds(said, b):
                if lost is not None:
                    if len(window) > QUOTE_WORDS:
                        lost["quote_long"] += 1
                    if borrowed and len(tokens) - len(window) < len(piece[origin:upto].split()):
                        lost["quote_prev_paragraph"] += 1  # (the window really reaches into it)
                return window
    return None


def _spellings(
    raw: object, name: str, haystack: str, *, skip: set[str], cued: frozenset[str]
) -> list[str]:
    """The other spellings in one list the text really holds, each once, not the name itself."""
    out: list[str] = []
    for other in raw if isinstance(raw, list) else []:
        if not isinstance(other, str):
            continue
        other = " ".join(other.split())
        key = name_key(other)
        if (
            other
            and len(other) <= NAME_CHARS  # (a name is never shortened: a cut one is another name)
            and "|" not in other
            and key != name_key(name)
            and key not in skip
            and _has(haystack, other)
            and not_names.allowed(other, cued)
        ):
            skip.add(key)
            out.append(other)
    return out


def read_answer(reply: str, piece: str) -> list[Found]:
    """The names in one answer that are really in `piece`. A name, other spelling or quote the
    text does not hold (as whole words) is dropped (a quote alone: the name stays, without it)."""
    data = _json(reply)
    items = data.get("names") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    haystack = _squash(piece)
    cued = not_names.cued_in(piece)  # a pronoun is a name only where the text says so (#2358)
    out: list[Found] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        kind = KINDS.get(str(item.get("kind", "")).strip().lower())
        if not isinstance(name, str) or kind is None:
            continue
        # A name is never shortened (#2607): over the limits it is a sentence, not a name.
        name = " ".join(name.split())
        if (
            not name
            or len(name) > NAME_CHARS
            or len(name.split()) > NAME_WORDS
            or "|" in name
            or not _in_text(haystack, name, piece)
            or not not_names.allowed(name, cued)
        ):
            continue
        # A disguise is read first, so the same spelling is never also a public one.
        seen: set[str] = set()
        secret = _spellings(item.get("secret"), name, haystack, skip=seen, cued=cued)
        also = _spellings(item.get("also"), name, haystack, skip=seen, cued=cued)
        quote = item.get("quote")
        kept = ""
        if isinstance(quote, str):
            words = quote.split()[:QUOTE_WORDS]  # the front of a verbatim quote is still verbatim
            if words and _has(haystack, " ".join(words)):
                kept = " ".join(words)
        out.append(Found(name, kind, tuple(also[:MAX_ALSO]), tuple(secret[:MAX_ALSO]), kept))
    return out


def in_the_rules(name: str, kind: str, target: str, fallback: str) -> bool:
    """Is this a kind of monster, or a spell, in the rules? Those belong to the rules, not to the
    campaign (a proper name like "Snot" is not a monster). A person counts as a monster when the
    rules hold that name ("Owlbear"). A spell name is only looked for among ideas and items: a
    place called "Sanctuary" or a person called "Wish" is a real name."""
    from dmbot.rules import index  # late: the rules data loads on first use

    rules = index.srd()
    if kind == "npc" and rules.is_monster(name):
        return True
    if kind in ("concept", "item"):
        return rules.lookup(name, target, fallback, kind="spell") is not None
    return False


def merge(
    answers: Iterable[list[Found]],
    skip_keys: Collection[str] = (),
    *,
    cap: int = MAX_NAMES,
    rules: tuple[str, str] | None = None,
) -> list[Found]:
    """The pieces' names as one list, each once (other spellings joined, the first quote kept),
    leaving out names DMbot already has, ordinary words and game terms, at most `cap`. A name
    that is a disguise of another entry folds into it, and its other spellings become that
    entry's secret spellings. `rules`
    (target, fallback ruleset): names the rules hold as a monster or spell are left out too."""
    skip = {name_key(k) for k in skip_keys} | COMMON | GAME_TERMS
    order: list[str] = []
    merged: dict[str, Found] = {}
    for found in answers:
        for item in found:
            key = name_key(item.name)
            if not key or key in skip:
                continue
            if rules is not None and in_the_rules(item.name, item.kind, *rules):
                continue
            item = Found(  # a spelling DMbot already holds (a secret one too) is not offered again
                item.name,
                item.kind,
                tuple(a for a in item.also if name_key(a) not in skip),
                tuple(a for a in item.secret if name_key(a) not in skip),
                item.quote,
            )
            old = merged.get(key)
            if old is None:
                order.append(key)
                merged[key] = item
                continue
            seen = {name_key(x) for x in (*old.also, *old.secret)}
            more = tuple(x for x in item.also if name_key(x) not in seen)
            more_secret = tuple(x for x in item.secret if name_key(x) not in seen)
            merged[key] = Found(
                old.name,
                old.kind,
                (*old.also, *more)[:MAX_ALSO],
                (*old.secret, *more_secret)[:MAX_ALSO],
                old.quote or item.quote,
            )
    out = [merged[k] for k in order]
    # A disguise is a secret spelling of the real one. When the AI also lists the disguise as a
    # name of its own (as it is told to), that entry folds into the real one's secret spelling:
    # never a second, public entry. An entry folds only into one that is itself kept: of two that
    # hold each other, the first stays, and a chain folds from the top.
    holder: dict[str, str] = {}
    for f in out:
        for a in f.secret:
            holder.setdefault(name_key(a), name_key(f.name))
    place = {name_key(f.name): i for i, f in enumerate(out)}
    # Walk down from the entries nobody holds. An entry whose holder is kept folds into it; one
    # whose holder was folded stays (its holder is not there to keep it), and so on down. A loop
    # with no top (each holds the next) keeps its earliest entry and walks from there, so every
    # name is either an entry or a kept entry's secret. (In a chain, a folded entry's own secret
    # becomes a public entry rather than moving under the top one: allowed, nothing is lost.)
    children: dict[str, list[str]] = {}
    for key, above in holder.items():
        if above != key and above in place and key in place:
            children.setdefault(above, []).append(key)
    kept_keys: dict[str, bool] = {}

    def settle(start: str, keep: bool) -> None:
        stack = [(start, keep)]
        while stack:
            key, is_kept = stack.pop()
            if key in kept_keys:
                continue
            kept_keys[key] = is_kept
            for child in children.get(key, ()):
                stack.append((child, not is_kept))

    for key in place:
        if holder.get(key) is None or holder[key] == key or holder[key] not in place:
            settle(key, True)  # nobody holds it
    for key in place:  # what is left hangs in a loop: the earliest stays
        settle(key, True)
    # The cap counts the kept entries only: a folded disguise takes no slot, so it can never
    # cost its holder one. A holder that is cut takes its folded disguise's spellings with it.
    folded = [f for f in out if not kept_keys[name_key(f.name)]]
    out = [f for f in out if kept_keys[name_key(f.name)]][:cap]
    # A folded entry's other spellings (its secret ones too) name the same disguise, so they join
    # its holder as secret spellings, never public ones and never another entry's own name:
    # "Brother Aldric also [Aldric]" folded into Vecnor leaves Vecnor holding "Aldric" in secret.
    # A loop is allowed to leave a kept entry as another kept entry's secret too (Vecnor is an
    # entry and Mira's secret): every name is then somewhere, and nothing is lost to the fold.
    extra: dict[str, list[str]] = {}
    for gone in folded:
        top = holder.get(name_key(gone.name))
        if top is not None:
            extra.setdefault(top, []).extend((*gone.also, *gone.secret))
    joined: list[Found] = []
    entry_names = {name_key(f.name) for f in out}  # another entry's own name stays that entry's
    for f in out:
        have = {name_key(x) for x in (f.name, *f.also, *f.secret)} | entry_names
        carried: list[str] = []
        for a in extra.get(name_key(f.name), ()):
            if name_key(a) not in have and name_key(a) not in skip:
                have.add(name_key(a))
                carried.append(a)
        joined.append(
            Found(f.name, f.kind, f.also, (*f.secret, *carried)[:MAX_ALSO], f.quote)
            if carried
            else f
        )
    out = joined
    # An other spelling that is another name's own name is that name's, not an alias; and a
    # spelling a name holds as secret is never also public.
    names = {name_key(f.name) for f in out}
    out = [
        Found(
            f.name,
            f.kind,
            tuple(
                a
                for a in f.also
                if name_key(a) not in names and name_key(a) not in {name_key(x) for x in f.secret}
            ),
            f.secret,
            f.quote,
        )
        for f in out
    ]
    return out


def counts_by_kind(found: Iterable[Found]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in found:
        out[item.kind] = out.get(item.kind, 0) + 1
    return out


# Why a connection line was dropped, as counts for the log (#2563): never a name or a word of the
# text. `unreadable`: not a connection object; `word_not_allowed`: a relationship word that is
# neither a built-in, a campaign one nor a catch-all with the quote's own words; `bad_names`: an
# empty, too long or doubled name, or a quote of no words or too many; `not_in_text`: a name or
# the quote is not in the text, or the quote does not hold both names; `name_not_found`: an end is
# not a name DMbot will have; `same_pair`: the pair was already offered; `already_have`: the
# campaign has it; `over_cap`: past the most one reading offers.
Dropped = Counter[str]


def _quote_window(
    words: list[str], a: str, b: str, holds: Callable[[str, str], bool] | None = None
) -> list[str]:
    """A quote of more than `QUOTE_WORDS` words cut to that many (#2601): the front, or the back
    when the front loses one of the two names. Words of a verbatim quote stay verbatim; the check
    that the text holds the quote and both names still follows, so this only shortens. `holds`
    says whether a squashed quote holds an end (by any of its spellings, #2841)."""
    if len(words) <= QUOTE_WORDS:
        return words
    holds = holds or _has
    for window in (words[:QUOTE_WORDS], words[-QUOTE_WORDS:]):
        said = _squash(" ".join(window))
        if holds(said, a) and holds(said, b):
            return window
    return words[:QUOTE_WORDS]


def _groups(aliases: Mapping[str, Sequence[str]]) -> dict[str, set[str]]:
    """For each name key of an entry (its main name and every spelling), the other keys of the same
    entry (#2841): the campaign's confirmed aliases and the spellings this read listed. A spelling
    two entries both claim belongs to neither (it would be a guess which one is meant)."""
    claimed: dict[str, set[str]] = {}
    mains = {name_key(m) for m in aliases}
    for main, spellings in aliases.items():
        for sp in spellings:
            if name_key(sp) in mains and name_key(sp) != name_key(main):
                continue  # (another entry's own name is never this one's spelling)
            claimed.setdefault(name_key(sp), set()).add(name_key(main))
    out: dict[str, set[str]] = {}
    for main, spellings in aliases.items():
        mk = name_key(main)
        members = {mk} | {
            name_key(sp) for sp in spellings if len(claimed.get(name_key(sp), ())) == 1
        }
        for m in members:
            out.setdefault(m, set()).update(members - {m})
    return out


def read_connections(
    reply: str,
    piece: str,
    own: Collection[str] = (),
    dropped: Dropped | None = None,
    aliases: Mapping[str, Sequence[str]] | None = None,
    unsettled: list[Unsettled] | None = None,
) -> list[Connection]:
    """The connections in one answer that the text really holds: both names and the quote are in
    `piece` as whole words and the quote holds both names, so DMbot never invents a link. The
    names are checked against the merged list later (`keep_connections`). `dropped` counts why
    each line that failed did. `aliases` (a name key of the campaign to its confirmed public alias
    keys, each fitting that one entry only) lets a text that uses only an alias back the canonical
    name the AI wrote (#2762). A read's own spellings, secret ones too, are in it for this check
    alone (#2841): a connection found through a secret one is kept secret (`keep_connections`).
    `unsettled` (#2790): when given, a connection whose pronoun the sentence before did not settle
    is added there instead of being dropped as `not_in_text`, for the caller to settle; the caller
    then counts what became of each (the counts add up to the lines found)."""
    lost = dropped if dropped is not None else Counter()
    data = _json(reply)
    items = data.get("connections") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    haystack = _squash(piece)
    variants: dict[str, str | None] = {}  # (one text's search for a name's other spelling, once)
    groups = _groups(aliases or {})  # (an entry's name and spellings, #2841)

    def forms(end: str) -> list[str]:
        return [end, *sorted(groups.get(name_key(end), ()))]

    def holds(quote: str, end: str) -> bool:
        """Whether a squashed quote holds this end or any other spelling of the same entry."""
        return any(_in_quote(quote, f) for f in forms(end))

    out: list[Connection] = []
    for item in items:
        if not isinstance(item, dict):
            lost["unreadable"] += 1
            continue
        a, b, quote = item.get("a"), item.get("b"), item.get("quote")
        word = str(item.get("how", "")).strip().lower()
        word = KIN_NEUTRAL.get(word, word)  # (a gendered role word is kept as its neutral one)
        old_feeling = LEGACY_FEELING.get(word)  # "friend", "ally", "enemy" (#3175)
        if old_feeling is not None:
            word = "knows"
        reverse = word in LORE_REVERSED
        word = LORE_REVERSED.get(word, word)
        how = (
            CONNECTIONS.get(word)
            or LORE_KEPT.get(word)
            or next((k for k in own if k.lower() == word), None)
        )
        label = ""
        if how == "knows":
            # How the one feels about the other: one of nine words (a reputation, #3175), in the
            # line's "words". One off the list is dropped and counted; the connection is kept
            # without it. "neutral" is the plain "they know each other" and stores no word.
            raw = item.get("words")
            said_feeling = feeling_words.word_of(raw)
            if (
                said_feeling is None
                and raw not in (None, "")
                and not (isinstance(raw, str) and not raw.strip())
            ):
                lost["feeling_off_list"] += 1  # (a word off the list, or not even text)
            said_feeling = said_feeling or old_feeling
            label = "" if said_feeling in (None, feeling_words.NEUTRAL) else said_feeling
        if how is None and word in LORE_WORDS:  # a lore word with its own fixed words (#2757)
            how, label = FALLBACK_PREDICATE, LORE_WORDS[word]
        elif how is None and word in LORE_KIN:  # family by role
            how, label = "kin_of", word
        elif how is None and SYNONYMS.get("_".join(word.split())) == DISGUISED_AS:
            how = DISGUISED_AS  # "poses as", "impersonates": a disguise, never a public link
        if word == OTHER:  # the catch-all, kept with the words the text used
            label = at_word_end(" ".join(str(item.get("words") or "").split()), WORDS_CHARS)
            if not label or "|" in label:
                lost["word_not_allowed"] += 1
                continue
            # The label is the one free text of a connection: its words must be the quote's own,
            # so nothing carried in from the document or the list can ride in as a label.
            quoted = set(re.findall(r"\w+", _squash(str(quote))))
            if not set(re.findall(r"\w+", _squash(label))) <= quoted:
                lost["word_not_allowed"] += 1
                continue
            how = FALLBACK_PREDICATE
            if SYNONYMS.get("_".join(label.casefold().split())) == DISGUISED_AS:
                how, label = DISGUISED_AS, ""  # (a disguise word said as "other": still secret)
        if how is None:
            lost["word_not_allowed"] += 1
            continue
        if not all(isinstance(x, str) for x in (a, b, quote)):
            lost["unreadable"] += 1
            continue
        # A name is never shortened (#2607): a cut one is another name, so a long one is dropped.
        a, b = " ".join(str(a).split()), " ".join(str(b).split())
        if reverse:  # "a is an apprentice of b" is kept as "b mentors a"
            a, b = b, a
        spoken = str(quote).split()
        if (
            not a
            or not b
            or len(a) > NAME_CHARS
            or len(b) > NAME_CHARS
            or name_key(a) == name_key(b)
            or not spoken
        ):
            lost["bad_names"] += 1
            continue
        # A name the document spells another way than the AI wrote it (#2757) counts as in the
        # text when exactly one spelling a letter away is there; keep_connections later checks
        # that spelling is no OTHER name of the campaign. Kept per end, so each is checked against
        # the name it stands for.
        seen = [a, b]
        for index, end in enumerate((a, b)):
            if not _in_text(haystack, end, piece):
                spelled = next((f for f in forms(end)[1:] if _in_text(haystack, f, piece)), None)
                spelled = spelled or _variant(haystack, end, variants)
                if spelled is not None:
                    seen[index] = spelled
        if name_key(seen[0]) == name_key(seen[1]):  # two ends that came to one spelling
            lost["bad_names"] += 1
            continue
        words = _quote_window(spoken, seen[0], seen[1], holds)
        said = " ".join(words)
        said_squashed = _squash(said)
        made = Connection(
            a,
            how,
            b,
            said,
            words=label,
            variant_a=seen[0] if seen[0] != a else "",
            variant_b=seen[1] if seen[1] != b else "",
        )
        if not (
            _in_text(haystack, seen[0], piece)
            and _in_text(haystack, seen[1], piece)
            and _has(haystack, said)
        ):
            lost["not_in_text"] += 1
        elif holds(said_squashed, seen[0]) and holds(said_squashed, seen[1]):
            out.append(_via(made, said_squashed, seen, forms))
        elif (wide := _widen(piece, words, seen[0], seen[1], holds, lost)) is not None:
            # The quote named one end ("his sister…"): the sentence before it joins, word for word.
            lost["quote_widened"] += 1
            joined = replace(made, quote=" ".join(wide))
            out.append(_via(joined, _squash(" ".join(wide)), seen, forms))
        elif unsettled is not None and (
            open_ := _unsettled(piece, words, said_squashed, made, seen, holds)
        ):
            unsettled.append(open_)
        else:
            lost["not_in_text"] += 1
    return out


def _via(
    made: Connection, said: str, seen: list[str], forms: Callable[[str], list[str]]
) -> Connection:
    """The connection with, for an end the quote holds only through another spelling of its
    entry, that spelling as the end's variant (#2841). `keep_connections` then keeps the link
    secret when the spelling is a disguise, and drops it when the spelling is another entry's."""
    out = made
    for index, end in enumerate(seen):
        if _in_quote(said, end):
            continue
        via = next((f for f in forms(end)[1:] if _in_quote(said, f)), "")
        if via and index == 0 and not made.variant_a:
            out = replace(out, variant_a=via)
        elif via and index == 1 and not made.variant_b:
            out = replace(out, variant_b=via)
    return out


def _unsettled(
    piece: str,
    words: list[str],
    said: str,
    made: Connection,
    seen: list[str],
    holds: Callable[[str, str], bool] = lambda q, e: _in_quote(q, e),
) -> Unsettled | None:
    """The question a quote leaves open, or None when it is not one the AI should be asked: the
    quote must hold exactly one of the two names and exactly one distinct pronoun (two pronouns
    could each mean another person, and which one stands for the missing end is a guess)."""
    in_a, in_b = holds(said, seen[0]), holds(said, seen[1])
    if in_a == in_b:
        return None
    written = [w.strip(".,;:!?\"'“”‘’()") for w in words]
    pronouns = [w for w in written if w.casefold() in PRONOUNS]
    # "his father cared for him and his sister": his and him are one person, so one pronoun (#2841)
    if len({_PERSON.get(w.casefold(), w.casefold()) for w in pronouns}) != 1:
        return None
    pattern = r"\s+".join(re.escape(w) for w in words)
    for hit in re.finditer(pattern, piece, re.I):
        if _BREAK.search(piece[hit.start() : hit.end()]):
            continue
        return Unsettled(made, not in_a, pronouns[0], piece[: hit.end()])
    return None


def _clashes(reader: Ends, variant: str, own: str) -> bool:
    """Whether a spelling the text used for an end is, or may be, a name other than that end's own
    (#2757): then the AI's name was not this spelling, and the link is not this one's."""
    if not variant:
        return False
    got = reader.match(variant)
    return bool(got.ambiguous) or got.name not in (None, own)


def keep_connections(
    answers: Iterable[list[Connection]],
    names: Iterable[Found],
    known: Mapping[str, str],
    *,
    cap: int = MAX_CONNECTIONS,
    skip: Callable[[Connection], bool] | None = None,
    dropped: Dropped | None = None,
    questions: list[str] | None = None,
    speaker: str | None = None,
    picks: list[Pick] | None = None,
    secret_keys: Collection[str] = (),
) -> list[Connection]:
    """The pieces' connections as one list, each once, whose two ends are names DMbot will have:
    one of this reading's names (or its other spelling, public or secret) or `known` (a name key
    of the campaign's own mapped to its name). Each end becomes the name it stands for. The same
    pair is offered once, whichever way round or how it was said (one fact per pair and kind;
    friends and family read the same both ways). At most `cap`. `dropped` counts why each one that
    was not kept was not (`name_not_found`, `which_one`, `same_pair`, `already_have`,
    `over_cap`).

    An end is matched the way a reader would (`end_match`, #2642): a first or last name, with a
    title or "my brother" taken off. An end that fits two names equally ("Mara") is left out as
    `which_one` and its words are added to `questions` ("Which Mara?"), never guessed. With
    `picks` given, a connection whose other end is a name waits for the DM's pick instead
    (`which_waiting`, #2650): it is added there, with its candidates, and is not left out.

    `speaker`: the character whose backstory this is, as the DM said (#2650). "I", "my" and "me"
    are that character, and so are a bare "his" or "her"; with no speaker, a connection that
    needs "I" is left out as `whose` (counted, never guessed)."""
    lost = dropped if dropped is not None else Counter()
    names = list(names)
    ends: dict[str, str] = dict(known)
    for f in names:  # every public name first: another name's spelling never takes it over
        ends.setdefault(name_key(f.name), f.name)
    hidden = {name_key(x) for f in names for x in f.secret} - {name_key(f.name) for f in names}
    # (disguises of names this read did not offer, such as the campaign's own: `secret_keys`)
    hidden |= {name_key(x) for x in secret_keys} - {name_key(f.name) for f in names}
    claimed: dict[str, set[str]] = {}
    for f in names:
        for spelling in (*f.also, *f.secret):
            claimed.setdefault(name_key(spelling), set()).add(f.name)
    for key, owners in claimed.items():
        # a spelling that two names claim is no end: DMbot would be guessing which one is meant
        if key not in ends and len(owners) == 1:
            ends[key] = next(iter(owners))
    both_ways = {"kin_of", "ally_of", "enemy_of", "rival_of"}
    # (a public other spelling fits an end by its words too, for the name it belongs to; a secret
    # one never does)
    spellings = {
        sp: f.name for f in names for sp in f.also if len(claimed.get(name_key(sp), ())) == 1
    }
    reader = Ends(ends, {*ends.values()}, spellings)
    seen: set[tuple[str, str, str]] = set()
    # kin_of, by unordered pair: (its place in `out` or None, its role or "", the order it was said)
    kin_held: dict[tuple[str, str], tuple[int | None, str, tuple[str, str]]] = {}
    contradicted: set[tuple[str, str]] = set()
    knows_held: dict[tuple[str, str], int] = {}  # a kept `knows` line, by direction (#3175)
    # kin_of picks ("Which Mara?") with a role, by (the end that is a name, the end as written):
    # the pick itself and the way round it was said, so opposite roles are caught here too
    kin_picks: dict[tuple[str, str], tuple[Pick, bool]] = {}
    seen_picks: set[tuple[str, str, str, bool]] = set()
    out: list[Connection] = []
    answers = [list(piece) for piece in answers]
    looked = 0
    for piece in answers:
        for c in piece:
            looked += 1
            end_a, no_a = speaker_end(c.a, speaker)
            end_b, no_b = speaker_end(c.b, speaker)
            if no_a or no_b:
                lost["whose"] += 1
                continue
            first, second = reader.match(end_a), reader.match(end_b)
            if (c.variant_a or c.variant_b) and (first.ambiguous or second.ambiguous):
                lost["spelling_clash"] += 1  # (a spelling is never a question: it is a guess)
                continue
            if (
                picks is not None
                and len(picks) < MAX_PICKS
                and first.ambiguous != second.ambiguous  # exactly one end is the question
                and max(len(first.ambiguous), len(second.ambiguous)) <= MAX_PICK_CHOICES
                and (second.name if first.ambiguous else first.name) is not None
            ):
                fixed, other = (first, second) if second.ambiguous else (second, first)
                # (a pick is secret as a connection would be, so its card is kept so too)
                secret = bool(
                    {
                        name_key(c.a),
                        name_key(c.b),
                        name_key(c.variant_a),
                        name_key(c.variant_b),
                        fixed.key,
                    }
                    & hidden
                )
                word = c.b if second.ambiguous else c.a
                again = (name_key(fixed.name or ""), c.how, name_key(word), bool(second.ambiguous))
                if again in seen_picks:
                    lost["same_pair"] += 1
                    continue
                seen_picks.add(again)
                made_pick = Pick(
                    fixed.name or "", bool(second.ambiguous), word, other.ambiguous or (),
                    c.how, c.quote, secret, c.words,
                )  # fmt: skip
                if c.how == "kin_of" and kin.directional(c.words):
                    # The same contradiction check as for a plain connection: a role said both
                    # ways round, or against a role already offered for one of the candidates, is
                    # not believed; the two are still family, so the question is plain family.
                    pk = (name_key(fixed.name or ""), name_key(word))
                    earlier = kin_picks.get(pk)
                    if earlier is not None and earlier[1] != bool(second.ambiguous):
                        picks[picks.index(earlier[0])] = replace(earlier[0], words="")
                        lost["which_way"] += 1
                        continue
                    kin_picks[pk] = (made_pick, bool(second.ambiguous))
                    for cand in other.ambiguous or ():
                        ck = name_key(cand)
                        held = kin_held.get((min(pk[0], ck), max(pk[0], ck)))
                        said_as = (pk[0], ck) if second.ambiguous else (ck, pk[0])
                        if held is not None and held[1] and held[2] != said_as:
                            made_pick = replace(made_pick, words="")
                            kin_picks[pk] = (made_pick, bool(second.ambiguous))
                            where = held[0]
                            if where is not None:  # the line offered already is plain family too
                                out[where] = replace(out[where], words="")
                            kin_held[(min(pk[0], ck), max(pk[0], ck))] = (where, "", held[2])
                            break
                picks.append(made_pick)
                lost["which_waiting"] += 1
                continue
            if first.ambiguous or second.ambiguous:
                lost["which_one"] += 1
                if questions is not None:
                    for end, got in ((c.a, first), (c.b, second)):
                        if got.ambiguous and end not in questions:
                            questions.append(end)
                continue
            a, b = first.name, second.name
            if a is None or b is None or name_key(a) == name_key(b):
                lost["name_not_found"] += 1
                continue
            # A spelling the text used for an end the AI wrote another way must not be another
            # name of the campaign (#2757): then it was that name, and the link is not this one's.
            if _clashes(reader, c.variant_a, a) or _clashes(reader, c.variant_b, b):
                lost["spelling_clash"] += 1
                continue
            ka, kb = name_key(a), name_key(b)
            made = Connection(
                a,
                c.how,
                b,
                c.quote,
                # secret when the end was written under a secret spelling, with a title in
                # front of it too ("Lady Nightshade", #2591): the matched key counts
                bool(
                    {
                        name_key(c.a),
                        name_key(c.b),
                        name_key(c.variant_a),
                        name_key(c.variant_b),
                        first.key,
                        second.key,
                    }
                    & hidden
                ),
                c.words,
                decided=c.decided,
            )
            role = c.words if c.how == "kin_of" and kin.directional(c.words) else ""
            # A shared role (sibling, spouse, cousin) has no order to contradict, but it still
            # beats a plain "family" line for the pair, whichever was read first.
            shared = c.words if c.how == "kin_of" and kin.role_of(c.words) in kin.MUTUAL else ""
            pair = (min(ka, kb), max(ka, kb))
            if c.how == "kin_of":
                # Family connections are stored once for a pair, in the order of a role ("a is the
                # parent of b"): a role beats plain "family", two roles the same way round are one
                # fact, and two roles the opposite way round contradict: neither role is believed,
                # but the two are still family, so plain "family" is offered (the DM sees no
                # direction to pick from, and no line is lost).
                if pair in contradicted:
                    lost["which_way" if role else "same_pair"] += 1
                    continue
                held = kin_held.get(pair)
                if held is not None:
                    where, held_role, held_order = held
                    if role and held_role and held_order != (ka, kb):
                        contradicted.add(pair)
                        if where is not None:  # the first line stays, as plain family
                            out[where] = replace(out[where], words="")
                        lost["which_way"] += 1  # and this one is the contradiction
                        continue
                    # Only a plain line is replaced, and only by a role: a held shared role or
                    # free-text label is never swapped (first read wins; two roles conflict anyway).
                    if (
                        where is None  # (narrows `where` for the index below)
                        or not (role or shared)
                        or held_role
                        or out[where].words
                    ):
                        lost["same_pair"] += 1  # (a plain line, or the same role again)
                        continue
                    lost["same_pair"] += 1  # the plain line is dropped for the role
                    out[where] = made
                    kin_held[pair] = (
                        where,
                        role,
                        (ka, kb),
                    )  # (a shared role has no order: role "")
                    continue
                kin_held[pair] = (None, role, (ka, kb))
            else:
                if c.how == "knows" and (ka, kb) in knows_held:
                    # A plain "knows" read first must not swallow a later one that says how the
                    # one feels (#3175): the word is added to the held line. Two different
                    # words for the same direction clash: the first stays and the clash is counted.
                    held_at = knows_held[(ka, kb)]
                    held_words = out[held_at].words
                    if c.words and not held_words:
                        out[held_at] = replace(out[held_at], words=c.words)
                        lost["feeling_added"] += 1
                    elif c.words and held_words != c.words:
                        lost["feeling_clash"] += 1
                    else:
                        lost["same_pair"] += 1
                    continue
                if c.how in both_ways and kb < ka:
                    ka, kb = kb, ka
                # One fact per pair and kind: the queue's key has no words, so a second
                # catch-all line for the pair (a mentor who is also a rival) is not offered.
                if (ka, c.how, kb) in seen:
                    lost["same_pair"] += 1
                    continue
                seen.add((ka, c.how, kb))
            if role:
                # A "Which X?" question asked earlier with the opposite role for one of this
                # line's two names contradicts it the same way: both end as plain family.
                for pk, (pick, fixed_is_a) in list(kin_picks.items()):
                    if not pick.words:
                        continue
                    cands = {name_key(x) for x in pick.candidates}
                    opposite = (
                        (pk[0] == ka and kb in cands and not fixed_is_a)
                        or (pk[0] == kb and ka in cands and fixed_is_a)
                    )  # fmt: skip
                    if opposite:
                        plain_pick = replace(pick, words="")
                        picks_ref = picks if picks is not None else []
                        picks_ref[picks_ref.index(pick)] = plain_pick
                        kin_picks[pk] = (plain_pick, fixed_is_a)
                        made = replace(made, words="")
                        role = ""
                        kin_held[pair] = (None, "", (ka, kb))
                        break
            if skip is not None and skip(Connection(a, c.how, b, c.quote, words=c.words)):
                lost["already_have"] += 1
                continue  # the campaign has it already, or an end is no name it has
            if len(out) >= cap:
                # Full. The rest are still read, so a contradiction among family lines is found
                # whichever side of the cap it falls on, but none of them is offered.
                lost["over_cap"] += 1
                continue
            out.append(made)
            if c.how == "kin_of":
                kin_held[pair] = (len(out) - 1, role, (ka, kb))
            if c.how == "knows":
                knows_held[(ka, kb)] = len(out) - 1
    return out
