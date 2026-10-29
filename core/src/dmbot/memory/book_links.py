"""The book and the table (docs/STORY_MEMORY.md, section 6; #1715): "the book's Captain Varn" and
"our Captain Varn" are joined only by the DM's word, and stay two things.

DMbot suggests by name, with no AI: a person or place that the story plan (read from the DM's
adventure, `memory.story_plan`) holds, and a table entry of the same kind with the same name or
another name it is called (compared the way names are everywhere, `models.name_key`). The DM says
**Same** or **Not the same**; either is remembered, so a pair is never asked about twice. A book
entry is the same as at most one table entry, and a table entry as at most one book entry in each
file. Every decision is written to the memory change log, so Undo works, and goes with the entry
it is about. Nothing here is ever shown to players or put in a transcript: only the DM's screens
read it, and recall (`memory.recall`) never touches it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from psycopg import errors

from dmbot.memory._changes import PLAN_SAME_AS
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    PLAYER_CHARACTER,
    MemoryRuleError,
    Written,
    name_key,
    new_id,
)
from dmbot.memory.store import MemoryStore

SAME = "same"
NOT_SAME = "not_same"
SUGGESTIONS_MAX = 10  # asked about at a time
PICK_MAX = 25  # the most a menu holds

# The table kinds a book kind can be the same as (the book's "people" include named groups).
PERSON_KINDS = ("character", "faction")
PLACE_KINDS = ("place",)

ONLY_DM = "Only the DM can do that."
GONE = "That isn't in your adventure any more, or DMbot no longer remembers it."
CLASH = "That link clashes with another one just made. Look again and try once more."
TWICE = "Two of those would link the same name twice. Pick only one of them."
NOT_MATCHING = (
    "A character in your adventure can only be the same as a character or group DMbot "
    "remembers, and a place as a place."
)
TAKEN_BOOK = (
    "That name in your adventure is already linked to another name DMbot remembers. Take that "
    "one off first."
)
TAKEN_TABLE = (
    "That name DMbot remembers is already linked to another name in this file of your adventure. "
    "Take that one off first."
)


@dataclass(frozen=True, slots=True)
class Pair:
    plan_id: str
    plan_name: str
    plan_kind: str  # person or place
    entity_id: str
    entity_name: str


@dataclass(frozen=True, slots=True)
class Option:
    plan_id: str
    name: str
    kind: str
    chapter: str


@dataclass(frozen=True, slots=True)
class Linked:
    """A table entry's book entry, for the DM's card."""

    plan_id: str
    name: str
    kind: str
    chapter: str  # the chapter the read was in when it found it ("" when none)


def kinds_for(plan_kind: str) -> tuple[str, ...]:
    return PLACE_KINDS if plan_kind == "place" else PERSON_KINDS


# The chapter a book entry was found in: the nearest chapter at or before it in the same file
# (an entry's `position` is where in the text its batch ended).
def _nearest_chapter(alias: str, column: str) -> str:
    return (
        f"(SELECT c.{column} FROM plan_nodes c WHERE c.guild_id = {alias}.guild_id"
        f" AND c.campaign_id = {alias}.campaign_id AND c.document_id = {alias}.document_id"
        " AND c.kind = 'chapter'"
        f" AND c.position <= {alias}.position"
        " ORDER BY c.position DESC, c.created_at DESC, c.id DESC LIMIT 1)"
    )


def chapter_of(alias: str) -> str:
    """SQL for the name of the chapter of the plan entry `alias`: the nearest one at or before it
    (the later when two share a place). The one rule: Guide me uses `chapter_id_of`."""
    return _nearest_chapter(alias, "name")


def chapter_id_of(alias: str) -> str:
    """The same rule, giving the chapter's id."""
    return _nearest_chapter(alias, "id")


_CHAPTER = chapter_of("n")


