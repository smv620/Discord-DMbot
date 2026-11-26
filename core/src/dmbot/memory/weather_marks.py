"""Which weather DMbot has already noted for a place (#4748; Supervisor's ruling on #4747): the
after-session weather check notes a place's snow, frost or heat once, and again only when the kept
climate it was judged against changes. Ids, a fixed word and a time only (0542).

"Changed" means the climate's value is another, or the row the note was judged against is no longer
one of the kept rows (turned down, deleted, or replaced). A second kept row with the same value
beside it is no change, so which row is chosen never decides it.

The mark is written before the note is sent, so two reads finishing together send it once; a
message that then fails to arrive costs that note, never a repeat. Marks are taken in a fixed order
(place, kind), so two such reads never wait on each other in a circle.

What doesn't carry a mark, so the note may come once more (never a leak, only a repeat):
- a place merged into another: the marks stay on the merged-away entry;
- an Undo that removes the climate row and puts it back: the mark went with the row.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import NamedTuple

from dmbot.db import Database
from dmbot.memory.models import new_id


class Mark(NamedTuple):
    place_id: str
    kind: str  # weather_note.SNOW, FROST or HEAT
    climate_id: str  # the kept climate row the note was judged against
    climate: str  # its value then ("arctic" … "hot")
    kept_ids: tuple[str, ...] = ()  # every kept climate row it was judged among


async def first_time(
    db: Database, guild_id: int, campaign_id: str, marks: Sequence[Mark], now: int | None = None
) -> list[bool]:
    """For each mark, in the order given, whether its note is new: no note yet for that place and
    kind, or one judged against another value, or against a row that is no longer among the kept
    ones. Records each one that is. A place or climate row gone meanwhile is not new (the note
    would name nothing that is kept)."""
    at = int(time.time()) if now is None else now
    order = sorted(range(len(marks)), key=lambda i: (marks[i].place_id, marks[i].kind))
    new = [False] * len(marks)
    # (DMbot's own write: a re-mark carries this build's stamp, `lineage`)
    async with db.guild(guild_id, by_dmbot=True) as conn:
        for i in order:
            m = marks[i]
            kept = sorted({m.climate_id, *m.kept_ids})
            cur = await conn.execute(
                "INSERT INTO memory_weather_notes (guild_id, campaign_id, id, place_id, kind,"
                " climate_id, climate, created_at)"
                " SELECT %s, %s, %s, e.id, %s, f.id, %s, %s FROM memory_entities e"
                " JOIN memory_map_facts f ON f.guild_id = e.guild_id"
                " AND f.campaign_id = e.campaign_id AND f.id = %s"
                " WHERE e.guild_id = %s AND e.campaign_id = %s AND e.id = %s"
                " ON CONFLICT (guild_id, campaign_id, place_id, kind) DO UPDATE"
                " SET climate_id = EXCLUDED.climate_id, climate = EXCLUDED.climate,"
                " created_at = EXCLUDED.created_at"
                " WHERE NOT (memory_weather_notes.climate = EXCLUDED.climate"
                " AND memory_weather_notes.climate_id = ANY(%s::text[]))",
                (guild_id, campaign_id, new_id(), m.kind, m.climate, at, m.climate_id,
                 guild_id, campaign_id, m.place_id, kept),
            )  # fmt: skip
            new[i] = bool(cur.rowcount)
    return new


async def noted_at(
    db: Database, guild_id: int, campaign_id: str, at: int
) -> list[tuple[str, str, str]]:
    """The (place id, kind, climate) of the notes `first_time` recorded at the moment `at`, in a
    fixed order: what the worker's outbox row (#4904 3b-4b) reads back to say them, so the row
    holds a time and no word of the story."""
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT place_id, kind, climate FROM memory_weather_notes"
            " WHERE guild_id = %s AND campaign_id = %s AND created_at = %s"
            " ORDER BY place_id, kind",
            (guild_id, campaign_id, at),
        )
        return [
            (str(r["place_id"]), str(r["kind"]), str(r["climate"])) for r in await cur.fetchall()
        ]


async def unmark_at(db: Database, guild_id: int, campaign_id: str, at: int) -> int:
    """Take back the marks made at the moment `at`, so a later read may note them: what the bot
    does when a worker's weather row arrives while the campaign's table is playing (the note is
    never sent during play, and a note not sent is never marked). Returns how many went."""
    async with db.guild(guild_id, by_dmbot=True) as conn:
        cur = await conn.execute(
            "DELETE FROM memory_weather_notes"
            " WHERE guild_id = %s AND campaign_id = %s AND created_at = %s",
            (guild_id, campaign_id, at),
        )
        return int(cur.rowcount)


__all__ = ["Mark", "first_time", "noted_at", "unmark_at"]
