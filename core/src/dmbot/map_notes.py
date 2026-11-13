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
    CLOSE_FORMS,
    CLOSE_LEVELS,
    LAYOUT_MAX,
    LINK_KINDS,
    LINK_WORD_MAX,
    LINKS_MAX,
    NEXT_TO,
    PLACE_NAME_MAX,
    PLACE_WHERE_MAX,
    PLACES_MAX,
    ROUTE_TO,
    Place,
    Read,
)
from dmbot.memory import next_words, route_words

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
    close: dict[str, Any] | None = None  # the close read (#4173 slice 6b-1), or None


def close_of(read: Read) -> dict[str, Any] | None:
    """A read's close read as it is kept (`close_read`), or None when it had none: the map's
    level, each place's size and form, and its links. The same shape `MapNotesSection` checks."""
    if not (read.level or read.links or any(p.level or p.form for p in read.places)):
        return None
    return {
        "level": read.level,
        "places": [
            {"name": p.name.strip()[:PLACE_NAME_MAX], "level": p.level, "form": p.form}
            for p in read.places[:PLACES_MAX]
            if p.name.strip()
        ],
        "links": [
            {"a": k.a, "kind": k.kind, "b": k.b, "word": k.word, "sure": k.sure}
            for k in read.links[:LINKS_MAX]
        ],
    }


class MapNoteStore:
    """A campaign's map descriptions."""

    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    async def add(
        self, guild_id: int, campaign_id: str, user_id: int, read: Read, message_id: int = 0
    ) -> bool:
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
                "INSERT INTO map_notes (guild_id, campaign_id, user_id, message_id, made_at, alt,"
                " layout, places, close_read) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb,"
                " %s::jsonb)",
                (
                    guild_id,
                    campaign_id,
                    user_id,
                    message_id,
                    int(self._clock()),
                    alt[:ALT_MAX],
                    read.layout[:LAYOUT_MAX],
                    json.dumps(places),
                    None if (close := close_of(read)) is None else json.dumps(close),
                ),
            )
            await conn.execute(
                "DELETE FROM map_notes WHERE guild_id = %s AND campaign_id = %s AND id NOT IN"
                " (SELECT id FROM map_notes WHERE guild_id = %s AND campaign_id = %s"
                "  ORDER BY id DESC LIMIT %s)",
                (guild_id, campaign_id, guild_id, campaign_id, KEEP),
            )
        return True

    async def forget_message(self, guild_id: int, message_id: int) -> int:
        """The picture was deleted: so are the notes made from it. How many went."""
        if message_id <= 0:
            return 0
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "DELETE FROM map_notes WHERE guild_id = %s AND message_id = %s",
                (guild_id, message_id),
            )
            return cur.rowcount or 0

    async def notes(self, guild_id: int, campaign_id: str) -> list[MapNote]:
        """This campaign's notes, newest first."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id, user_id, made_at, alt, layout, places, close_read FROM map_notes"
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
                r["close_read"],
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
    close: str | None = None  # the close read, already JSON (#4173 slice 6b-1)


def _close(value: object) -> str:
    """A backup's close read, checked as the store writes it, as JSON (`close_of`'s shape)."""
    if not isinstance(value, dict) or set(value) != {"level", "places", "links"}:
        raise CampaignError(DAMAGED)
    level, places, links = value["level"], value["places"], value["links"]
    if level not in ("", *CLOSE_LEVELS) or not isinstance(places, list):
        raise CampaignError(DAMAGED)
    if not isinstance(links, list) or len(places) > PLACES_MAX or len(links) > LINKS_MAX:
        raise CampaignError(DAMAGED)
    kept_places = []
    for p in places:
        if not isinstance(p, dict) or set(p) != {"name", "level", "form"}:
            raise CampaignError(DAMAGED)
        if p["level"] not in ("", *CLOSE_LEVELS) or p["form"] not in ("", *CLOSE_FORMS):
            raise CampaignError(DAMAGED)
        kept_places.append(
            {"name": _text(p["name"], PLACE_NAME_MAX), "level": p["level"], "form": p["form"]}
        )
    kept_links = []
    names = {p["name"].casefold() for p in kept_places}
    for k in links:
        if not isinstance(k, dict) or set(k) != {"a", "kind", "b", "word", "sure"}:
            raise CampaignError(DAMAGED)
        if k["kind"] not in LINK_KINDS or not isinstance(k["sure"], bool):
            raise CampaignError(DAMAGED)
        # the same lists the reader keeps to (a hand-edited file carries no free words into what
        # 6b-2 will write): ends on the map's places, and a word only on its kind's list
        ends = (k["a"], k["b"])
        if not all(isinstance(e, str) and e.casefold() in names for e in ends):
            raise CampaignError(DAMAGED)
        if k["a"].casefold() == k["b"].casefold() or not isinstance(k["word"], str):
            raise CampaignError(DAMAGED)
        listed = {
            NEXT_TO: next_words.word_of, ROUTE_TO: route_words.means_of,
        }.get(k["kind"], lambda _w: "")  # fmt: skip
        if k["word"] != listed(k["word"]):
            raise CampaignError(DAMAGED)
        kept_links.append(
            {
                "a": _text(k["a"], PLACE_NAME_MAX), "kind": k["kind"],
                "b": _text(k["b"], PLACE_NAME_MAX),
                "word": _text(k["word"], LINK_WORD_MAX, empty_ok=True), "sure": k["sure"],
            }
        )  # fmt: skip
    return json.dumps({"level": level, "places": kept_places, "links": kept_links})


def _text(value: object, limit: int, *, empty_ok: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not value.strip() and not empty_ok):
        raise CampaignError(DAMAGED)
    return value


class MapNotesSection:
    """A campaign's map descriptions in its backups (an `ExportSection`)."""

    name = "map_notes"

    async def dump(self, conn: Conn, guild_id: int, campaign_id: str) -> list[Any]:
        cur = await conn.execute(
            "SELECT user_id, made_at, alt, layout, places, close_read FROM map_notes"
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
                # (only when there is one, so a file of notes with none reads as before)
                **({"close": r["close_read"]} if r["close_read"] is not None else {}),
            }
            for r in await cur.fetchall()
        ]

    def check(self, rows: list[Any]) -> list[_Checked]:
        if not isinstance(rows, list) or len(rows) > KEEP:
            raise CampaignError(DAMAGED)
        out: list[_Checked] = []
        for row in rows:
            keys = {"by", "at", "alt", "layout", "places"}
            if not isinstance(row, dict) or set(row) - {"close"} != keys:
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
                    _close(row["close"]) if "close" in row else None,
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
                " places, close_read) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                (guild_id, campaign_id, r.user_id, r.made_at, r.alt, r.layout, r.places, r.close),
            )

    async def clear(self, conn: Conn, guild_id: int, campaign_id: str) -> None:
        await conn.execute(
            "DELETE FROM map_notes WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
