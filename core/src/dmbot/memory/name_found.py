"""What made a waiting name a name (#5133 slice 3b; docs/design/session_names.md, section 6).

For each name the after-session step suggests, how it was found (introduced by name, typed,
addressed, a place, spoken of, or the scan alone) and a pointer to the one line that made it a
name, so the "📝 Check new names" card can quote that line and put the surest names first. Pointers
only, never words: the card reads the line when it draws, with who agrees to be recorded asked then
(0608).

A row goes when its name is answered (a trigger on the entry's status), when its entry goes (a
cascade), and when its line goes (`transcript.store._forget_mentions`, beside the mentions; and by
speaker in a person's forget). An Undo that sets an answered name waiting again does not bring its
row back: the card then simply has no quote. Never logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from dataclasses import dataclass

from dmbot.db import Database

HOWS = ("introduced", "typed", "addressed", "place", "spoken_of", "scan", "singled_out")  # (0614)
SURE = frozenset({"introduced", "typed"})  # offered first (section 6)


@dataclass(frozen=True, slots=True)
class Found:
    how: str
    line_ref: str | None  # `transcript.models.line_ref`, or None when no line is kept
    typed: int = 0  # how many of its hearings were typed in chat (0611)
    times: int = 0  # how many times its group was heard, 0 when not counted (0611)


class NameFound:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._now = clock

    async def keep(
        self,
        guild_id: int,
        campaign_id: str,
        entity_id: str,
        how: str,
        line_ref: str | None,
        *,
        times: int = 0,
        typed: int = 0,
    ) -> None:
        """What made this waiting entry a name; a later suggestion of it replaces the row (a name
        suggested again after another session keeps that session's line). Written only while the
        entry still waits: one answered in the moment since it was made (the website lists it at
        once) gets no row, since its trigger has already run."""
        if how not in HOWS:
            raise ValueError(f"how is one of {', '.join(HOWS)}")
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "INSERT INTO name_found (guild_id, campaign_id, entity_id, how, line_ref,"
                " found_at, times, typed) SELECT %s, %s, %s, %s, %s, %s, %s, %s"
                " WHERE EXISTS (SELECT 1 FROM memory_entities"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s"
                " AND status = 'proposed')"
                " ON CONFLICT (guild_id, campaign_id, entity_id) DO UPDATE"
                " SET how = EXCLUDED.how, line_ref = EXCLUDED.line_ref,"
                " found_at = EXCLUDED.found_at, times = EXCLUDED.times, typed = EXCLUDED.typed",
                (
                    guild_id,
                    campaign_id,
                    entity_id,
                    how,
                    line_ref,
                    int(self._now()),
                    max(0, times),
                    min(max(0, typed), max(0, times)),  # (never more typed than heard)
                    guild_id,
                    campaign_id,
                    entity_id,
                ),
            )

    async def of(
        self, guild_id: int, campaign_id: str, entity_ids: Collection[str]
    ) -> dict[str, Found]:
        """What made each of these entries a name, for those that have a row."""
        if not entity_ids:
            return {}
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT entity_id, how, line_ref, typed, times FROM name_found"
                " WHERE guild_id = %s AND campaign_id = %s AND entity_id = ANY(%s)",
                (guild_id, campaign_id, list(entity_ids)),
            )
            rows = await cur.fetchall()
        return {
            str(r["entity_id"]): Found(
                str(r["how"]), r["line_ref"], int(r["typed"]), int(r["times"])
            )
            for r in rows
        }
