"""Who knows each found row, scored against the key's `knowers` (#4523 slice 2, as ruled).

The built keys (`total_key.json`, `full_key.json`) say on every entry and tie who should know it:
`"party"` or `"everyone"` (every player character), `"dm"` (nobody), or a list of characters' names
(player characters, and NPCs with a point of view such as Wit). This module asks the backup the same
question the product asks: it rebuilds the gate's lists (`gate.Statements`) from the backup's own
rows and lets `gate.visible` say what each character knows, so the score follows the one rule the
bot follows, never a second copy of it. A row put to sleep by a hide (`hidden_by`) grants nothing,
as in the gate.

Player characters are judged first (the ruling): a player character who knows what the key keeps
from them is a **leak**, a failure on its own line; one who should know and doesn't is **too
narrow**. An NPC knower is only judged where the key lists names (or says `"dm"`): a wrong one is
its own counted line, never a failure. Only NPCs the key names are asked; one the key never names
can't be judged. Ids and counts only: no name from the backup is printed.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from dmbot.memory import gate

Verdict = Literal["right", "leaked", "too narrow"]
NpcVerdict = Literal["", "npc right", "npc wrong"]
RIGHT: Verdict = "right"
LEAKED: Verdict = "leaked"
NARROW: Verdict = "too narrow"
NPC_RIGHT: NpcVerdict = "npc right"
NPC_WRONG: NpcVerdict = "npc wrong"
WHOLE_PARTY = frozenset({"party", "everyone"})
NOBODY = "dm"
# (an entry or tie is "known" by a character in the products' own sense: a row of its own for that
# character, or a group's. An entry is known by its own name only: knowing a connection, a property
# or another name (a disguise) makes the gate count its entries as known of, but the key's entry
# `knowers` say who knows the name, as `gate.display_name` reads it, so a cover name never leaks.)


@dataclass(frozen=True, slots=True)
class Scored:
    """Who knows one found key row, against the key."""

    section: str  # "entry" or "tie"
    id: str
    verdict: Verdict  # the player characters'
    npc: NpcVerdict = ""  # where the key lists names (or says "dm"); "" where it doesn't judge
    check: bool = False  # the key marks this row's knowers as the owner's to check


def _ids(rows: Iterable[Mapping[str, Any]], *, skip: Collection[str] = ()) -> set[str]:
    return {str(r["id"]) for r in rows if str(r.get("status")) not in skip}


def statements(tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> gate.Statements:
    """The gate's lists, from a backup's memory rows instead of the database (`gate.load`'s rules:
    a statement that was turned down or merged away grants nothing, a group's row reaches its
    confirmed members unless the group is the party, and a sleeping row is left out)."""
    views = {
        str(p["id"]): str(p["entity_id"])
        for p in tables.get("perspective", ())
        if p.get("entity_id") is not None
    }
    entities = tables.get("entity", ())
    live_entries = _ids(entities, skip=("rejected", "merged"))
    states = {str(s["id"]): str(s["entity_id"]) for s in tables.get("state", ())}
    relations = {
        str(r["id"]): (str(r["subject_id"]), str(r["object_id"]))
        for r in tables.get("relation", ())
        if r.get("status") != "rejected"
    }
    aliases = {
        str(a["id"]): tuple(str(i) for i in (a.get("entity_id"), a.get("used_by")) if i)
        for a in tables.get("alias", ())
        if a.get("status") != "rejected"
    }
    columns = (
        (gate.ENTRY, "about_entity_id", live_entries),
        (gate.PROPERTY, "state_id", states),
        (gate.CONNECTION, "relation_id", relations),
        (gate.ALIAS, "alias_id", aliases),
    )
    knowers: dict[tuple[str, str], dict[str, float]] = {}
    for row in tables.get("knower", ()):
        character = views.get(str(row.get("perspective_id")))
        if character is None or row.get("hidden_by"):
            continue  # (no character, or asleep under a hide: the gate never reads it)
        for kind, column, live in columns:
            statement = row.get(column)
            if statement is not None and str(statement) in live:
                knowers.setdefault((kind, str(statement)), {})[character] = float(
                    row.get("confidence", 1.0)
                )
    party = {str(e["id"]) for e in entities if e.get("party")}
    groups: dict[str, set[str]] = {}
    for r in tables.get("relation", ()):
        if (
            r.get("predicate") == "member_of"
            and r.get("status") == "confirmed"
            and str(r["object_id"]) not in party
        ):
            groups.setdefault(str(r["subject_id"]), set()).add(str(r["object_id"]))
    told: dict[str, set[str]] = {}
    for row in tables.get("told", ()):
        if str(row["alias_id"]) in aliases:
            told.setdefault(str(row["alias_id"]), set()).add(str(row["character_id"]))
    return gate.Statements(
        knowers=knowers,
        groups={m: frozenset(g) for m, g in groups.items()},
        states=states,
        connections=relations,
        aliases=aliases,
        player_characters=frozenset(
            str(e["id"])
            for e in entities
            if e.get("role") == "player_character" and e.get("status") == "confirmed"
        ),
        told={a: frozenset(c) for a, c in told.items()},
    )


class Knowers:
    """What each of the key's characters knows in one backup, asked once per character."""

    def __init__(
        self,
        tables: Mapping[str, Sequence[Mapping[str, Any]]],
        characters: Mapping[str, set[str]],
        players: Collection[str],
    ) -> None:
        data = statements(tables)
        self.players = frozenset(players)
        # (a character the backup has no entry for knows nothing: its rows score as too narrow)
        self.seen = {
            name: gate.visible(gate.Viewer.player(ids), data) if ids else gate.Visible()
            for name, ids in characters.items()
        }

    def who(self, kind: str, statement_ids: Collection[str], among: Iterable[str]) -> set[str]:
        """The characters in `among` who know any of `statement_ids` (one key row can be held by
        more than one stored row; any of them known is known)."""

        def knows(seen: gate.Visible, s: str) -> bool:
            if kind == gate.ENTRY:  # (by its own name: see the note at the top)
                return (gate.ENTRY, s) in seen.sureness
            return seen.knows(kind, s)

        return {name for name in among if any(knows(self.seen[name], s) for s in statement_ids)}

    def score(
        self,
        section: str,
        row_id: str,
        knowers: str | Sequence[str],
        kind: str,
        statement_ids: Collection[str],
        *,
        itself: str = "",
        check: bool = False,
    ) -> Scored:
        """One found key row. `itself`: the entry's own name, for an entry row, since every
        character knows it exists and the key need not say so."""
        if isinstance(knowers, str) and knowers not in WHOLE_PARTY | {NOBODY}:
            # (a typo such as "Party" would turn every player character who knows it into a leak)
            raise ValueError(f"{row_id}: knowers must be party, everyone, dm or a list of names")
        if isinstance(knowers, str):
            want_pcs = set(self.players) if knowers in WHOLE_PARTY else set()
            want_npcs: set[str] | None = None if knowers in WHOLE_PARTY else set()
        else:
            want_pcs = set(knowers) & self.players
            want_npcs = {k for k in knowers if k not in self.players and k != itself}
        has = self.who(kind, statement_ids, self.players)
        if itself in self.players:
            want_pcs.add(itself)
        verdict: Verdict = LEAKED if has - want_pcs else NARROW if want_pcs - has else RIGHT
        npc: NpcVerdict = ""
        if want_npcs is not None:
            judged = [n for n in self.seen if n not in self.players and n != itself]
            npc = NPC_RIGHT if self.who(kind, statement_ids, judged) == want_npcs else NPC_WRONG
        return Scored(section, row_id, verdict, npc, check)
