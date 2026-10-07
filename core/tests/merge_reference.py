"""The row-by-row merge from before #164, kept as a reference: the set-based merge
must leave every table, flag and change-log row exactly as this did (#342 review).
Only change: the walk goes oldest first, as the new one does (the old query had no
ORDER BY, so its order was whatever Postgres returned)."""

from __future__ import annotations

from dmbot.memory import store as s
from dmbot.memory._changes import ALIASES, CORRECTIONS, ENTITIES, MENTIONS, RELATIONS, Changes
from dmbot.memory.checks import duplicate_of, ordered
from dmbot.memory.models import CONFIRMED, DM, MERGED, REJECTED, MemoryRuleError, lookup_key


async def merge(
    memory: s.MemoryStore,
    guild_id: int,
    campaign_id: str,
    keep_id: str,
    gone_id: str,
    *,
    source: str,
    dm_said_same: bool = False,
) -> int | None:
    async with memory._write(guild_id, campaign_id, source) as w:
        keep = await s._entity_row(w, keep_id)
        gone = await s._entity_row(w, gone_id)
        needs_dm = CONFIRMED in (keep["status"], gone["status"]) or keep["type"] != gone["type"]
        if needs_dm and not dm_said_same:
            raise MemoryRuleError("Only the DM can say these two are the same.")
        if gone["played_by"] is not None and keep["played_by"] is None:
            await w.update(
                ENTITIES, keep_id, {"type": gone["type"], "played_by": gone["played_by"]}
            )
        await _move_aliases(w, keep_id, gone_id)
        own = await w.select(
            ALIASES, " AND entity_id = %s AND key = %s", [keep_id, lookup_key(keep["name"])]
        )
        if own and own[0]["secret"]:
            raise MemoryRuleError("A secret name can't be the main name.")
        await _move_relations(w, keep_id, gone_id)
        for table in (MENTIONS, CORRECTIONS):
            for row in await w.select(table, " AND entity_id = %s", [gone_id]):
                await w.update(table, row["id"], {"entity_id": keep_id})
        await w.update(ENTITIES, keep_id, {"status": s._stronger(keep["status"], gone["status"])})
        await w.update(ENTITIES, gone_id, {"status": MERGED, "merged_into": keep_id})
        return w.batch


async def _move_aliases(w: Changes, keep_id: str, gone_id: str) -> None:
    keep_aliases = {a["key"]: a for a in await w.select(ALIASES, " AND entity_id = %s", [keep_id])}
    for alias in await w.select(ALIASES, " AND entity_id = %s", [gone_id]):
        twin = keep_aliases.get(alias["key"])
        if twin is None:
            await w.update(ALIASES, alias["id"], {"entity_id": keep_id})
            continue
        upgrade = s._upgrade(twin, alias["status"], alias["secret"], DM)
        await w.delete(ALIASES, alias["id"])
        if upgrade:
            await w.update(ALIASES, twin["id"], upgrade)
    for alias in await w.select(ALIASES, " AND used_by = %s", [gone_id]):
        await w.update(ALIASES, alias["id"], {"used_by": keep_id})


async def _move_relations(w: Changes, keep_id: str, gone_id: str) -> None:
    onto = await s._load_ontology(w)
    for row in await w.select(
        RELATIONS,
        " AND (subject_id = %s OR object_id = %s) ORDER BY created_at, id",
        [gone_id, gone_id],
    ):
        if await w.get(RELATIONS, row["id"]) is None:
            continue  # removed earlier in the walk (folded into a twin)
        subject = keep_id if row["subject_id"] == gone_id else row["subject_id"]
        obj = keep_id if row["object_id"] == gone_id else row["object_id"]
        if subject == obj:
            await s._delete_relation(w, row["id"])
            continue
        pred = onto.predicates.get(row["predicate"])
        if pred is not None:
            subject, obj = ordered(pred, subject, obj)
        moved = s._relation(
            await w.update(RELATIONS, row["id"], {"subject_id": subject, "object_id": obj})
        )
        others = [r for r in await s._relations_touching(w, subject, obj) if r.id != moved.id]
        twin = duplicate_of(moved, others)
        if twin is not None:
            upgrade = s._upgrade(s._relation_row(twin), moved.status, moved.secret, DM)
            await s._delete_relation(w, moved.id)
            if upgrade:
                await w.update(RELATIONS, twin.id, upgrade)
            continue
        if pred is not None and moved.status != REJECTED:
            types = {e: (await s._entity_row(w, e))["type"] for e in (subject, obj)}
            await s._flag_problems(w, onto, moved, types[subject], types[obj], others)
