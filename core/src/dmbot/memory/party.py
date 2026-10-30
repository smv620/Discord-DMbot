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
