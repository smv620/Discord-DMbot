"""What a sidebar rules answer looked at (#1957): the list behind the "📖 What I looked at"
button, so the DM can see why DMbot cited what it did and open any card.

The list is what `context.build` gave the answer (its `hits` and `house_rules`), taken when the
answer is made and kept with the answer, never worked out again later: a later look-up could
differ. It is the DM's alone. A table question's answer has no such button, and the list is
kept in memory only (`Registry`, a few hundred answers, oldest dropped), so it is gone after a
restart and nothing about it is written to the database; the button then says so. House rules
come first and are labelled as house rules; an entry of the DM's chapter says "DM only"."""

from __future__ import annotations

import secrets
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.discord_limits import PHONE_LABEL_MAX, shorten
from dmbot.rules import rule_card
from dmbot.rules.index import Hit, Index
from dmbot.sidebar.context import Context

BUTTON_LABEL = "What I looked at"
BUTTON_EMOJI = "📖"
EMPTY = "I didn't look anything up for this one."
GONE = "I no longer have that list. Ask the question again."
NOT_YOURS = "Only the person who asked can open this list."
NO_CARD = "I can't open that card now. Ask the question again."
HINT = "Tap a name to read its card."
HEADING = "What I looked at:"
KEEP = 300  # answers whose lists are kept; the oldest goes first
LIST_MAX = 10  # lines shown (a rules answer holds at most a few)
BUTTONS_MAX = 5  # lines that get a button: more is a wall of buttons on a phone


@dataclass(frozen=True, slots=True)
class Looked:
    """One line of the list."""

    label: str  # "Shield (spell)", or "House rule 3"
    place: str  # "SRD 5.2.1, p. 172", "DM only", "your table"
    value: str = ""  # what `/dmbot rule` is given to open its card; "" for a house rule


def _place(hit: Hit) -> str:
    if hit.entry.dm_side:
        return "DM only"
    where = f"{hit.entry.source}, p. {hit.entry.page}"
    return f"{where} {hit.tag}" if hit.tag else where


def of(ctx: Context, srd: Index) -> tuple[Looked, ...]:
    """The house rules, then the entries, that the answer was built from, in the order the
    prompt had them. A lazy import: the UI modules import the sidebar."""
    from dmbot.ui.rule_lookup import choice_value

    items = [Looked(f"House rule {rule.number}", "your table") for rule in ctx.house_rules]
    items += [
        Looked(rule_card.choice_label(hit.entry), _place(hit), choice_value(srd, hit.entry))
        for hit in ctx.hits
    ]
    return tuple(items[:LIST_MAX])


def text_of(items: Sequence[Looked]) -> str:
    """The private message: one line each, `Shield (armor) · SRD 5.2.1, p. 93`, and a hint when
    some lines are buttons."""
    if not items:
        return EMPTY
    lines = [HEADING, *(f"{item.label} · {item.place}" for item in items)]
    if any(item.value for item in items):
        lines.append(HINT)
    return "\n".join(lines)


def button_labels(items: Sequence[Looked]) -> list[str]:
    """What each line's button says: the name alone, cut to what a phone shows (25), and with its
    kind in front of the cut when two lines share a name ("Shield (spell)", "Shield (armor)").
    The full label is in the line above the button."""
    names = [item.label.split(" (", 1)[0] for item in items]
    out = []
    for item, name in zip(items, names, strict=True):
        shown = item.label.split(":", 1)[0].rstrip(")") + ")" if names.count(name) > 1 else name
        out.append(shorten(shown, PHONE_LABEL_MAX))
    return out


@dataclass(frozen=True, slots=True)
class Kept:
    owner_id: int  # the DM who asked: the only one who may open the list
    guild_id: int
    campaign_id: str
    items: tuple[Looked, ...]


class Registry:
    """The lists of the latest answers, by a random key put in the button's id. In memory only."""

    def __init__(self, keep: int = KEEP) -> None:
        self._keep = keep
        self._kept: OrderedDict[str, Kept] = OrderedDict()

    def remember(self, kept: Kept) -> str:
        key = secrets.token_hex(6)
        self._kept[key] = kept
        while len(self._kept) > self._keep:
            self._kept.popitem(last=False)
        return key

    def get(self, key: str) -> Kept | None:
        return self._kept.get(key)
