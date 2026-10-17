"""Guide me, the first version, with no AI (docs/STORY_MEMORY.md, section 6, "5d slice 6"; #1731).

The DM presses **🧭 What's next?** and sees, privately, where the table seems to be in their own
adventure and what the book lists next. Everything shown is a name or summary the reader stored in
`plan_nodes`; nothing is written or invented, and DMbot never decides: the DM can say they are in
another chapter, and that beats the guess until they clear it.

This module is the rule (pure, no database) and a few reads and writes. The chapter rule is the
one `book_links` uses for its "(Chapter …)" text: the nearest chapter at or before an entry in the
same file. The table's chapter is the one with the most table entries linked to the book
(`plan_same_as`, status `same`) among the entries heard in the last two sessions; a tie goes to the
later chapter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from dmbot.memory._changes import PLAN_POSITION
from dmbot.memory.book_links import chapter_id_of
from dmbot.memory.models import DM, MemoryRuleError, new_id
from dmbot.memory.store import MemoryStore

EVIDENCE = 3  # table names shown as "from: …"
AHEAD = 6  # book entries listed as still ahead in the chapter
SUMMARY_SHOWN = 150  # characters of the next chapter's summary
SESSIONS = 2  # the last sessions whose names count as "where the table is"
GONE = "That chapter isn't in your adventure any more."

NODES = """
    SELECT n.id, n.kind, n.name, n.summary, n.position, n.created_at,
           {chapter_id} AS chapter_id,
           (SELECT p.chapter_id FROM plan_position p WHERE p.guild_id = n.guild_id
               AND p.campaign_id = n.campaign_id AND p.document_id = n.document_id) AS pick
    FROM plan_nodes n
    WHERE n.guild_id = %s AND n.campaign_id = %s AND n.document_id = %s
    ORDER BY n.position, n.created_at, n.id
"""
# The table entry the DM linked to each book entry of the file, and whether it was heard in one of
# the last sessions (`memory_heard`, where a session's names are kept: the primary key leads with
# the session, so the newest two are an index walk). Kept apart from NODES: a few rows, so the
# joins stay cheap whatever the plan.
LINKED = """
    WITH recent AS (
        SELECT DISTINCT session_started_at FROM memory_heard
        WHERE guild_id = %(g)s AND campaign_id = %(c)s
        ORDER BY session_started_at DESC LIMIT %(sessions)s
    )
    SELECT sa.plan_id, e.name,
           EXISTS (SELECT 1 FROM memory_heard m WHERE m.guild_id = e.guild_id
               AND m.campaign_id = e.campaign_id AND m.entity_id = e.id
               AND m.session_started_at IN (SELECT session_started_at FROM recent)) AS heard
    FROM plan_same_as sa
    JOIN memory_entities e ON e.guild_id = sa.guild_id AND e.campaign_id = sa.campaign_id
        AND e.id = sa.entity_id AND e.status IN ('proposed', 'confirmed')
    WHERE sa.guild_id = %(g)s AND sa.campaign_id = %(c)s AND sa.document_id = %(d)s
        AND sa.status = 'same'
"""


@dataclass(frozen=True, slots=True)
class Node:
    """A chapter, place or person the reader found, with the table entry the DM linked to it."""

    id: str
    kind: str
    name: str
    summary: str
    position: int
    created_at: int
    table_name: str | None = None  # the linked table entry, if the DM said it is the same
    heard: bool = False  # ...and it was heard in one of the last sessions
    chapter_id: str | None = None  # the chapter it is in, by the one rule (`book_links`)


@dataclass(frozen=True, slots=True)
class Guide:
    """Where the table seems to be and what is next, from the nodes of one file."""

    chapters: tuple[Node, ...]  # in book order
    chapter: Node | None  # None: no guess and no pick, the DM chooses
    picked: bool  # the DM's word rather than DMbot's guess
    evidence: tuple[str, ...] = field(default=())  # up to EVIDENCE table names
    ahead: tuple[str, ...] = field(default=())  # up to AHEAD book names not linked to the table
    ahead_more: int = 0
    following: Node | None = None  # the next chapter
    in_chapter: frozenset[str] = frozenset()  # ids of everything in the chapter, for the clashes


def _order(node: Node) -> tuple[int, int, str]:
    return (node.position, node.created_at, node.id)


def guess(nodes: Sequence[Node]) -> Node | None:
    """The chapter with the most linked entries heard lately; a tie goes to the later chapter;
    None when nothing is linked and heard."""
    chapters = sorted((n for n in nodes if n.kind == "chapter"), key=_order)
    counts: dict[str, int] = {}
    for n in nodes:
        if n.kind != "chapter" and n.heard and n.chapter_id is not None:
            counts[n.chapter_id] = counts.get(n.chapter_id, 0) + 1
    best: Node | None = None
    for chapter in chapters:  # in order, so `>=` lets a later chapter win a tie
        if counts.get(chapter.id, 0) and (
            best is None or counts[chapter.id] >= counts.get(best.id, 0)
        ):
            best = chapter
    return best


def build(nodes: Sequence[Node], pick_id: str | None) -> Guide:
    """What to show, for one file: the DM's pick if there is one, else the guess."""
    chapters = tuple(sorted((n for n in nodes if n.kind == "chapter"), key=_order))
    by_id = {c.id: c for c in chapters}
    chapter = by_id.get(pick_id) if pick_id else None
    picked = chapter is not None
    if chapter is None:
        chapter = guess(nodes)
    if chapter is None:
        return Guide(chapters, None, False)
    inside = [
        n
        for n in nodes  # (already in book order: `NODES` sorts them)
        if n.kind != "chapter" and n.chapter_id == chapter.id
    ]
    linked = [n for n in inside if n.table_name and (picked or n.heard)]
    unmet = [n.name for n in inside if not n.table_name]
    index = chapters.index(chapter)
    return Guide(
        chapters,
        chapter,
        picked,
        tuple(dict.fromkeys(n.table_name for n in linked if n.table_name))[:EVIDENCE],
        tuple(unmet[:AHEAD]),
        max(0, len(unmet) - AHEAD),
        chapters[index + 1] if index + 1 < len(chapters) else None,
        frozenset([chapter.id, *(n.id for n in inside)]),
    )


