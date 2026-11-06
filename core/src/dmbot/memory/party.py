"""The player characters who played a session together know each other, and form The party (#3036).

Playing a session together is a fact DMbot already holds: who sat at the table and which character
each person plays. So this is written by rule, never guessed by the AI, and never for an NPC. After
an import or a live session, every player character whose player took part gets a `knows` with
every other one (one connection a pair, either way round counts as having it) and `member_of` "The
party", one faction the campaign keeps for them (found by its source, so the DM can rename it).

The DM can remove any of these like any connection, and a removed one is not added again: the rule
leaves a connection that exists in any state (a rejected one included) as it is. Everything is one
change, so one Undo takes the whole step back. The connections are the DM's to say, confirmed and in
the open: the players sat at the same table.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from dmbot.memory._changes import ALIASES, ENTITIES, Changes
from dmbot.memory.models import CONFIRMED, PARTY_SOURCE, PLAYER_CHARACTER, REJECTED, new_id

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
    """The confirmed player characters these players play (their newest each), by entry id: the
    ones who took part. A merged entry or an unconfirmed one is not one."""
    if not user_ids:
        return []
    rows = await w.select(
        ENTITIES,
        " AND role = %s AND status = %s AND played_by = ANY(%s) AND merged_into IS NULL",
        [PLAYER_CHARACTER, CONFIRMED, sorted(user_ids)],
    )
    newest: dict[int, dict[str, Any]] = {}
    for row in rows:
        best = newest.get(int(row["played_by"]))
        if best is None or (row["created_at"], row["id"]) > (best["created_at"], best["id"]):
            newest[int(row["played_by"])] = row
    return sorted(str(r["id"]) for r in newest.values())


async def members_of(w: Changes) -> list[str]:
    """The party's members (#3363, Supervisor 2026-11-05): the campaign's confirmed player
    characters that have a player, the newest of each player, whether or not that player spoke in a
    given session. So lore first, a session first or an assignment later all give the same members.
    A merged or unconfirmed entry is not one, and a character with no player is not a member. A
    replaced character (the player's older one) is no longer the newest: it leaves as history, not
    deleted."""
    rows = await w.select(
        ENTITIES,
        " AND role = %s AND status = %s AND played_by IS NOT NULL AND merged_into IS NULL",
        [PLAYER_CHARACTER, CONFIRMED],
    )
    newest: dict[int, dict[str, Any]] = {}
    for row in rows:
        best = newest.get(int(row["played_by"]))
        if best is None or (row["created_at"], row["id"]) > (best["created_at"], best["id"]):
            newest[int(row["played_by"])] = row
    return sorted(str(r["id"]) for r in newest.values())


async def is_replaced(w: Changes, entity_id: str, current: Collection[str]) -> bool:
    """Is this player character no longer its player's newest one (another entry of the same
    player is a current member)? Then its party tie is history. A character with no player, or a
    merged one, is never judged here: its tie is the DM's to keep or remove."""
    row = await w.get(ENTITIES, entity_id)
    if row is None or row["role"] != PLAYER_CHARACTER or row["played_by"] is None:
        return False
    if row["merged_into"] is not None:
        return False
    mine = await w.select(
        ENTITIES, " AND played_by = %s AND role = %s AND id = ANY(%s)",
        [row["played_by"], PLAYER_CHARACTER, sorted(current)],
    )  # fmt: skip
    return bool(mine)


async def find_or_make(w: Changes) -> tuple[str | None, bool]:
    """(the party faction's id, whether it was just made). Found by its source, whatever the DM
    renamed it to. One the DM rejected is their word that there is no such group: no id (None),
    nothing is made again and nobody is added to it. A merged one is gone, and a new one is made."""
    rows = await w.select(ENTITIES, " AND source = %s AND type = 'faction'", [PARTY_SOURCE])
    live = [r for r in rows if r["status"] == CONFIRMED and r["merged_into"] is None]
    if live:
        return str(min(live, key=lambda r: (r["created_at"], r["id"]))["id"]), False
    if any(r["status"] == REJECTED for r in rows):
        return None, False
    row = await w.insert(
        ENTITIES,
        {
            "id": new_id(), "type": "faction", "name": PARTY_NAME, "description": "",
            "status": CONFIRMED, "merged_into": None, "source": PARTY_SOURCE,
            "created_at": w.now, "played_by": None, "role": None, "needs_look": False,
            "unique_item": None, "hidden_from_players": False,
        },
    )  # fmt: skip
    from dmbot.memory.store import _new_alias  # (the store imports this module)

    await w.insert(ALIASES, _new_alias(w, row["id"], PARTY_NAME, "full", CONFIRMED, False, None))
    return str(row["id"]), True
