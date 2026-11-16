"""Public never becomes secret by itself (#4524, the owner's rule; Supervisor's ruling on #4524):
without the DM's own choice, DMbot never takes knowledge away from the players. Nothing they are
shown is hidden or made secret, and no player character who knows something stops knowing it.

The one guard every write passes through (`Changes.update` / `update_rows` call `hold` for entries,
other names and connections; the knowers setters keep the player characters who knew an item,
`knowers._players_kept`, and ask with `ask_knowers`): a write that is not the
DM's own choice to make something secret (`Changes.dm_chose_secret`, set only by the DM's 👁️ / 🤫 /
Who knows buttons and Make it secret) keeps a public item as it is, and asks the DM instead with a
🤫 question (`secret_questions`), counting what it held back on the write (`Changes.held_back`) for
its caller's notice. The rest of the write goes ahead: an upload still adds its new things, a merge
still merges. Adding knowers or revealing is never held. Undo and a restore are the DM's own acts
and pass through (as `Changes._knowers_follow` treats them).

"Public" (the ruling's D): shown to the players by its flags (`knowers.entry_shown` and its kin),
or known to a player character, an awake who-knows row at any certainty (a guess counts)."""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from dmbot.memory._changes import ALIAS_KNOWERS, ENTITIES, KNOWERS, PERSPECTIVES, Table
from dmbot.memory.models import PC_VIEW

if TYPE_CHECKING:
    from dmbot.memory._changes import Changes

# The column values that take an item away from the players, by table.
NARROWING: dict[str, frozenset[str]] = {
    "memory_entities": frozenset({"hidden_from_players", "audience_limited"}),
    "memory_aliases": frozenset({"secret"}),
    "memory_relations": frozenset({"secret", "unrevealed"}),
}
_KNOWN_COLUMN = {
    "memory_entities": "about_entity_id",
    "memory_aliases": "alias_id",
    "memory_relations": "relation_id",
}


def narrows(table: str, change: tuple[str, Any]) -> bool:
    """Whether setting this column to this value would take the item away from the players."""
    column, value = change
    return value is True and column in NARROWING.get(table, ())


# The points of view whose rows make an item public (read only: the guard never makes one). A
# player character's, including one who left the party (party_knowledge.md §10: the guard protects
# their rows too). #4537 slice 1 adds the "everyone" view (common knowledge) here, its third case.
PUBLIC_VIEW_KINDS: tuple[str, ...] = (PC_VIEW,)


async def player_views(w: Changes) -> set[str]:
    """The points of view whose knowing makes an item public (`PUBLIC_VIEW_KINDS`)."""
    return {
        str(r["id"])
        for r in await w.select(PERSPECTIVES, " AND kind = ANY(%s)", [list(PUBLIC_VIEW_KINDS)])
    }


async def known_to_players(w: Changes, table: str, item_id: str) -> bool:
    """Whether a player character knows this item: an awake who-knows row, any certainty."""
    views = await player_views(w)
    if not views:
        return False
    rows = await w.select(
        KNOWERS,
        f" AND {_KNOWN_COLUMN[table]} = %s AND perspective_id = ANY(%s)",
        [item_id, sorted(views)],
    )
    if rows:
        return True
    if table == "memory_aliases":  # (the older per-name list, #2935)
        old = await w.select(
            ALIAS_KNOWERS,
            " AND alias_id = %s AND perspective_id = ANY(%s)",
            [item_id, sorted(views)],
        )
        return bool(old)
    return False


async def is_public(w: Changes, table: Table, row: dict[str, Any]) -> bool:
    """Shown to the players by its flags, or known to a player character (the ruling's D)."""
    from dmbot.memory import knowers

    if table.name == "memory_entities":
        shown = knowers.entry_shown(row)
    elif table.name == "memory_aliases":
        owner = await w.get(ENTITIES, row["entity_id"], lock=False)
        shown = knowers.alias_shown(row) and owner is not None and knowers.entry_shown(owner)
    else:  # (a connection the players see: shown itself, and both its ends shown)
        ends = {
            str(e["id"]): e
            for e in await w.select(
                ENTITIES, " AND id = ANY(%s)", [[row["subject_id"], row["object_id"]]]
            )
        }
        shown = knowers.relation_shown(row) and knowers.ends_shown(ends, row)
    return shown or await known_to_players(w, table.name, row[table.key])


async def hold(
    w: Changes, table: Table, before: dict[str, Any], changes: dict[str, Any]
) -> dict[str, Any]:
    """`changes` without what would take a public item away from the players, unless the DM
    chose it; what is held back becomes a 🤫 question (asked once for each item and change)."""
    if w.dm_chose_secret or w.undoes is not None or w.joining:
        return changes
    taking = {c: v for c, v in changes.items() if narrows(table.name, (c, v)) and not before.get(c)}
    if not taking or not await is_public(w, table, before):
        return changes
    from dmbot.memory import secret_questions

    await secret_questions.ask(w, secret_questions.NARROW, table.name, before[table.key], taking)
    return {c: v for c, v in changes.items() if c not in taking}


async def ask_knowers(w: Changes, table: str, item_id: str, entity_ids: Collection[str]) -> None:
    """A knowers setter kept these player characters on an item they knew (#4524): one 🤫 question
    with what it would have done, `{"knowers_off": [their entry ids]}`."""
    from dmbot.memory import secret_questions

    await secret_questions.ask(
        w, secret_questions.NARROW, table, item_id, {"knowers_off": sorted(entity_ids)}
    )
