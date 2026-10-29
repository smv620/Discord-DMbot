"""How often each name was said, in campaign backups (an `ExportSection`; #1747).

Without it a restored campaign loses what the counts feed: quiet story threads, the pre-session
note's quiet-threads line, name hints' "said recently" order, Guide me's chapter guess and the
10-session fade of deeds. A row is a count, never a word or a line: entry, session start, speaker
and times. Rows from the file are untrusted: the section is refused whole if any is damaged, and
two things are dropped (not refused) when the backup is loaded, because a backup outlives both:
- a speaker who is not recorded now in the restoring server (consent carries per server, and a
  count exists only because someone agreed; it must not outlive "Stop recording me");
- an entry that is not in the restored campaign (the memory section loads first).
A speaker who deleted their lines (Retention) is gone from the table already, so the export has
no counts of theirs. An older backup has no such section and restores with no counts, as
before. Sections load in the file's order and the memory section is written first; a hand-reordered
file loses its counts (never a leak).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn
from dmbot.memory.models import is_id

DAMAGED = "This backup file is damaged (bad heard counts)."
# Names x sessions x speakers stays far below this; at ~130 bytes a row it also keeps the section
# well inside the backup size limit (200,000 rows alone would be ~26 MB, over it).
HEARD_ROWS_MAX = 100_000
INT64_MAX = 2**63 - 1
INT32_MAX = 2**31 - 1
_KEYS = {"entity_id", "session_started_at", "speaker_id", "times"}

Row = tuple[str, int, int, int]  # entity id, session start, speaker, times


def _int(value: object, low: int, high: int) -> bool:
    # bool is an int in Python; a JSON true must not pass as a count.
    return type(value) is int and low <= value <= high


def _checked(rows: object) -> list[Row]:
    """The rows as tuples, or CampaignError(DAMAGED) on anything out of shape. Pure."""
    if not isinstance(rows, list) or len(rows) > HEARD_ROWS_MAX:
        raise CampaignError(DAMAGED)
    out: list[Row] = []
    seen: set[tuple[str, int, int]] = set()
    for raw in rows:
        if not (
            isinstance(raw, dict)
            and set(raw) == _KEYS
            and is_id(raw["entity_id"])
            and _int(raw["session_started_at"], 1, INT64_MAX)
            and _int(raw["speaker_id"], 1, INT64_MAX)
            and _int(raw["times"], 1, INT32_MAX)
        ):
            raise CampaignError(DAMAGED)
        key = (raw["entity_id"], raw["session_started_at"], raw["speaker_id"])
        if key in seen:
            raise CampaignError(DAMAGED)
        seen.add(key)
        out.append((*key, raw["times"]))
    return out


def _is_checked(rows: object) -> bool:
    """True for what `check` returned: a list holding only tuples (the whole list, not a guess
    from its first row, so a mixed list is validated)."""
    return isinstance(rows, list) and all(isinstance(r, tuple) for r in rows)


class HeardSection:
    name = "memory_heard"

    def __init__(self, consenting: Callable[[int], Awaitable[frozenset[int]]]) -> None:
        # The people recorded in a server now, as the rest of DMbot decides it (current
        # wording, the outside engine, a stop not yet saved): never a second reading of the table.
        self._consenting = consenting

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT entity_id, session_started_at, speaker_id, times FROM memory_heard"
            " WHERE guild_id = %s AND campaign_id = %s"
            " ORDER BY session_started_at DESC, entity_id, speaker_id LIMIT %s",
            (guild_id, campaign_id, HEARD_ROWS_MAX),
        )
        # Past the cap the oldest sessions are left out (they have faded from every use), so a
        # file this writes is one `check` accepts. Oldest first in the file, as it reads best.
        return [
            {
                "entity_id": r["entity_id"],
                "session_started_at": int(r["session_started_at"]),
                "speaker_id": int(r["speaker_id"]),
                "times": int(r["times"]),
            }
            for r in reversed(await cur.fetchall())
        ]

    def check(self, rows: list[Any]) -> list[Row]:
        """Pure; the store runs it in a worker thread before its transaction."""
        return _checked(rows)

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        checked = list(rows) if _is_checked(rows) else _checked(rows)
        if not checked:
            return
        # Consent is read now, at restore, for this server. Entries are those the memory
        # section just loaded into this campaign.
        speakers = sorted(await self._consenting(guild_id))
        if not speakers:
            return
        await conn.execute(
            "INSERT INTO memory_heard"
            " (guild_id, campaign_id, entity_id, session_started_at, speaker_id, times)"
            " SELECT %s, %s, h.entity_id, h.session_started_at, h.speaker_id, h.times"
            " FROM unnest(%s::text[], %s::bigint[], %s::bigint[], %s::int[])"
            " AS h(entity_id, session_started_at, speaker_id, times)"
            " JOIN memory_entities e ON e.guild_id = %s AND e.campaign_id = %s"
            " AND e.id = h.entity_id WHERE h.speaker_id = ANY(%s)"
            " ON CONFLICT DO NOTHING",
            (
                guild_id,
                campaign_id,
                [r[0] for r in checked],
                [r[1] for r in checked],
                [r[2] for r in checked],
                [r[3] for r in checked],
                guild_id,
                campaign_id,
                speakers,
            ),
        )
        # Consent is re-checked after the async insert (CLAUDE.md): a "Stop recording me" that
        # landed meanwhile drops its holder from the cache at once, so their rows go again.
        still = await self._consenting(guild_id)
        gone = sorted(set(speakers) - still)
        if gone:
            await conn.execute(
                "DELETE FROM memory_heard WHERE guild_id = %s AND campaign_id = %s"
                " AND speaker_id = ANY(%s)",
                (guild_id, campaign_id, gone),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        # The memory section's clear deletes entries, and the counts go with them (foreign
        # key); this makes it explicit for a replace that keeps the entries.
        await conn.execute(
            "DELETE FROM memory_heard WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )


__all__ = ["HEARD_ROWS_MAX", "HeardSection"]