async def has_plan(memory: MemoryStore, guild_id: int, campaign_id: str) -> bool:
    """Does the campaign hold any person or place read from a book?"""
    async with memory._read(guild_id, campaign_id) as scope:
        cur = await scope.conn.execute(
            "SELECT 1 FROM plan_nodes WHERE guild_id = %s AND campaign_id = %s"
            " AND kind IN ('person', 'place') LIMIT 1",
            scope.ids,
        )
        return await cur.fetchone() is not None


async def suggestions(
    memory: MemoryStore, guild_id: int, campaign_id: str, limit: int = SUGGESTIONS_MAX
) -> list[Pair]:
    """Pairs to ask the DM about: a person or place from the book and a confirmed table entry of
    the same kind that has the same name or another name it is called, which nobody has said yes
    or no to, and where neither is already taken (see the one-to-one rule)."""
    onto = await memory.ontology(guild_id, campaign_id)
    async with memory._read(guild_id, campaign_id) as scope:
        cur = await scope.conn.execute(
            "SELECT n.id, n.name, n.kind, n.document_id FROM plan_nodes n"
            " WHERE n.guild_id = %s AND n.campaign_id = %s AND n.kind IN ('person', 'place')"
            " AND NOT EXISTS (SELECT 1 FROM plan_same_as s WHERE (s.guild_id, s.campaign_id,"
            " s.plan_id, s.status) = (n.guild_id, n.campaign_id, n.id, 'same')) ORDER BY"
            " n.kind, n.name_key, n.id LIMIT 2000",
            scope.ids,
        )
        nodes = await cur.fetchall()
        wanted: dict[str, list[Any]] = {}
        for node in nodes:
            wanted.setdefault(name_key(node["name"]), []).append(node)
        if not wanted:
            return []
        cur = await scope.conn.execute(
            "SELECT DISTINCT a.key, e.id, e.name, e.type FROM memory_aliases a"
            " JOIN memory_entities e ON (e.guild_id, e.campaign_id, e.id)"
            " = (a.guild_id, a.campaign_id, a.entity_id)"
            " WHERE a.guild_id = %s AND a.campaign_id = %s AND a.key = ANY(%s)"
            " AND a.status = %s AND e.status = %s AND e.merged_into IS NULL"
            " AND NOT e.audience_limited AND NOT e.party AND e.role IS DISTINCT FROM %s",
            (*scope.ids, list(wanted), CONFIRMED, CONFIRMED, PLAYER_CHARACTER),
        )
        entries = await cur.fetchall()
        cur = await scope.conn.execute(
            "SELECT plan_id, entity_id, document_id, status FROM plan_same_as"
            " WHERE guild_id = %s AND campaign_id = %s",
            scope.ids,
        )
        decided = await cur.fetchall()
    asked = {(d["plan_id"], d["entity_id"]) for d in decided}
    held = {(d["entity_id"], d["document_id"]) for d in decided if d["status"] == SAME}
    candidates: list[Pair] = []
    seen: set[tuple[str, str]] = set()
    documents = {n["id"]: n["document_id"] for n in nodes}
    for entry in sorted(entries, key=lambda e: (name_key(e["name"]), e["id"])):
        for node in wanted.get(entry["key"], ()):
            kinds = kinds_for(node["kind"])
            pair = (node["id"], entry["id"])
            if (
                pair in seen
                or pair in asked
                or (entry["id"], node["document_id"]) in held
                or not any(onto.is_a(entry["type"], kind) for kind in kinds)
            ):
                continue
            seen.add(pair)
            candidates.append(
                Pair(node["id"], node["name"], node["kind"], entry["id"], entry["name"])
            )
    candidates.sort(key=lambda p: (p.plan_kind, name_key(p.plan_name), p.plan_id, p.entity_id))
    # No two on one page may use the same book entry, or the same table entry in one file: the
    # DM could tick both, and only one can be the same. The other comes on a later page.
    out: list[Pair] = []
    used_nodes: set[str] = set()
    used_entries: set[tuple[str, str]] = set()
    for cand in candidates:
        taken = (cand.entity_id, documents[cand.plan_id])
        if cand.plan_id in used_nodes or taken in used_entries:
            continue
        used_nodes.add(cand.plan_id)
        used_entries.add(taken)
        out.append(cand)
    return out[:limit]


