"""The short tag shown for an entry that has no unique proper name (#3431). Pure.

"beggar woman" and "the woman" say what someone is, not who, and two of them look the same in a list
or a question. So wherever DMbot asks about such an entry or lists it for the DM, it is shown with
where it was seen or how it ties to someone named: `beggar woman (in Termalaine)`,
`the woman (member of the Ash Wolves)`. The tag is built every time from what the DM has confirmed,
never stored and never invented, and it holds no words of a transcript.

A tie to an entry the viewer may not see is marked `visible=False` and never used; `dm` is the DM's
view, which may use secret confirmed ties."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.memory import descriptions, kin
from dmbot.memory.models import Entity

TAG_MAX = 50  # the part in brackets; a long clause is cut at a word
_PLACE = "located_in"
_GROUP_LABELS = {
    "member_of": "member of",
    "works_for": "works for",
    "serves": "serves",
}
_CLAUSE_END = re.compile(r"[.;:!?\n]|,\s")


@dataclass(frozen=True, slots=True)
class TagTie:
    """One tie of the entry, as the viewer may see it. `outgoing`: the entry is the subject."""

    predicate: str
    other_name: str  # the other end's shown name
    outgoing: bool = True
    confirmed: bool = False
    secret: bool = False
    detail: str = ""
    visible: bool = True  # the viewer may see the other end (not hidden from players, not a clue)


def needs_tag(entity: Entity) -> bool:
    """Whether this entry has no unique proper name: an unnamed person, or a name that is only a
    description ("beggar woman")."""
    return entity.unnamed or descriptions.is_description(entity.name)


def _cut(text: str, limit: int = TAG_MAX) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(",;: ") + "…"


def _first_clause(note: str) -> str:
    return _CLAUSE_END.split(" ".join(note.split()), maxsplit=1)[0].strip()


def _context(ties: Sequence[TagTie], note: str, dm: bool) -> str | None:
    """Strongest confirmed tie first: where it is, then its group, then family; else the
    first clause of the entry's own note."""
    usable = [
        t for t in ties if t.confirmed and t.visible and t.other_name and (dm or not t.secret)
    ]
    for tie in usable:
        if tie.predicate == _PLACE and tie.outgoing:
            return f"in {tie.other_name}"
    for tie in usable:
        label = _GROUP_LABELS.get(tie.predicate)
        if label is not None and tie.outgoing:
            return f"{label} {tie.other_name}"
    for tie in usable:
        if tie.detail and kin.role_of(tie.detail) is not None:
            return f"family of {tie.other_name}"  # (which way the role reads is not guessed here)
    clause = _first_clause(note)
    return clause or None


def tag_for(
    entity: Entity,
    ties: Sequence[TagTie] = (),
    *,
    shown_name: str | None = None,
    dm: bool = False,
) -> str:
    """`shown_name` (default the entry's name) with its tag in brackets when the entry needs one;
    the name alone for an entry with a proper name. For an unnamed person, pass the label built by
    `unnamed.label` as `shown_name`: it already says whose they are, and is used when no tie says
    more. A player's view (`dm=False`) never uses a secret tie, and the note (the DM's own words) is
    used only for the DM."""
    name = shown_name or entity.name
    if not needs_tag(entity):
        return name
    context = _context(ties, entity.description if dm else "", dm)
    if context is None:
        return name
    return f"{name} ({_cut(context)})"