# ---- reading and writing -------------------------------------------------------------------


async def read(
    memory: MemoryStore, guild_id: int, campaign_id: str, document_id: str
) -> tuple[list[Node], str | None]:
    """The nodes of one file with their links, and the DM's pick for it: two reads, so with the
    clashes (`StoryWatch.open_for`) a press makes three."""
    async with memory._read(guild_id, campaign_id) as scope:
        cur = await scope.conn.execute(
            NODES.format(chapter_id=chapter_id_of("n")), (guild_id, campaign_id, document_id)
        )
        rows = await cur.fetchall()
        pick = str(rows[0]["pick"]) if rows and rows[0]["pick"] else None
        cur = await scope.conn.execute(
            LINKED, {"sessions": SESSIONS, "g": guild_id, "c": campaign_id, "d": document_id}
        )
        table = {r["plan_id"]: (r["name"], bool(r["heard"])) for r in await cur.fetchall()}
        nodes = [
            Node(
                r["id"],
                r["kind"],
                " ".join(str(r["name"]).split()),
                " ".join(str(r["summary"]).split()),
                int(r["position"]),
                int(r["created_at"]),
                *table.get(r["id"], (None, False)),
                r["chapter_id"],
            )
            for r in rows
        ]
    return nodes, pick


async def _lock(w: Any, campaign_id: str, document_id: str) -> None:
    """One pick or clear at a time for a file, so two never interleave."""
    await w.conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"dmbot.plan_position:{campaign_id}:{document_id}",),
    )


async def pick(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    document_id: str,
    chapter_id: str,
    user_id: int,
) -> int | None:
    """The DM says the table is in this chapter. Returns the batch to pass to `memory.undo`."""
    async with memory._write(guild_id, campaign_id, DM) as w:
        # Two picks at once for one file: the second waits and then updates the first's row.
        await _lock(w, campaign_id, document_id)
        cur = await w.conn.execute(
            "SELECT 1 FROM plan_nodes WHERE guild_id = %s AND campaign_id = %s AND id = %s"
            " AND document_id = %s AND kind = 'chapter'",
            (guild_id, campaign_id, chapter_id, document_id),
        )
        if await cur.fetchone() is None:
            raise MemoryRuleError(GONE)
        held = await w.select(PLAN_POSITION, " AND document_id = %s", [document_id])
        if held:
            await w.update(
                PLAN_POSITION, held[0]["id"],
                {"chapter_id": chapter_id, "set_by": user_id, "created_at": w.now},
            )  # fmt: skip
        else:
            await w.insert(
                PLAN_POSITION,
                {
                    "id": new_id(),
                    "document_id": document_id,
                    "chapter_id": chapter_id,
                    "set_by": user_id,
                    "created_at": w.now,
                },
            )
        return w.batch


async def clear(
    memory: MemoryStore, guild_id: int, campaign_id: str, document_id: str
) -> int | None:
    """Back to DMbot's guess. Returns the batch to undo (None when there was no pick)."""
    async with memory._write(guild_id, campaign_id, DM) as w:
        await _lock(w, campaign_id, document_id)  # the same lock as `pick`
        for row in await w.select(PLAN_POSITION, " AND document_id = %s", [document_id]):
            await w.delete(PLAN_POSITION, row["id"])
        return w.batch


def summary_of(node: Node) -> str:
    text = node.summary.strip()
    return text if len(text) <= SUMMARY_SHOWN else text[: SUMMARY_SHOWN - 1] + "…"


__all__ = [
    "Guide", "Node", "build", "clear", "guess", "pick", "read", "summary_of",
]  # fmt: skip
