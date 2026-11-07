"""People a lore text describes but never names (#2959 item 4, owner decision 2026-10-29).

"The son of an influential weaver mother and a devoted father" says the character has a mother and
a father and nothing about who they are. Pass 1 lists such a person under an UNNAMED heading as
`- <named end> | <role of the person> | <the text's words for them> | quote: "…"`. This module
checks each line the way every other line is checked (nothing is invented: the quote is in the
document, holds the named end and the person's own words, and the relationship is one DMbot keeps)
and turns it into an `UnnamedLink`, which the existing machinery of the who-is-it check (#2790:
`MemoryStore.add_unnamed`, `name_background._unnamed_rows`) stores as a proposed entry with no
name: hung off the named end for a family role ("Ken's sister"), and on its own for any other
relationship ("unnamed - barmaid with red hair", owner's rule, #3736: everyone the story mentions
beside a named entry is kept). Pure: no AI, no database. Counts only are logged.

Who may see it (one rule): the entry is a person the story never named, so it is the DM's alone
(the store keeps it from players) whatever the upload's knowledge choice says; the choice
(`known_by`, world level) only sets who knows the connection, and that applies once the DM gives
the person a name. If the two ever disagree, the unnamed entry wins.

One person is never two entries: a line is dropped when the same read already ties the named end
to a NAMED person by that role word ("his father Rolo"), wherever in the text the name stands,
before or after the description, and when the person's words are themselves a name of the read.
That check is for family only: someone tied to a named entry in any other way ("a barmaid, Mira,
warned Tavin") is held back only when their words are a name of the read or another person stands
in the same quote, so a person the text names elsewhere can come back as an unnamed one, for the
DM's "Same as…" question to join; the owner's rule (#3736) keeps a person rather than lose one.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from dmbot.memory import background as bg
from dmbot.memory import place_words, reputation_words
from dmbot.memory.link_words import KIN, predicate_for_either
from dmbot.memory.models import name_key
from dmbot.memory.ontology import FALLBACK_PREDICATE
from dmbot.memory.who_resolve import UnnamedLink

# Unnamed people from one read. Nothing is dropped early (owner, #3736), so the limit is only a
# guard against a runaway read: a document's own people fit well inside it. The rest are counted.
MAX_UNNAMED = 60
# The text's words for a person: the most distinguishing description it gives ("barmaid at Three
# Flags Sailing with red hair", #3736), a phrase, never a sentence.
WHAT_WORDS = 12

_LINE = re.compile(
    r'^-\s*(?P<anchor>[^|]+?)\s*\|\s*(?P<role>[^|]+?)\s*\|\s*(?P<what>[^|]+?)\s*\|\s*quote:\s*"(?P<quote>.+)"\s*$'
)


@dataclass(frozen=True, slots=True)
class Line:
    anchor: str  # the named end, as the AI wrote it
    role: str  # what the person is to it: mother, sister, uncle…
    what: str  # the text's words for the person: "weaver mother"
    quote: str


def parse(lines: Iterable[str]) -> tuple[list[Line], int]:
    """The well-formed UNNAMED lines, and how many were not."""
    out: list[Line] = []
    bad = 0
    for raw in lines:
        m = _LINE.match(raw.strip())
        if m is None:
            bad += 1
            continue
        out.append(
            Line(*(" ".join(m.group(g).split()) for g in ("anchor", "role", "what", "quote")))
        )
    return out, bad


# The roles the prompt offers, and the neutral words: nothing else is a family role here
# (`LORE_REVERSED` also maps "apprentice", which is no family).
ROLES = frozenset(
    {"mother", "father", "sister", "brother", "son", "daughter", "aunt", "uncle", "niece",
     "nephew", "cousin", "husband", "wife", "grandmother", "grandfather", "parent", "child",
     "sibling", "spouse", "grandparent", "grandchild"}
)  # fmt: skip


_BRACKET = re.compile(r"^(?P<base>.+?)\s*\((?P<inner>[^()]+)\)$")
_IS_A = re.compile(r"^(?:is|are|was|were)\s+(?:(?:an?|the)\s+)?", re.IGNORECASE)
_TRAILING = re.compile(r"\s+(?:of|for|to|with)$", re.IGNORECASE)
_PLAIN = frozenset({"the", "and", "with", "who", "that", "from", "for", "her", "his", "their"})


def _tie(role: str, quote: str, place: bool) -> tuple[str, str, bool, bool] | None:
    """How a line's second column is kept: (the relationship, its words, whether the NAMED end is
    the first end, whether the person hangs off the named end), or None when it is not one DMbot
    keeps (counted as `word_not_allowed`).

    A family role is as before ("his sister": "Ken's sister", `_kin`). Any other relationship
    (#3736: "works for", "is a rival of", "gave a pouch to") reads from the described person to
    the named one, the way the line is asked for, and the person is named on their own ("unnamed -
    barmaid with red hair"), since "Ken's served" says nothing. It is held to the same rules as a
    relationship line of the main read, so nothing rides in that the text does not say:
    - a place word ("works in") only when the named end is a place (`place`);
    - a feeling ("knows (hostile)") stepped down unless the quote has a cue for it (#3606);
    - anything else in the main read's own words (`predicate_for_either`, then its short words:
      "mentors", "is an apprentice of"), else the catch-all, whose words must be the quote's own
      and at most `bg.WORDS_CHARS` long, exactly as a catch-all line of the main read."""
    kin = _kin(role)
    if kin is not None:
        return kin[0], kin[0], kin[1], True
    words = " ".join(role.split())
    if not words:
        return None
    if place_words.canonical(words):
        return ("located_in", place_words.canonical(words) or "", False, False) if place else None
    predicate, detail, swap = predicate_for_either(words)
    if predicate == KIN:
        return None  # (a family word outside the roles the prompt offers: not guessed at)
    if predicate == "located_in" and not place:
        return None
    if predicate == "knows":
        bracket = _BRACKET.match(words)
        said = reputation_words.word_of(bracket["inner"]) if bracket else None
        if said is None or said == reputation_words.NEUTRAL:
            return "knows", "", swap, False
        held, _ = reputation_words.held_to_the_quote(said, quote)
        return "knows", "" if held == reputation_words.NEUTRAL else held, swap, False
    if predicate != FALLBACK_PREDICATE:
        return predicate, detail, swap, False
    short = _TRAILING.sub("", _IS_A.sub("", words.casefold()))
    for key in (short, short.removesuffix("s")):
        if key in bg.LORE_REVERSED:  # "is an apprentice of": kept as "the named one mentors"
            return bg.CONNECTIONS[bg.LORE_REVERSED[key]], "", True, False
        how = bg.CONNECTIONS.get(key) or bg.LORE_KEPT.get(key)
        if how is not None and how not in (KIN, "located_in", "knows"):
            return how, "", False, False
    label = bg.at_word_end(words, bg.WORDS_CHARS)
    quoted = set(re.findall(r"\w+", bg._squash(quote)))
    if not label or "|" in label or not set(re.findall(r"\w+", bg._squash(label))) <= quoted:
        return None
    return FALLBACK_PREDICATE, label, False, False


def _kin(role: str) -> tuple[str, bool] | None:
    """(the role as DMbot keeps it, whether the NAMED end is the first end) for the person's role
    word. "mother" is a parent of the named end (the person is first); "son" is a child of it,
    which is kept as "the named end is the parent" (`LORE_REVERSED`, so it is one fact whichever
    way it is said). A shared role (sibling, spouse, cousin) has no order."""
    if role.casefold() not in ROLES:
        return None
    word = bg.KIN_NEUTRAL.get(role.casefold(), role.casefold())
    if word in bg.LORE_REVERSED:
        return bg.LORE_REVERSED[word], True
    if word in bg.LORE_KIN:
        return word, False
    return None


def read_unnamed(
    lines: Iterable[str],
    text: str,
    aliases: Mapping[str, Sequence[str]],
    links: Sequence[bg.Connection],
    names: Collection[str],
    others: Collection[str],
    dropped: Counter[str],
    people: Collection[str] = (),
    places: Collection[str] = (),
) -> list[UnnamedLink]:
    """The unnamed people the text really holds, as links for the existing store path.
    `places`: the name keys of the read's places, the only ends a place word ("works in") may
    have.

    `aliases`: each name key of the read and campaign to its other spellings (as for connections).
    `links`: this read's connections, to catch a person the text names elsewhere. `names`: every
    name of the read (a person's words that are one of them are no unnamed person). `others`: the
    name keys a reach looks for as a third name (`background.reach_names`). `people`: the name keys
    of the read's persons: a second person in the quote means the described person may be theirs
    ("Pell met Tamsin, the son of a ropemaker mother"), so the line is dropped. Counts go to
    `dropped`:
    `unreadable`, `word_not_allowed` (a role DMbot does not keep), `not_in_text`, `already_named`,
    `same_pair` and `over_cap`; never a word of the text."""
    parsed, bad = parse(lines)
    dropped["unreadable"] += bad
    haystack = bg._squash(text)
    groups = bg._groups(aliases)
    name_keys = {name_key(n) for n in names}

    def forms(end: str) -> list[str]:
        return [end, *sorted(groups.get(name_key(end), ()))]

    def holds(quote: str, end: str) -> bool:
        return any(bg._in_quote(quote, f) for f in forms(end))

    out: list[UnnamedLink] = []
    seen: set[tuple[str, str]] = set()
    for line in parsed:
        what_words = line.what.split()
        tie = _tie(line.role, line.quote, name_key(line.anchor) in places)
        if tie is None or not what_words or len(what_words) > WHAT_WORDS:
            dropped["word_not_allowed"] += 1
            continue
        role, detail, fixed_is_a, family = tie
        # the second "name" the quote must hold: the text's own role word ("mother") for family,
        # else the description's own longest word ("barmaid"), which says who it is about
        own = [w for w in what_words if len(w) >= 3 and w.casefold() not in _PLAIN] or what_words
        head = line.role.casefold() if family else max(own, key=len).casefold()
        words = line.quote.split()
        if (
            len(words) > bg.QUOTE_WORDS
            or not bg._in_text(haystack, line.anchor, text)
            or not bg._has(haystack, line.quote)
            or not all(bg._has(haystack, w) for w in what_words)
        ):
            dropped["not_in_text"] += 1
            continue
        window: list[str] | None = words
        said = bg._squash(line.quote)
        if not (holds(said, line.anchor) and bg._in_quote(said, head)):
            # The sentence has no named subject ("Raised in wealth as the son of a weaver
            # mother"): the window reaches back to the sentence that names the end, the same
            # rules as for a pronoun (same paragraph, no third name between).
            window = bg._widen(
                text, words, line.anchor, head, lambda q, e: holds(q, e) or bg._in_quote(q, e),
                dropped, others, bg._own_ends([line.anchor, head], forms), needs_pronoun=False,
            )  # fmt: skip
        if window is None:
            dropped["not_in_text"] += 1
            continue
        shown = bg._squash(" ".join(window))
        if not all(bg._in_quote(shown, w) for w in what_words if len(w) >= 3):
            dropped["not_in_text"] += 1
            continue
        if name_key(line.what) in name_keys:
            dropped["already_named"] += 1  # (the person's words are a name of this read)
            continue
        # (family only: "whose parent is it?" is a question of family. A barmaid who warned Tavin
        # and Mira is tied to each of them, #3736)
        if (
            family
            and people
            and bg._third_name(" ".join(window), people, bg._own_ends([line.anchor, head], forms))
        ):
            dropped["not_in_text"] += 1  # (another person is in the quote: whose parent is it?)
            continue
        if family and _named_elsewhere(line, links, forms):
            dropped["already_named"] += 1
            continue
        key = (name_key(line.anchor), f"{role}|{name_key(line.what)}")
        if key in seen:
            dropped["same_pair"] += 1
            continue
        if len(out) >= MAX_UNNAMED:
            dropped["over_cap"] += 1
            continue
        seen.add(key)
        out.append(
            UnnamedLink(
                fixed=line.anchor,
                fixed_is_a=fixed_is_a,
                how=KIN if family else role,
                quote=" ".join(window),
                words=detail,
                what=line.what,
                anchored=family,
                # (the role is what the person is TO their anchor: one on their own has none, so
                # the barmaid who warned Tavin and works at the inn is one person, #3736)
                role=role if family else "",
            )
        )
    return out


def _named_elsewhere(
    line: Line, links: Sequence[bg.Connection], forms: Callable[[str], list[str]]
) -> bool:
    """Whether this read already ties the named end to a named person by the same role word: a
    family connection of the end, with this role, whose quote says the role word ("his father
    Rolo"). Its limits, on purpose: only this read's connections are seen (a named parent kept
    by an earlier read is not), and a named and an unnamed sibling of one end are taken for one
    person; both fail safe towards fewer unnamed entries, never a second entry."""
    mine = {name_key(f) for f in forms(line.anchor)}
    # (a role word inside another word or a hyphenated one, "mother-in-law", is not this role)
    role = re.compile(rf"(?<![\w-]){re.escape(line.role.casefold())}(?![\w-])")
    kept = _kin(line.role)
    for c in links:
        if c.how != "kin_of" or (kept is not None and c.words not in (kept[0], "")):
            continue
        if {name_key(c.a), name_key(c.b)} & mine and role.search(c.quote.casefold()):
            return True
    return False
