"""The player characters who played a session together know each other, and form The party (#3036).

Playing a session together is a fact DMbot already holds: who sat at the table and which character
each person plays. So this is written by rule, never guessed by the AI, and never for an NPC. After
an import or a live session, every player character whose player took part gets a `knows` with
every other one (one connection a pair, either way round counts as having it). Every confirmed
player character, its player "pending" too (#3363, #3995), is a `member_of` "The party", the one
entry with the `party` flag (#3954).

The DM can remove any of these like any connection, and a removed one is not added again: the rule
leaves a connection that exists in any state (a rejected one included) as it is. Everything is one
change, so one Undo takes the whole step back. The connections are the DM's to say, confirmed and in
the open: the players sat at the same table.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from dmbot.memory._changes import ALIASES, ENTITIES, RELATIONS, Changes
from dmbot.memory.models import (
    CONFIRMED,
    PARTY_SOURCE,
    PLAYER_CHARACTER,
    REJECTED,
    name_key,
    new_id,
)

PARTY_NAME = "The party"
KNOWS, MEMBER_OF = "knows", "member_of"


@dataclass(frozen=True, slots=True)
class PartyLinked:
    """What one run did, as counts only: the player characters who took part, the `knows` pairs
    and `member_of` links it added, and whether it made the party faction."""

    characters: int
    pairs: int
    members: int
    made_party: bool


def party_notice(characters: int) -> str:
    """One plain line for the DM screen after an import linked the party (#3036): how many player
    characters, never which. The step takes moments, so it is told as a result, not as a wait."""
    if characters == 1:
        return "🎭 **The party** has 1 player character."
    return f"🎭 **The party** is set up: {characters} player characters grouped together."


async def characters_of(w: Changes, user_ids: Collection[int]) -> list[str]:
    """The confirmed player characters these players play, all of them (the owner, 2026-11-10: a
    player may play several at once, #3998), by entry id: the ones who took part. A merged entry or
    an unconfirmed one is not one."""
    if not user_ids:
        return []
    rows = await w.select(
        ENTITIES,
        " AND role = %s AND status = %s AND played_by = ANY(%s) AND merged_into IS NULL",
        [PLAYER_CHARACTER, CONFIRMED, sorted(user_ids)],
    )
    return sorted(str(r["id"]) for r in rows)


async def members_of(w: Changes) -> list[str]:
    """The party's members: every confirmed, live player character of the campaign, whoever plays
    it, its player "pending" too (#3363, #3995). A player may play several at once (the owner,
    2026-11-10, #3998), so none leaves for being older: a character leaves the party only when the
    DM removes its membership, and that removal sticks (`MemoryStore.link_party`)."""
    rows = await w.select(
        ENTITIES,
        " AND role = %s AND status = %s AND merged_into IS NULL",
        [PLAYER_CHARACTER, CONFIRMED],
    )
    return sorted(str(r["id"]) for r in rows)


async def closed_beside_another(w: Changes, party_id: str) -> int:
    """How many live player characters' party memberships were ended (not removed) while their
    player has another player character: what the old "only the newest" rule closed (#3998), and
    any the DM ended by hand, which the store can't tell apart. Counted for Supervisor; never
    reopened by DMbot."""
    ties = await w.select(
        RELATIONS,
        " AND predicate = %s AND object_id = %s AND status = %s AND source = 'dm'",
        [MEMBER_OF, party_id, CONFIRMED],
    )
    still_in = {str(r["subject_id"]) for r in ties if r["to_session_at"] is None}
    ended = [
        r for r in ties if r["to_session_at"] is not None and str(r["subject_id"]) not in still_in
    ]  # (one the DM added back is in again, not counted)
    if not ended:
        return 0
    chars = {
        str(r["id"]): r["played_by"]
        for r in await w.select(
            ENTITIES,
            " AND role = %s AND status = %s AND merged_into IS NULL",
            [PLAYER_CHARACTER, CONFIRMED],
        )
    }
    per_player = Counter(p for p in chars.values() if p is not None)
    return sum(1 for r in ended if per_player[chars.get(str(r["subject_id"]))] > 1)


# The names a group the DM made for the party goes by, for the one-time fill (#3954, Supervisor's
# ruling: "the adventurers" added; not "the company" or "the heroes").
PARTY_NAMES = frozenset({"the party", "party", "our party", "the group", "the adventurers"})


@dataclass(frozen=True, slots=True)
class Filled:
    """What the fill did for one campaign, as a step name for the log: `had` (a flagged party),
    `rejected`, `linked` (#3036's party flagged), `members` (a group of exactly its player
    characters), `named` (a group named like the party), `made`. Counts only."""

    step: str
    party_id: str | None
    knower_rows: int = 0
    # A live #3036 party besides the flagged one (two "party" entries, from before #3954): the
    # flagged one wins for every reader; counted for Supervisor, never merged by DMbot.
    both: bool = False
    # Memberships ended (not removed) while the character's player has another (#3998): what the
    # old "only the newest of each player" rule closed, or the DM ended by hand. Counted, never
    # reopened (the two can't be told apart).
    ended: int = 0


async def flagged(w: Changes) -> dict[str, Any] | None:
    """The campaign's party: the entry with the `party` flag (one per campaign, a unique index;
    the flag moves with a merge). Whatever the DM renamed it to."""
    rows = await w.select(ENTITIES, " AND party")
    return rows[0] if rows else None


async def find_or_make(w: Changes) -> tuple[str | None, bool]:
    """(the party's id, whether it was just made). The flagged entry, live; one the DM rejected or
    put aside is their word that there is no such group: no id (None), nothing is made again and
    nobody is added to it (only the DM's own `ensure_party` brings it back). Otherwise a group the
    campaign already uses for the party is flagged (`fill`), else one is made."""
    filled = await fill(w)
    return filled.party_id, filled.step == "made"


async def fill(w: Changes) -> Filled:
    """The one party of the campaign (#3954), in Supervisor's order, the first that applies:
    the flagged entry; none when the DM rejected it (or the #3036 one) or put it aside; else the
    party #3036 linked (`source = 'party'`); else a group whose members are exactly its member
    player characters; else a group named like the party (`PARTY_NAMES`) with no member but them,
    the oldest each time; else a new one. The party is always shown (a trigger shows a flagged
    entry), so a group found by its name alone must be shown already: it may be the DM's own
    secret. A clue only some know is never adopted. A found group gets the flag, in the caller's
    change (one Undo)."""
    row = await flagged(w)
    if row is not None:
        if row["status"] == CONFIRMED and row["merged_into"] is None:
            other = await w.select(
                ENTITIES,
                " AND source = %s AND type = 'faction' AND status = %s AND merged_into IS NULL"
                " AND id <> %s",
                [PARTY_SOURCE, CONFIRMED, row["id"]],
            )
            return Filled("had", str(row["id"]), both=bool(other))
        return Filled("rejected", None)  # (rejected, or put aside: the DM's to bring back)
    if any(r["status"] == REJECTED for r in await w.select(
        ENTITIES, " AND source = %s AND type = 'faction'", [PARTY_SOURCE]
    )):  # fmt: skip
        return Filled("rejected", None)  # (#3036's rule: a rejected linked party stays gone)
    groups = sorted(
        (
            r for r in await w.select(
                ENTITIES,
                " AND type = 'faction' AND status = %s AND merged_into IS NULL"
                " AND NOT audience_limited",
                [CONFIRMED],
            )
        ),
        key=lambda r: (r["created_at"], r["id"]),
    )  # fmt: skip
    found = next((r for r in groups if r["source"] == PARTY_SOURCE), None)
    step = "linked"
    if found is None:
        members = set(await members_of(w))
        of = await _members(w, groups)
        # (a group of exactly the party's characters IS the party: the players know it, so it is
        # adopted even while it waits to be said, as every name the DM adds does, #2404)
        found, step = _by_members(groups, of, members), "members"
        if found is None:
            # (by its name alone it may be the DM's own secret group: only one already shown)
            shown = [g for g in groups if not g["hidden_from_players"]]
            found, step = await _by_name(w, shown, of, members), "named"
    if found is not None:
        await w.update(ENTITIES, found["id"], {"party": True})
        return Filled(step, str(found["id"]))
    return Filled("made", await _make(w))


async def _members(w: Changes, groups: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Each group's current confirmed members (`member_of`)."""
    if not groups:
        return {}
    links = await w.select(
        RELATIONS,
        " AND predicate = %s AND status = %s AND to_session_at IS NULL AND object_id = ANY(%s)",
        [MEMBER_OF, CONFIRMED, [str(g["id"]) for g in groups]],
    )
    of: dict[str, set[str]] = {}
    for r in links:
        of.setdefault(str(r["object_id"]), set()).add(str(r["subject_id"]))
    return of


def _by_members(
    groups: list[dict[str, Any]], of: dict[str, set[str]], members: set[str]
) -> dict[str, Any] | None:
    if not members:
        return None
    return next((g for g in groups if of.get(str(g["id"])) == members), None)


async def _by_name(
    w: Changes, groups: list[dict[str, Any]], of: dict[str, set[str]], members: set[str]
) -> dict[str, Any] | None:
    """A group named like the party, by its name or a confirmed public other name, whose members
    are only the party's characters (or none): a group with an NPC in it is the DM's own faction,
    and adopting it would stop its knowledge reaching its NPCs."""
    if not groups:
        return None
    rows = await w.select(
        ALIASES, " AND status = %s AND NOT secret AND entity_id = ANY(%s)",
        [CONFIRMED, [str(g["id"]) for g in groups]],
    )  # fmt: skip
    named = {str(r["entity_id"]) for r in rows if name_key(str(r["text"])) in PARTY_NAMES}
    named |= {str(g["id"]) for g in groups if name_key(str(g["name"])) in PARTY_NAMES}
    return next(
        (g for g in groups if str(g["id"]) in named and of.get(str(g["id"]), set()) <= members),
        None,
    )


async def _make(w: Changes) -> str:
    """A new party: a confirmed group, "The party", flagged, as the DM's write. Always shown to the
    players (they are in it)."""
    row = await w.insert(
        ENTITIES,
        {
            "id": new_id(), "type": "faction", "name": PARTY_NAME, "description": "",
            "status": CONFIRMED, "merged_into": None, "source": PARTY_SOURCE,
            "created_at": w.now, "played_by": None, "role": None, "needs_look": False,
            "unique_item": None, "hidden_from_players": False, "party": True,
        },
    )  # fmt: skip
    from dmbot.memory.store import _new_alias  # (the store imports this module)

    await w.insert(ALIASES, _new_alias(w, row["id"], PARTY_NAME, "full", CONFIRMED, False, None))
    return str(row["id"])
