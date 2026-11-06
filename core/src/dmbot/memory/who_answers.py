"""The stored answers to "Who is this?" (#3602, slice 2b): keep, find, sweep and wipe. The AI's
words are read by `who_reading`; this is where its ranked answers live until the name is answered.

- **Ids and words DMbot chose, never a transcript line.** An answer is an entry's id or "new" with
  a kind, and a confidence word. Counts only leave this module.
- **Kept until answered.** `sweep` deletes the answers of an entry that is no longer an open
  question (named, joined, removed, rejected, merged), and any answer that names an entry that has
  gone (removed, rejected or merged away). There is no foreign key to either entry.
- **Consent.** The answers carry the people whose lines the question came from. `purge_stopped`
  deletes them when any of them is not recorded now, before the DM is shown any; `forget` (Stop
  recording me) deletes theirs in every campaign of the server.
- **DM-only.** Only the bot and the DM-only door (slice 3) read the table.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dmbot.db import Conn

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

ENTITY, NEW = "entity", "new"
MAX_RANK = 3
CONFIDENCE = ("sure", "likely", "maybe")  # the table's own check; `who_reading.CONFIDENCE`
KINDS = ("character", "place", "faction", "item", "event")  # the same as `who_reading.NEW_KINDS`
_ID = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class StoredAnswer:
    """One ranked answer: `pick` is `ENTITY` (with `target_id`) or `NEW` (with `new_kind`)."""

    pick: str
    confidence: str
    target_id: str | None = None
    new_kind: str | None = None


class WhoAnswers:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def keep(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        answers: Sequence[StoredAnswer],
        *,
        speaker_ids: Collection[int],
        opted_in: Collection[int],
        session_started_at: int | None,
        prompt_version: str,
        model: str,
    ) -> int:
        """Keep these ranked answers for one waiting entry, replacing the earlier ones (an empty
        list clears them). Nothing is kept for a question made from someone who is not recorded
        now. At most `MAX_RANK`, in the order given; a malformed one is left out. Returns how many
        were kept."""
        if not set(speaker_ids) <= set(opted_in):
            return 0
        good = [a for a in answers if _well_formed(a, entity_id)][:MAX_RANK]
        version = prompt_version.strip()[:40]
        if not _ID.fullmatch(entity_id) or not version:
            return 0
        async with self._store._read(guild_id, campaign_id) as scope:
            # one transaction; two steps for the same entry at once end as the last one's answers
            # (an upsert by rank, then the ranks past the new count go), never a key clash
            for rank, a in enumerate(good, 1):
                await scope.conn.execute(
                    "INSERT INTO memory_who_answers (guild_id, campaign_id, entity_id, rank, pick,"
                    " target_id, new_kind, confidence, speaker_ids, session_started_at,"
                    " prompt_version, model, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (guild_id, campaign_id, entity_id, rank) DO UPDATE SET"
                    " pick = EXCLUDED.pick, target_id = EXCLUDED.target_id,"
                    " new_kind = EXCLUDED.new_kind, confidence = EXCLUDED.confidence,"
                    " speaker_ids = EXCLUDED.speaker_ids,"
                    " session_started_at = EXCLUDED.session_started_at,"
                    " prompt_version = EXCLUDED.prompt_version, model = EXCLUDED.model,"
                    " created_at = EXCLUDED.created_at",
                    (
                        *scope.ids, entity_id, rank, a.pick, a.target_id, a.new_kind, a.confidence,
                        sorted(set(speaker_ids)), session_started_at, version,
                        model[:80], max(1, int(self._store._clock())),
                    ),
                )  # fmt: skip
            await scope.conn.execute(
                "DELETE FROM memory_who_answers WHERE guild_id = %s AND campaign_id = %s"
                " AND entity_id = %s AND rank > %s",
                (*scope.ids, entity_id, len(good)),
            )
        return len(good)

    async def sweep(self, guild_id: int, campaign_id: str) -> int:
        """Delete the answers whose question is answered, or that name an entry that is gone. An
        entry's question is open while it is a suggestion (`proposed`) or a person the story never
        named (confirmed and `unnamed`) and has not been merged away. Returns how many went."""
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "DELETE FROM memory_who_answers a WHERE a.guild_id = %s AND a.campaign_id = %s"
                " AND (NOT EXISTS (SELECT 1 FROM memory_entities e"
                "   WHERE e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id"
                "   AND e.id = a.entity_id AND e.merged_into IS NULL"
                "   AND (e.status = 'proposed' OR (e.status = 'confirmed' AND e.unnamed)))"
                " OR (a.pick = 'entity' AND NOT EXISTS (SELECT 1 FROM memory_entities t"
                "   WHERE t.guild_id = a.guild_id AND t.campaign_id = a.campaign_id"
                "   AND t.id = a.target_id AND t.merged_into IS NULL"
                "   AND t.status IN ('proposed', 'confirmed'))))",
                scope.ids,
            )
            return int(cur.rowcount)

    async def purge_stopped(
        self, guild_id: int, campaign_id: str, opted_in: Collection[int]
    ) -> int:
        """Delete the answers with a speaker who is not recorded in this server now: the consent
        re-check on the read path, as for scenes, claims and deeds."""
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "DELETE FROM memory_who_answers WHERE guild_id = %s AND campaign_id = %s"
                " AND NOT (speaker_ids <@ %s::bigint[])",
                (*scope.ids, sorted(set(opted_in))),
            )
            return int(cur.rowcount)

    async def of(
        self,
        guild_id: int,
        campaign_id: str,
        entity_ids: Sequence[str],
        *,
        opted_in: Collection[int],
    ) -> Mapping[str, list[StoredAnswer]]:
        """The ranked answers for these waiting entries, best first, after the answered ones and
        the ones with a stopped speaker are gone. DM-only: the caller is a DM's card or door."""
        if not entity_ids:
            return {}
        await self.sweep(guild_id, campaign_id)
        await self.purge_stopped(guild_id, campaign_id, opted_in)
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT entity_id, pick, target_id, new_kind, confidence FROM memory_who_answers"
                " WHERE guild_id = %s AND campaign_id = %s AND entity_id = ANY(%s)"
                " ORDER BY entity_id, rank",
                (*scope.ids, list(entity_ids)),
            )
            out: dict[str, list[StoredAnswer]] = {}
            for r in await cur.fetchall():
                out.setdefault(str(r["entity_id"]), []).append(
                    StoredAnswer(
                        str(r["pick"]),
                        str(r["confidence"]),
                        str(r["target_id"]) if r["target_id"] is not None else None,
                        str(r["new_kind"]) if r["new_kind"] is not None else None,
                    )
                )
            return out

    async def missing(
        self, guild_id: int, campaign_id: str, entity_ids: Sequence[str]
    ) -> list[str]:
        """Of these waiting entries, the ones with no answers yet (what the next step works out).
        Call it after `of` (or `sweep` and `purge_stopped`): it does not clear stale answers."""
        if not entity_ids:
            return []
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT DISTINCT entity_id FROM memory_who_answers WHERE guild_id = %s"
                " AND campaign_id = %s AND entity_id = ANY(%s)",
                (*scope.ids, list(entity_ids)),
            )
            have = {str(r["entity_id"]) for r in await cur.fetchall()}
        return [i for i in entity_ids if i not in have]


def _well_formed(a: StoredAnswer, entity_id: str) -> bool:
    """The shape the table's checks allow, so one bad answer never fails the whole keep. An entry
    is never its own answer."""
    if a.confidence not in CONFIDENCE:
        return False
    if a.pick == ENTITY:
        target = a.target_id
        return (
            target is not None and _ID.fullmatch(target) is not None
            and target != entity_id and a.new_kind is None
        )  # fmt: skip
    return a.pick == NEW and a.target_id is None and a.new_kind in KINDS


async def forget(conn: Conn, guild_id: int, user_id: int) -> int:
    """Stop recording me: delete, in every campaign of the server, the answers worked out from this
    person's lines. Returns how many went."""
    cur = await conn.execute(
        "DELETE FROM memory_who_answers WHERE guild_id = %s AND %s = ANY(speaker_ids)",
        (guild_id, user_id),
    )
    return int(cur.rowcount)
