"""The rows of a campaign's maps (#4344, MapBot layers slice 3a): the two yeses, the one function
that says whether DMbot may use a map, and the one reader of `campaign_maps` (the CI test
`test_map_readers` holds every other file to it).

**Two yeses, two fields** (design rule 3). A map's row exists only after its uploader's right-to-use
yes (`right_by`, `right_at`); a DM's own upload carries the DM's yes to using it too. A player's map
waits for a DM (`use_ok` NULL), and `may_use_map` is false until a DM says yes. Nothing that reads a
map (the close read, positions, links, layer rows, answers) may go around `may_use_map`.

The store cannot see Discord, so who may do what (a DM answers, a DM or the poster removes) is the
caller's check, as in `pictures/store.py`. A map's picture is kept permanent while it is a map, so
the rolling removal of old pictures never takes it; removing the map clears that again.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NamedTuple

from dmbot.db import Conn, Database
from dmbot.memory._changes import MAP_FACTS, MAPS
from dmbot.memory.models import Written, new_id
from dmbot.memory.store import MemoryStore
from dmbot.pictures.store import MB, Usage, _lock, _usage

# Fixed lists in code, each with "other" (design rule 6).
KINDS = ("world", "region", "settlement", "building", "battle", "other")
CAME_FROM = ("discord", "website", "document", "import")
DM_ONLY = "dm"
TABLE = "table"
AUDIENCES = (DM_ONLY, TABLE)

NOT_YET = "DMbot can't keep a map before you say you may use it."
NOT_KEPT = "That picture isn't kept here any more."
ALREADY = "That picture is already a map."
FULL = "Permanent pictures are full. Remove one to make room."
BAD = "That isn't one of the choices."
GONE = "That map is gone. Open the Maps page again to see it as it is now."


class MapError(Exception):
    """A refusal, with the words to show."""


@dataclass(frozen=True, slots=True)
class Map:
    id: str
    picture_id: int
    title: str
    covers: str | None
    kind: str
    width: int | None
    height: int | None
    audience: str
    came_from: str
    import_tool: str
    right_by: int
    right_at: int
    use_ok: bool | None  # None: waiting for a DM; True: yes; False: no
    use_by: int | None
    use_at: int | None
    created_at: int
    replaces: str | None = None
    as_of: int | None = None

    @property
    def waiting(self) -> bool:
        """A map whose uploader said yes and no DM has answered yet."""
        return self.use_ok is None


class Viewer(NamedTuple):
    """Who is looking (design section 8.1): a DM sees every map; whoever may read the campaign's
    transcripts sees the `table` ones; anybody else sees none. The caller decides which."""

    dm: bool = False
    table: bool = False


def may_use_map(m: Map) -> bool:
    """THE answer to "may DMbot use this map?" (design rule 3): both yeses are on the row. Every
    reader of maps calls this; a map waiting for a DM, or one a DM said no to, is never used. (A
    picture in chat that is not a map yet has `dmbot.map_use.may_use_map`, until 6b-2 makes this
    the only one, #4349.)"""
    return m.use_ok is True


COLUMNS = (
    "id, picture_id, title, covers, kind, width, height, audience, came_from, import_tool,"
    " right_by, right_at, use_ok, use_by, use_at, created_at, replaces, as_of"
)


def _map(r: Any) -> Map:
    return Map(
        id=str(r["id"]),
        picture_id=int(r["picture_id"]),
        title=str(r["title"]),
        covers=None if r["covers"] is None else str(r["covers"]),
        kind=str(r["kind"]),
        width=None if r["width"] is None else int(r["width"]),
        height=None if r["height"] is None else int(r["height"]),
        audience=str(r["audience"]),
        came_from=str(r["came_from"]),
        import_tool=str(r["import_tool"]),
        right_by=int(r["right_by"]),
        right_at=int(r["right_at"]),
        use_ok=None if r["use_ok"] is None else bool(r["use_ok"]),
        use_by=None if r["use_by"] is None else int(r["use_by"]),
        use_at=None if r["use_at"] is None else int(r["use_at"]),
        created_at=int(r["created_at"]),
        replaces=None if r["replaces"] is None else str(r["replaces"]),
        as_of=None if r["as_of"] is None else int(r["as_of"]),
    )


def _seen(viewer: Viewer) -> str:
    """The clause for a viewer (section 8.1). A DM sees every map; whoever may read the transcripts
    sees the `table` ones that DMbot may use, so a map a DM said no to, or one still waiting for a
    DM, is not listed to the players."""
    if viewer.dm:
        return ""
    if viewer.table:
        return " AND audience = 'table' AND use_ok IS TRUE"
    return " AND FALSE"


class MapStore:
    """A campaign's maps. Every write is one batch of the change log, so one DM action is one Undo
    (design rule 8, #4346): they go through `MemoryStore.map_write`, take the picture store's lock
    first, and set the picture's permanent flag in the same transaction."""

    def __init__(
        self, db: Database, memory: MemoryStore, clock: Callable[[], float] = time.time
    ) -> None:
        self._db = db
        self._memory = memory
        self._clock = clock

    async def add_map(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        picture_id: int,
        uploader_id: int,
        uploader_is_dm: bool,
        right_to_use: bool,
        kind: str,
        came_from: str,
        title: str = "",
        audience: str = DM_ONLY,
        import_tool: str = "",
        covers: str | None = None,
        width: int | None = None,
        height: int | None = None,
        storage_mb: int,
    ) -> Written[Map]:
        """Keep a kept picture as a map. Refused (`MapError`) unless the uploader said yes to "Is
        this a map you're allowed to use and save in this campaign?" (`right_to_use`). A DM's own
        upload carries the DM's yes to using it as well; a player's waits for a DM. The picture is
        made permanent in the same step (so the rolling removal never takes it), refused when
        permanent pictures are full. The result's batch is the Undo."""
        if not right_to_use:
            raise MapError(NOT_YET)
        if kind not in KINDS or came_from not in CAME_FROM or audience not in AUDIENCES:
            raise MapError(BAD)
        if audience == TABLE and came_from != "discord":
            raise MapError(BAD)  # only a map posted in the table's chat starts shown to the table
        if (came_from == "import") != bool(import_tool):
            raise MapError(BAD)
        if (width is not None and width <= 0) or (height is not None and height <= 0):
            raise MapError(BAD)
        now = int(self._clock())
        async with self._memory.map_write(guild_id, campaign_id) as w:
            await _lock(w.conn, guild_id, campaign_id)  # (the picture store's lock, first)
            pic = await _picture(w.conn, guild_id, campaign_id, picture_id)
            if pic is None:
                raise MapError(NOT_KEPT)
            if covers is not None:  # (a place of this campaign, or the foreign key would refuse)
                cur = await w.conn.execute(
                    "SELECT 1 FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                    " AND id = %s",
                    (guild_id, campaign_id, covers),
                )
                if await cur.fetchone() is None:
                    raise MapError(BAD)
            if await w.select(MAPS, " AND picture_id = %s", [picture_id]):
                raise MapError(ALREADY)
            if not pic["permanent"]:
                usage = await _usage(w.conn, guild_id, campaign_id, storage_mb)
                if _full(usage, int(pic["size_bytes"]) + int(pic["preview_bytes"])):
                    raise MapError(FULL)
                await w.conn.execute(
                    "UPDATE campaign_pictures SET permanent = TRUE WHERE id = %s", (picture_id,)
                )
            row = await w.insert(
                MAPS,
                {
                    "id": new_id(), "picture_id": picture_id, "title": title[:120],
                    "covers": covers, "kind": kind, "width": width, "height": height,
                    "scale_value": None, "scale_unit": None, "scale_from": None,
                    "grid_kind": "none", "grid_cell_px": None, "grid_cell_distance": None,
                    "north_angle": 0.0, "north_from": "assumed", "audience": audience,
                    "came_from": came_from, "import_tool": import_tool, "right_by": uploader_id,
                    "right_at": now, "use_ok": True if uploader_is_dm else None,
                    "use_by": uploader_id if uploader_is_dm else None,
                    "use_at": now if uploader_is_dm else None, "as_of": None, "replaces": None,
                    "created_at": now,
                },
            )  # fmt: skip
            return Written(_map(row), w.batch)

    async def answer_use(
        self, guild_id: int, campaign_id: str, map_id: str, *, dm_id: int, yes: bool
    ) -> Written[Map]:
        """A DM's yes or no to "A map from <player>: use it?". A no leaves the picture kept and
        unused; removing it is a separate tap. The caller has checked that `dm_id` is a DM of this
        campaign. Answering again changes the answer, and the later one stands."""
        async with self._memory.map_write(guild_id, campaign_id) as w:
            await _lock(w.conn, guild_id, campaign_id)
            if await w.get(MAPS, map_id) is None:
                raise MapError(GONE)
            row = await w.update(
                MAPS, map_id, {"use_ok": yes, "use_by": dm_id, "use_at": int(self._clock())}
            )
            return Written(_map(row), w.batch)

    async def set_audience(
        self, guild_id: int, campaign_id: str, map_id: str, audience: str
    ) -> Written[Map]:
        """ "Show to the table" (or back to the DM only). The caller has checked that this is a DM,
        and has shown the warning "Players will see everything drawn on this map." first."""
        if audience not in AUDIENCES:
            raise MapError(BAD)
        async with self._memory.map_write(guild_id, campaign_id) as w:
            await _lock(w.conn, guild_id, campaign_id)
            if await w.get(MAPS, map_id) is None:
                raise MapError(GONE)
            row = await w.update(MAPS, map_id, {"audience": audience})
            return Written(_map(row), w.batch)

    async def remove_map(self, guild_id: int, campaign_id: str, map_id: str) -> Written[Map | None]:
        """Take a picture's map status away (a DM can always; its poster can too). The picture
        stays a picture, no longer permanent (Supervisor's ruling on #4344: a pin the DM set before
        the map existed is cleared too). Facts that point at the map are cleared first, through
        the log, so one Undo puts them back with it. No such map: nothing, and no batch."""
        async with self._memory.map_write(guild_id, campaign_id) as w:
            await _lock(w.conn, guild_id, campaign_id)
            row = await w.get(MAPS, map_id)
            if row is None:
                return Written(None, None)
            for fact in await w.select(MAP_FACTS, " AND map_id = %s", [map_id]):
                await w.update(MAP_FACTS, str(fact["id"]), {"map_id": None})
            for other in await w.select(MAPS, " AND replaces = %s", [map_id]):
                await w.update(MAPS, str(other["id"]), {"replaces": None})
            await w.delete(MAPS, map_id)
            await w.conn.execute(
                "UPDATE campaign_pictures SET permanent = FALSE WHERE id = %s",
                (int(row["picture_id"]),),
            )
            return Written(_map(row), w.batch)

    async def maps(
        self,
        guild_id: int,
        campaign_id: str,
        viewer: Viewer,
        *,
        usable_only: bool = False,
    ) -> list[Map]:
        """THE reader of `campaign_maps`: the maps this viewer may see (section 8.1), oldest
        first. `usable_only` is what every reader that works from a map's content asks for (rule
        3): only maps `may_use_map` allows."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM campaign_maps WHERE guild_id = %s AND campaign_id = %s"
                f"{_seen(viewer)} ORDER BY created_at, id",
                (guild_id, campaign_id),
            )
            found = [_map(r) for r in await cur.fetchall()]
        return [m for m in found if may_use_map(m)] if usable_only else found

    async def get(self, guild_id: int, campaign_id: str, map_id: str, viewer: Viewer) -> Map | None:
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM campaign_maps WHERE guild_id = %s AND campaign_id = %s"
                f" AND id = %s{_seen(viewer)}",
                (guild_id, campaign_id, map_id),
            )
            row = await cur.fetchone()
        return None if row is None else _map(row)

    async def waiting_for_a_dm(self, guild_id: int, campaign_id: str) -> list[Map]:
        """The maps a player said yes to and no DM has answered ("❓ Questions for you")."""
        return [m for m in await self.maps(guild_id, campaign_id, Viewer(dm=True)) if m.waiting]


async def _picture(conn: Conn, guild_id: int, campaign_id: str, picture_id: int) -> Any:
    cur = await conn.execute(
        "SELECT permanent, size_bytes, preview_bytes FROM campaign_pictures"
        " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND deleted_at IS NULL",
        (guild_id, campaign_id, picture_id),
    )
    return await cur.fetchone()


def _full(usage: Usage, need: int) -> bool:
    """Would one more permanent picture of this size pass the 80% share?"""
    return usage.permanent_bytes + need > usage.permanent_limit_bytes


__all__ = ["MB", "Map", "MapError", "MapStore", "Viewer", "may_use_map"]
