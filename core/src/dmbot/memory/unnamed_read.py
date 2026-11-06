"""People a lore text describes but never names (#2959 item 4, owner decision 2026-10-29).

"The son of an influential weaver mother and a devoted father" says the character has a mother and
a father and nothing about who they are. Pass 1 lists such a person under an UNNAMED heading as
`- <named end> | <role of the person> | <the text's words for them> | quote: "…"`. This module
checks each line the way every other line is checked (nothing is invented: the quote is in the
document, holds the named end and the person's own words, and the role is a family word DMbot
keeps) and turns it into an `UnnamedLink`, which the existing machinery of the who-is-it check
(#2790: `MemoryStore.add_unnamed`, `name_background._unnamed_rows`) stores as a proposed entry with
no name, hung off the named end. Pure: no AI, no database. Counts only are logged.

Who may see it (one rule): the entry is a person the story never named, so it is the DM's alone
(the store keeps it from players) whatever the upload's knowledge choice says; the choice
(`known_by`, world level) only sets who knows the connection, and that applies once the DM gives
the person a name. If the two ever disagree, the unnamed entry wins.

One person is never two entries: a line is dropped when the same read already ties the named end
to a NAMED person by that role word ("his father Rolo"), wherever in the text the name stands,
before or after the description, and when the person's words are themselves a name of the read.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from dmbot.memory import background as bg
from dmbot.memory.models import name_key
from dmbot.memory.who_resolve import UnnamedLink

MAX_UNNAMED = 20  # unnamed people from one read: the rest are counted, never made
WHAT_WORDS = 6  # the text's words for a person: a phrase, not a sentence

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
) -> list[UnnamedLink]:
    """The unnamed people the text really holds, as links for the existing store path.

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
        kin = _kin(line.role)
        what_words = line.what.split()
        if kin is None or not what_words or len(what_words) > WHAT_WORDS:
            dropped["word_not_allowed"] += 1
            continue
        role, fixed_is_a = kin
        head = line.role.casefold()  # the text's own role word ("mother"), the second "name"
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
        if people and bg._third_name(
            " ".join(window), people, bg._own_ends([line.anchor, head], forms)
        ):
            dropped["not_in_text"] += 1  # (another person is in the quote: whose parent is it?)
            continue
        if _named_elsewhere(line, links, forms):
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
                how="kin_of",
                quote=" ".join(window),
                words=role,
                what=line.what,
                anchored=True,
                role=role,
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