async def decide(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    decisions: Sequence[tuple[str, str, str]],
    user_id: int,
) -> Written[int]:
    """The DM's word on one or more pairs, `(plan id, table entry id, "same" or "not_same")`, in
    one change (one Undo). A pair already decided is changed to the new word. Refused in plain
    words when it would break the one-to-one rule or match a person with a place. Returns how many
    pairs were written."""
    if not decisions:
        return Written(0, None)
    async with memory._write(guild_id, campaign_id, DM) as w:
        done = 0
        said_for: set[tuple[str, str]] = set()
        for plan_id, entity_id, status in decisions:
            if status not in (SAME, NOT_SAME):
                raise MemoryRuleError(GONE)
            node = await _node(w, plan_id)
            entity = await _entity(w, entity_id)
            if node is None or entity is None:
                raise MemoryRuleError(GONE)
            onto = await memory.ontology(guild_id, campaign_id)
            if not any(onto.is_a(entity["type"], k) for k in kinds_for(node["kind"])):
                raise MemoryRuleError(NOT_MATCHING)
            if status == SAME:
                for mark in (("book", plan_id), ("table", f"{entity_id}/{node['document_id']}")):
                    if mark in said_for:
                        raise MemoryRuleError(TWICE)
                    said_for.add(mark)
            if status == SAME:  # which side is taken, said plainly (before the index could say)
                held_book = await w.select(
                    PLAN_SAME_AS,
                    " AND plan_id = %s AND status = %s AND entity_id <> %s",
                    [plan_id, SAME, entity_id],
                )
                if held_book:
                    raise MemoryRuleError(TAKEN_BOOK)
                held_table = await w.select(
                    PLAN_SAME_AS,
                    " AND entity_id = %s AND document_id = %s AND status = %s AND plan_id <> %s",
                    [entity_id, node["document_id"], SAME, plan_id],
                )
                if held_table:
                    raise MemoryRuleError(TAKEN_TABLE)
            existing = await w.select(
                PLAN_SAME_AS, " AND plan_id = %s AND entity_id = %s", [plan_id, entity_id]
            )
            try:
                if existing:
                    await w.update(PLAN_SAME_AS, existing[0]["id"], {"status": status})
                else:
                    await w.insert(
                        PLAN_SAME_AS,
                        {
                            "id": new_id(),
                            "plan_id": plan_id,
                            "document_id": node["document_id"],
                            "entity_id": entity_id,
                            "status": status,
                            "decided_by": user_id,
                            "created_at": w.now,
                        },
                    )
            except errors.UniqueViolation:  # a race the checks above did not see
                raise MemoryRuleError(CLASH) from None
            done += 1
        return Written(done, w.batch)


async def unlink(
    memory: MemoryStore, guild_id: int, campaign_id: str, plan_id: str, entity_id: str
) -> Written[bool]:
    """Take a "same" off (the pair is forgotten, so it may be suggested again). Undoable."""
    async with memory._write(guild_id, campaign_id, DM) as w:
        rows = await w.select(
            PLAN_SAME_AS, " AND plan_id = %s AND entity_id = %s AND status = %s",
            [plan_id, entity_id, SAME],
        )  # fmt: skip
        if not rows:
            return Written(False, None)
        await w.delete(PLAN_SAME_AS, rows[0]["id"])
        return Written(True, w.batch)


async def _node(w: Any, plan_id: str) -> dict[str, Any] | None:
    cur = await w.conn.execute(
        "SELECT id, kind, document_id, name FROM plan_nodes WHERE guild_id = %s"
        " AND campaign_id = %s AND id = %s AND kind IN ('person', 'place')",
        (*w.ids, plan_id),
    )
    row = await cur.fetchone()
    return None if row is None else dict(row)


