"""The numbers meaning search compares (#2664; docs/STORY_MEMORY.md 9a, "Embeddings"): one row for
each piece of a campaign's own text that Voyage AI turned into a list of numbers.

**Comparing is done here, in Python, inside ONE campaign.** A read names the campaign and loads only
that campaign's rows for the current model, so nothing is ever compared across campaigns or
servers; the table's row-level security and composite keys back that up. A campaign holds thousands
of notes at most and the best few are re-scored by the reranker anyway, so no database extension is
needed. A read over `SIZE_WARNING` rows logs a counts-only warning, so the day it is slow is seen
before it is felt.

**A person's words leave no copy.** A transcript line's row is tied to the line by a foreign key
that deletes it with the line, so every way a line goes (Stop recording me, Delete my transcripts,
retention, a deleted session or campaign) takes its numbers in the same statement. Saving a line's
numbers is one statement that selects the line, so a line deleted while its text was away at
Voyage AI is not saved at all. Never in a backup: they are made again. Every row records the model
that made it and a hash of the text, so a changed text or model is made again, never mixed.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from dmbot.db import Database
from dmbot.memory.store import MemoryStore
from dmbot.memory.voyage import cosine

log = logging.getLogger(__name__)

# What is embedded. Campaign text (the first four) stays with the campaign like the rest of its
# generated content; a line is a person's words, kept only while the line exists.
ASSERTION, EVENT_NOTE, DM_NOTE, LORE, LINE = "assertion", "event_note", "dm_note", "lore", "line"
KINDS = (ASSERTION, EVENT_NOTE, DM_NOTE, LORE, LINE)
SIZE_WARNING = 20_000  # rows in one campaign for one read: the day to plan something better
MAX_DIMS = 4096  # the table's CHECK


def text_hash(text: str) -> str:
    """A fingerprint of the text a vector was made from; the text itself is never stored here."""
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Item:
    kind: str
    owner_id: str  # a line's id as text for `LINE`
    hash: str
    vector: Sequence[float]


@dataclass(frozen=True, slots=True)
class Match:
    kind: str
    owner_id: str  # as stored: a long text's parts are `id#n` (`embedding.owner_of` strips it)
    score: float  # cosine: 1.0 the same direction


class VectorStore:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    @classmethod
    def of(cls, memory: MemoryStore) -> VectorStore:
        """On the memory store's own database (the bot has the one store)."""
        return cls(memory._db)

    async def save(self, guild_id: int, campaign_id: str, model: str, items: Sequence[Item]) -> int:
        """Keep these vectors for the campaign's own text (every kind but a line). One for a kind
        and owner already kept replaces it. How many were kept."""
        kept = 0
        async with self._db.guild(guild_id) as conn:
            for item in items:
                if item.kind == LINE or item.kind not in KINDS or not _fits(item.vector):
                    continue
                cur = await conn.execute(
                    "INSERT INTO memory_vectors (guild_id, campaign_id, owner_kind, owner_id,"
                    " model, text_hash, vector, made_at)"
                    " SELECT %s, %s, %s, %s, %s, %s, %s::real[], %s"
                    " WHERE EXISTS (SELECT 1 FROM campaigns WHERE guild_id = %s AND id = %s)"
                    " ON CONFLICT (guild_id, campaign_id, owner_kind, owner_id) DO UPDATE SET"
                    " model = EXCLUDED.model, text_hash = EXCLUDED.text_hash,"
                    " vector = EXCLUDED.vector, made_at = EXCLUDED.made_at",
                    (
                        guild_id, campaign_id, item.kind, item.owner_id, model, item.hash,
                        [float(x) for x in item.vector], int(self._now()), guild_id, campaign_id,
                    ),
                )  # fmt: skip
                kept += cur.rowcount
        return kept

    async def save_lines(
        self, guild_id: int, campaign_id: str, model: str, items: Sequence[Item]
    ) -> int:
        """Keep these vectors of transcript lines. A line that is gone, or is not a line of one of
        this campaign's sessions, is skipped: the statement selects the line, so a line deleted
        while its text was being embedded leaves nothing behind. How many were kept."""
        kept = 0
        async with self._db.guild(guild_id) as conn:
            for item in items:
                line_id = item.owner_id.split("#", 1)[0]  # "12" or "12#2": a long line in parts
                if item.kind != LINE or not line_id.isdecimal() or not _fits(item.vector):
                    continue
                cur = await conn.execute(
                    "INSERT INTO memory_vectors (guild_id, campaign_id, owner_kind, owner_id,"
                    " line_id, model, text_hash, vector, made_at)"
                    " SELECT l.guild_id, s.campaign_id, 'line', %s, l.id, %s, %s,"
                    " %s::real[], %s"
                    " FROM transcript_lines l JOIN transcript_sessions s"
                    "  ON s.id = l.session_id AND s.guild_id = l.guild_id"
                    " WHERE l.guild_id = %s AND s.campaign_id = %s AND l.id = %s"
                    # waits for a delete that is running and then skips the gone line, instead of
                    # failing the foreign key and losing the whole batch
                    " FOR KEY SHARE OF l"
                    " ON CONFLICT (guild_id, campaign_id, owner_kind, owner_id) DO UPDATE SET"
                    " model = EXCLUDED.model, text_hash = EXCLUDED.text_hash,"
                    " vector = EXCLUDED.vector, made_at = EXCLUDED.made_at",
                    (
                        item.owner_id, model, item.hash, [float(x) for x in item.vector],
                        int(self._now()), guild_id, campaign_id, int(line_id),
                    ),
                )  # fmt: skip
                kept += cur.rowcount
        return kept

    async def has_any(self, guild_id: int, campaign_id: str, model: str, kind: str) -> bool:
        """Whether this campaign has any numbers of this kind for this model: the cheap check
        before a search sends a question anywhere with nothing to compare it to."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM memory_vectors WHERE guild_id = %s AND campaign_id = %s"
                " AND model = %s AND owner_kind = %s LIMIT 1",
                (guild_id, campaign_id, model, kind),
            )
            return await cur.fetchone() is not None

    async def kept(
        self, guild_id: int, campaign_id: str, model: str, *, kind: str | None = None
    ) -> dict[tuple[str, str], str]:
        """What is kept for this model: (kind, owner id) -> the text's hash. What is not here, or
        has another hash, needs making (or making again)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT owner_kind, owner_id, text_hash FROM memory_vectors"
                " WHERE guild_id = %s AND campaign_id = %s AND model = %s"
                " AND (%s::text IS NULL OR owner_kind = %s)",
                (guild_id, campaign_id, model, kind, kind),
            )
            return {
                (str(r["owner_kind"]), str(r["owner_id"])): str(r["text_hash"])
                for r in await cur.fetchall()
            }

    async def nearest(
        self,
        guild_id: int,
        campaign_id: str,
        model: str,
        query: Sequence[float],
        *,
        kinds: Collection[str] | None = None,
        limit: int = 20,
    ) -> list[Match]:
        """The kept vectors closest in meaning to `query`, best first. One campaign, one model, the
        same number of numbers: nothing else is read, let alone compared."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT owner_kind, owner_id, vector FROM memory_vectors"
                " WHERE guild_id = %s AND campaign_id = %s AND model = %s"
                " AND cardinality(vector) = %s AND (%s::text[] IS NULL OR owner_kind = ANY(%s))",
                (
                    guild_id, campaign_id, model, len(query),
                    None if kinds is None else list(kinds), None if kinds is None else list(kinds),
                ),
            )  # fmt: skip
            rows = await cur.fetchall()
        if len(rows) > SIZE_WARNING:
            log.warning("Meaning search: %d vectors read for one campaign (over %d)", len(rows),
                        SIZE_WARNING)  # fmt: skip
        scored = [
            Match(str(r["owner_kind"]), str(r["owner_id"]), cosine(query, r["vector"]))
            for r in rows
        ]
        scored.sort(key=lambda m: (-m.score, m.kind, m.owner_id))
        return scored[: max(limit, 0)]

    async def drop(
        self, guild_id: int, campaign_id: str, kind: str, owner_ids: Collection[str]
    ) -> int:
        """The vectors of text that is gone ."""
        if not owner_ids:
            return 0
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM memory_vectors WHERE guild_id = %s AND campaign_id = %s"
                " AND owner_kind = %s AND owner_id = ANY(%s)",
                (guild_id, campaign_id, kind, list(owner_ids)),
            )
            return cur.rowcount

    async def drop_lines(self, guild_id: int, campaign_id: str, owner_ids: Collection[str]) -> int:
        """The numbers of lines that still exist but must not be searched any more: their speaker
        no longer agrees to be recorded, or the line is off topic. (A line that is deleted loses
        its numbers by itself.)"""
        if not owner_ids:
            return 0
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM memory_vectors WHERE guild_id = %s AND campaign_id = %s"
                " AND owner_kind = 'line' AND owner_id = ANY(%s)",
                (guild_id, campaign_id, list(owner_ids)),
            )
            return cur.rowcount

    async def drop_other_models(self, guild_id: int, campaign_id: str, model: str) -> int:
        """A change of model: every vector made by another one goes, so all of it is made again
        and two models' numbers are never compared."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM memory_vectors WHERE guild_id = %s AND campaign_id = %s"
                " AND model <> %s",
                (guild_id, campaign_id, model),
            )
            return cur.rowcount

    async def count(self, guild_id: int, campaign_id: str) -> int:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_vectors WHERE guild_id = %s AND campaign_id = %s",
                (guild_id, campaign_id),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0


def _fits(vector: Sequence[float]) -> bool:
    return 1 <= len(vector) <= MAX_DIMS
