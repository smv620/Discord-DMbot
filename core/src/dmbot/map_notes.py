"""Map descriptions kept for MapBot (#2450, part 3; docs/PLAN.md, "Images posted there").

When someone who agreed posts a map in the table chat, the AI reads it again for its places and
where each one is from the others (`dmbot.images`), written so the layout could be drawn again from
the words alone. Those words are kept here, per campaign, for MapBot, a later feature that draws
simple maps from asserted facts. **Nothing is built on them now.** Only words are kept, never the
picture.

Per campaign and isolated (CLAUDE.md: campaign isolation); deleted with the campaign; in its backup
(`MapNotesSection`); and deleted with the poster's lines when they delete their own words
(`TranscriptStore.delete_person`), because they are that person's picture put into words. A
campaign keeps at most `KEEP` notes: the oldest goes when another is saved. Stopping being
recorded does not delete them, as it does not delete transcript lines; a backup taken earlier still
holds them, with the poster's Discord id, like the transcripts in it.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.db import Conn, Database
from dmbot.images import (
    ALT_MAX,
    LAYOUT_MAX,
    PLACE_NAME_MAX,
    PLACE_WHERE_MAX,
    PLACES_MAX,
    Place,
    Read,
)

log = logging.getLogger(__name__)

KEEP = 200  # notes a campaign keeps
INT64_MAX = 2**63 - 1
DAMAGED = (
    "This backup file is damaged or isn't a DMbot campaign backup. "
    "Pick the file you got from `/dmbot backup`."
)


@dataclass(frozen=True, slots=True)
class MapNote:
    id: int
    user_id: int  # who posted the map
    made_at: int
    alt: str
    layout: str
    places: tuple[Place, ...]


class MapNoteStore:
    """A campaign's map descriptions."""

    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def add(self, guild_id: int, campaign_id: str, user_id: int, read: Read) -> bool:
        """Keep a map's description. False for anything that is not a map with something in it.
        The oldest note goes when the campaign is over `KEEP`. One transaction."""
        if read.kind != "map" or not (read.places or read.layout):
            return False
        # The same rules a backup is checked by, so what is kept can always be restored.
        alt = read.alt.strip() or "A map."
        places = [
            {"name": p.name.strip()[:PLACE_NAME_MAX], "where": p.where[:PLACE_WHERE_MAX]}
            for p in read.places[:PLACES_MAX]
            if p.name.strip()
        ]
        if not places and not read.layout.strip():
            return False
        async with self._db.guild(guild_id) as conn:
            # One add at a time per campaign, so the cap below is exact.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"map_notes:{guild_id}:{campaign_id}",),
            )
            await conn.execute(
                "INSERT INTO map_notes (guild_id, campaign_id, user_id, made_at, alt, layout,"
                " places) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
                (
                    guild_id,
                    campaign_id,
                    user_id,
                    int(self._clock()),
                    alt[:ALT_MAX],
                    read.layout[:LAYOUT_MAX],
                    json.dumps(places),
                ),
            )
            await conn.execute(
                "DELETE FROM map_notes WHERE guild_id = %s AND campaign_id = %s AND id NOT IN"
                " (SELECT id FROM map_notes WHERE guild_id = %s AND campaign_id = %s"
                "  ORDER BY id DESC LIMIT %s)",
                (guild_id, campaign_id, guild_id, campaign_id, KEEP),
            )
        return True

    async def notes(self, guild_id: int, campaign_id: str) -> list[MapNote]:
        """This campaign's notes, newest first."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, user_id, made_at, alt, layout, places FROM map_notes"
                " WHERE guild_id = %s AND campaign_id = %s ORDER BY id DESC",
                (guild_id, campaign_id),
            )
            rows = await cur.fetchall()
        return [
            MapNote(
                int(r["id"]),
                int(r["user_id"]),
                int(r["made_at"]),
                str(r["alt"]),
                str(r["layout"]),
                tuple(Place(str(p["name"]), str(p["where"])) for p in r["places"]),
            )
            for r in rows
        ]


@dataclass(frozen=True, slots=True)
class _Checked:
    user_id: int
    made_at: int
    alt: str
    layout: str
    places: str  # already JSON


def _text(value: object, limit: int, *, empty_ok: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not value.strip() and not empty_ok):
        raise CampaignError(DAMAGED)
    return value


class MapNotesSection:
    """A campaign's map descriptions in its backups (an `ExportSection`)."""

    name = "map_notes"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT user_id, made_at, alt, layout, places FROM map_notes"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY id",
            (guild_id, campaign_id),
        )
        return [
            {
                "by": str(r["user_id"]),
                "at": int(r["made_at"]),
                "alt": r["alt"],
                "layout": r["layout"],
                "places": r["places"],
            }
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[_Checked]:
        if not isinstance(rows, list) or len(rows) > KEEP:
            raise CampaignError(DAMAGED)
        out: list[_Checked] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"by", "at", "alt", "layout", "places"}:
                raise CampaignError(DAMAGED)
            by, at, places = row["by"], row["at"], row["places"]
            if (
                not (isinstance(by, str) and by.isascii() and by.isdigit())
                or not 0 < int(by) <= INT64_MAX
                or isinstance(at, bool)
                or not isinstance(at, int)
                or not 0 <= at <= INT64_MAX
                or not isinstance(places, list)
                or len(places) > PLACES_MAX
            ):
                raise CampaignError(DAMAGED)
            kept = []
            for p in places:
                if not isinstance(p, dict) or set(p) != {"name", "where"}:
                    raise CampaignError(DAMAGED)
                kept.append(
                    {
                        "name": _text(p["name"], PLACE_NAME_MAX),
                        "where": _text(p["where"], PLACE_WHERE_MAX, empty_ok=True),
                    }
                )
            layout = _text(row["layout"], LAYOUT_MAX, empty_ok=True)
            if not kept and not layout.strip():
                raise CampaignError(DAMAGED)
            out.append(
                _Checked(
                    int(by),
                    at,
                    _text(row["alt"], ALT_MAX),
                    layout,
                    json.dumps(kept),
                )
            )
        return out

    async def load(self, conn: Conn, guild_id: int, campaign_id: str, rows: Any) -> None:
        # Either the rows `check` already returned (the store checks in a worker thread before
        # the transaction opens), or a raw list, checked here.
        checked = (
            rows
            if isinstance(rows, list) and rows and all(isinstance(r, _Checked) for r in rows)
            else self.check(rows)
        )
        for r in checked:
            await conn.execute(
                "INSERT INTO map_notes (guild_id, campaign_id, user_id, made_at, alt, layout,"
                " places) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
                (guild_id, campaign_id, r.user_id, r.made_at, r.alt, r.layout, r.places),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM map_notes WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