async def _entity(w: Any, entity_id: str) -> dict[str, Any] | None:
    cur = await w.conn.execute(
        "SELECT id, type, name FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
        " AND id = %s AND status = %s AND merged_into IS NULL AND NOT party"
        " AND role IS DISTINCT FROM %s",
        (*w.ids, entity_id, CONFIRMED, PLAYER_CHARACTER),
    )
    row = await cur.fetchone()
    return None if row is None else dict(row)


async def linked(
    memory: MemoryStore, guild_id: int, campaign_id: str, entity_id: str
) -> list[Linked]:
    """The book entries the DM said are this table entry (for the DM's card only)."""
    async with memory._read(guild_id, campaign_id) as scope:
        cur = await scope.conn.execute(
            f"SELECT n.id, n.name, n.kind, {_CHAPTER} AS chapter FROM plan_same_as s"
            " JOIN plan_nodes n ON (n.guild_id, n.campaign_id, n.id)"
            " = (s.guild_id, s.campaign_id, s.plan_id)"
            " WHERE s.guild_id = %s AND s.campaign_id = %s AND s.entity_id = %s"
            " AND s.status = 'same' ORDER BY n.name_key, n.id",
            (*scope.ids, entity_id),
        )
        return [
            Linked(r["id"], r["name"], r["kind"], r["chapter"] or "") for r in await cur.fetchall()
        ]


async def options_for(
    memory: MemoryStore, guild_id: int, campaign_id: str, entity_id: str, offset: int = 0
) -> tuple[list[Option], int]:
    """The book's people or places a table entry could be the same as, to pick from by hand: those
    of its kind, not yet taken in a file where this entry is already linked, the ones sharing a
    word with its name first, then by name. A page of `PICK_MAX`, and how many there are."""
    onto = await memory.ontology(guild_id, campaign_id)
    async with memory._read(guild_id, campaign_id) as scope:
        cur = await scope.conn.execute(
            "SELECT type, name FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
            " AND id = %s AND status = %s AND merged_into IS NULL AND NOT party"
            " AND role IS DISTINCT FROM %s",
            (*scope.ids, entity_id, CONFIRMED, PLAYER_CHARACTER),
        )
        entity = await cur.fetchone()
        if entity is None:
            return [], 0
        kind = "place" if onto.is_a(entity["type"], "place") else "person"
        if kind == "person" and not any(onto.is_a(entity["type"], k) for k in PERSON_KINDS):
            return [], 0
        cur = await scope.conn.execute(
            f"SELECT n.id, n.name, n.kind, {_CHAPTER} AS chapter FROM plan_nodes n"
            " WHERE n.guild_id = %s AND n.campaign_id = %s AND n.kind = %s"
            " AND NOT EXISTS (SELECT 1 FROM plan_same_as s WHERE (s.guild_id, s.campaign_id,"
            " s.plan_id, s.status) = (n.guild_id, n.campaign_id, n.id, 'same'))"
            " AND NOT EXISTS (SELECT 1 FROM plan_same_as t WHERE (t.guild_id, t.campaign_id,"
            " t.entity_id, t.document_id, t.status) = (n.guild_id, n.campaign_id, %s,"
            " n.document_id, 'same'))"
            " ORDER BY n.name_key, n.id",
            (*scope.ids, kind, entity_id),
        )
        rows = await cur.fetchall()
    words = set(name_key(entity["name"]).split())
    ranked = sorted(
        rows,
        key=lambda r: (
            not (words & set(name_key(r["name"]).split())),
            name_key(r["name"]),
            r["id"],
        ),
    )
    page = ranked[offset : offset + PICK_MAX]
    return [Option(r["id"], r["name"], r["kind"], r["chapter"] or "") for r in page], len(ranked)
