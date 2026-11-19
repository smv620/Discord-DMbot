"""Who knows it, in one line, worked out against the current party (#4537, party knowledge slice 3a;
`docs/design/party_knowledge.md` §7, rules 13.1 and 13.2; Supervisor's ruling on #4537; the
ux-critic's words).

| Icon | When | Line |
|---|---|---|
| 🌍 | "everyone" holds it (common knowledge) | "🌍 Everyone knows" |
| ✅ | every current member knows it (a party of one: that character) | "✅ The whole party knows" |
| 👥 | two or more members, not all | "👥 Known to: Cerric and Belleros" |
| 👤 | exactly one member, and the party has more than one | "👤 Known to: Cerric" |
| 🤫 | no member knows it | "🤫 No one in the party knows. Only the DM does." or "… Known to: …" |

- **Worked out when shown, never stored** (rule 13.2): from the statement's knowers and the party
  as it is now (`party_record.current_members`, rule 13.1).
- **A guess counts as knowing** (Supervisor's ruling D on #4524): any row, at any sureness.
- **👥 and 👤 name party members only,** in the party list's order; anyone else is one tap away in
  "Who knows this?". **🤫 names who else does:** characters who left first (with why), then NPCs,
  at most three, then "and N others".
- **🌍 comes first,** even with no party; with no party and no common row, the line says so.
- Pure, names in, words out; character names are escaped by the caller's `escape`. Never a pronoun
  for a character (Supervisor's ruling C).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass

EVERYONE = "🌍 Everyone knows"
WHOLE = "✅ The whole party knows"
WHOLE_ONE = "✅ The whole party knows ({name})"
KNOWN_TO = "{icon} Known to: {names}"
NO_ONE = "🤫 No one in the party knows. Only the DM does."
NO_ONE_BUT = "🤫 No one in the party knows. Known to: {names}"
NO_PARTY = "No player characters yet. Add one under Names."
MAX_OTHERS = 3  # (a statement many NPCs know would fill the card)
LEFT_WORDS = {"left": "left the party", "retired": "retired", "died": "died", "away": "away"}


@dataclass(frozen=True, slots=True)
class Outsider:
    """Someone who knows it and isn't in the party now: a character who left (`ended`: why), or an
    NPC (`ended` None)."""

    id: str
    name: str
    ended: str | None = None


def joined(names: Sequence[str]) -> str:
    """ "Cerric", "Cerric and Belleros", "Cerric, Belleros and Mira". Pure."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def line(
    holders: Collection[str],
    party: Sequence[tuple[str, str]],
    *,
    common: bool = False,
    outsiders: Sequence[Outsider] = (),
    escape: Callable[[str], str] = lambda s: s,
) -> str:
    """The one line for a statement. `holders`: who has a row (ids, a guess included); `party`:
    the current members (id, name) in the party list's order; `outsiders`: who else knows it, in
    the order to show (the caller puts those who left first)."""
    if common:
        return EVERYONE
    if not party:
        return NO_PARTY
    held = set(holders)
    knowing = [escape(name) for pid, name in party if pid in held]
    if knowing and len(knowing) == len(party):
        return WHOLE_ONE.format(name=knowing[0]) if len(party) == 1 else WHOLE
    if len(knowing) >= 2:
        return KNOWN_TO.format(icon="👥", names=joined(knowing))
    if knowing:
        return KNOWN_TO.format(icon="👤", names=knowing[0])
    others = [o for o in outsiders if o.id in held]
    if not others:
        return NO_ONE
    shown = [_outsider(o, escape) for o in others[:MAX_OTHERS]]
    rest = len(others) - len(shown)
    names = (
        joined([*shown, f"{rest} {'other' if rest == 1 else 'others'}"]) if rest else joined(shown)
    )
    return NO_ONE_BUT.format(names=names)


def left_note(ended: str) -> str:
    """Why a character left, as a picker shows it under the name: "Left the party", "Died"."""
    word = LEFT_WORDS.get(ended, LEFT_WORDS["left"])
    return word[:1].upper() + word[1:]


def _outsider(o: Outsider, escape: Callable[[str], str]) -> str:
    if o.ended is None:
        return escape(o.name)
    return f"{escape(o.name)} ({LEFT_WORDS.get(o.ended, 'left the party')})"


def ordered_outsiders(
    left: Mapping[str, tuple[str, str]], npcs: Mapping[str, str]
) -> list[Outsider]:
    """Characters who left (id → (name, why)), then NPCs (id → name), each by name: the order the
    🤫 line and the pickers show them in."""
    gone = sorted(left.items(), key=lambda kv: kv[1][0].casefold())
    others = sorted(npcs.items(), key=lambda kv: kv[1].casefold())
    return [Outsider(i, n, why) for i, (n, why) in gone] + [Outsider(i, n) for i, n in others]


__all__ = [
    "EVERYONE",
    "NO_ONE",
    "NO_PARTY",
    "WHOLE",
    "Outsider",
    "joined",
    "left_note",
    "line",
    "ordered_outsiders",
]
