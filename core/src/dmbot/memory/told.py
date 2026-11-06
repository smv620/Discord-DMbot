"""Telling one character that a secret other name is an entry: "Tavin now knows the guard is Ken"
(#3089). Only the DM, per character, one change (so one Undo), and a way back that does not depend
on Undo (`take_back`).

What it stores is only what one character was told (`memory_told`), never a fact about the story.
Telling also gives the character the entry itself, its name only and none of its facts, because
without it they would hold a name with nowhere to fold it; `added_entry` remembers that this
telling gave it, so taking the telling back removes that row only then. Nothing else widens: the
character learns no fact they did not know (#3037: a join never teaches anyone anything). The gate
(`memory.gate`) is the one place that turns these rows into what a character sees.
"""

from __future__ import annotations

from dmbot.memory import knowers
from dmbot.memory._changes import ALIASES, ENTITIES, KNOWERS, PERSPECTIVES, TOLD, Changes
from dmbot.memory.models import CONFIRMED, DM, MemoryRuleError, new_id

NOT_A_SECRET_NAME = "Only a secret name can be told to one character."
DOES_NOT_HOLD_IT = "That character does not know that name."
NOT_TOLD = "That character was not told this."


async def tell(w: Changes, alias_id: str, character_id: str) -> bool:
    """The character now knows the other name is the entry it belongs to. Returns whether anything
    changed (telling again changes nothing). Refused unless it is the DM, the name is a confirmed
    secret name of a live entry, and the character is a confirmed character who knows the name."""
    if w.source != DM:
        raise MemoryRuleError(knowers.ONLY_THE_DM)
    alias = await w.get(ALIASES, alias_id)
    if alias is None or alias["status"] != CONFIRMED:
        raise MemoryRuleError(knowers.NO_SUCH_STATEMENT)
    if not alias["secret"]:
        raise MemoryRuleError(NOT_A_SECRET_NAME)
    entry = await w.get(ENTITIES, str(alias["entity_id"]))
    if entry is None or entry["status"] != CONFIRMED:
        raise MemoryRuleError(knowers.NO_SUCH_STATEMENT)
    await knowers.check_characters(w, [character_id])
    if await w.select(TOLD, " AND alias_id = %s AND character_id = %s", [alias_id, character_id]):
        return False
    views = await w.select(PERSPECTIVES, " AND entity_id = %s", [character_id])
    holds = bool(
        views
        and await w.select(
            KNOWERS, " AND alias_id = %s AND perspective_id = %s", [alias_id, views[0]["id"]]
        )
    )
    if not holds:
        raise MemoryRuleError(DOES_NOT_HOLD_IT)
    view_id = str(views[0]["id"])
    has_entry = bool(
        await w.select(
            KNOWERS,
            " AND about_entity_id = %s AND perspective_id = %s",
            [alias["entity_id"], view_id],
        )
    )
    if not has_entry:
        await w.insert(
            KNOWERS,
            {"id": new_id(), "perspective_id": view_id, "about_entity_id": alias["entity_id"],
             "state_id": None, "relation_id": None, "alias_id": None, "created_at": w.now,
             "confidence": 1.0},
        )  # fmt: skip
    await w.insert(
        TOLD,
        {"id": new_id(), "alias_id": alias_id, "character_id": character_id,
         "added_entry": not has_entry, "created_at": w.now},
    )  # fmt: skip
    return True


async def take_back(w: Changes, alias_id: str, character_id: str) -> None:
    """The DM takes a telling back: the character holds the name and nothing more, as before.
    Removes the telling and, only when this telling gave it, the entry's row. Refused when the
    character was not told. Does not depend on Undo: later changes cannot stop it."""
    if w.source != DM:
        raise MemoryRuleError(knowers.ONLY_THE_DM)
    rows = await w.select(
        TOLD, " AND alias_id = %s AND character_id = %s", [alias_id, character_id]
    )
    if not rows:
        raise MemoryRuleError(NOT_TOLD)
    told = rows[0]
    await w.delete(TOLD, str(told["id"]))
    if told["added_entry"]:
        alias = await w.get(ALIASES, alias_id)
        views = await w.select(PERSPECTIVES, " AND entity_id = %s", [character_id])
        if alias is not None and views:
            for row in await w.select(
                KNOWERS,
                " AND about_entity_id = %s AND perspective_id = %s",
                [alias["entity_id"], views[0]["id"]],
            ):
                await w.delete(KNOWERS, str(row["id"]))
