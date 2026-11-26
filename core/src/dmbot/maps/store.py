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

import dataclasses
import re
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from dmbot.db import Conn, Database
from dmbot.memory._changes import MAP_FACTS, MAP_FEATURES, MAPS
from dmbot.memory.models import Written, new_id
from dmbot.memory.store import MemoryStore
from dmbot.pictures.store import MB, Usage, _lock, _usage

if TYPE_CHECKING:
    from dmbot.maps.disagree import Direction, Distance
    from dmbot.maps.legs import Route, Total
    from dmbot.maps.measure import MapInfo, Pin
    from dmbot.maps.measure_words import NotShown
    from dmbot.maps.said_phrases import Said
    from dmbot.memory._changes import Changes

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
NOT_YOURS = "Only the person who posted it can take it back."
GONE = "That map is gone. Open the Maps page again to see it as it is now."
# A map from a DM's uploaded file stays with the DMs (#4259; its picture is DM-only, #4003 slice c)
DMS_FILE = (
    "This map can't be shown to the table: it came from a DM's file, so it stays with the DMs."
)


class MapError(Exception):
    """A refusal, with the words to show."""


class AlreadyAnswered(MapError):
    """A DM's answer came second (`answer_use(first_only=True)`): the first one stands. Carries
    the map as it is, so the caller can say who answered."""

    def __init__(self, answered: Map) -> None:
        super().__init__("That's already answered.")
        self.map = answered


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
    # A player's own Discord id: they also see the maps they uploaded themselves, in any state
    # (so they can see theirs is waiting for a DM), never anyone else's hidden one (#4347).
    own: int | None = None


def may_use_map(m: Map) -> bool:
    """THE answer to "may DMbot use this map?" (design rule 3): both yeses are on the row. Every
    reader of maps calls this; a map waiting for a DM, or one a DM said no to, is never used. The
    only one (#4349): a picture in chat that is not a map yet is never read closely at all."""
    return m.use_ok is True


# The close read's sweep (#4349): at most this many failed reads of one map (the migration's CHECK
# holds the same 3), and a claim or a failure older than this is taken again: a sweep that stopped
# mid-read (the bot restarted), or the wait before the next try.
CLOSE_READ_TRIES = 3
CLOSE_READ_LEASE_S = 1800
# A map the sweep may take: allowed, and not read, not being read (or that claim is stale) and not
# failed too often. Its two numbers are the tries limit and the oldest live claim, in that order.
_CLAIMABLE = (
    "use_ok IS TRUE AND close_read_tries < %s"
    " AND (close_read_at IS NULL OR (close_read_tries > 0 AND close_read_at < %s))"
)

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
    DM, is not listed to the players, except to the person who uploaded it (`own`)."""
    if viewer.dm:
        return ""
    # `own` is an int by type and is cast again here, so nothing but a number reaches the SQL text.
    own = "" if viewer.own is None else f" OR right_by = {int(viewer.own)}"
    if viewer.table:
        return f" AND ((audience = 'table' AND use_ok IS TRUE){own})"
    return f" AND (FALSE{own})"


async def maps_and_pins(
    conn: Conn, guild_id: int, campaign_id: str, place_ids: list[str], *, dm: bool
) -> tuple[list[MapInfo], list[Pin]]:
    """The maps an answer or a check may measure on, and these places' pins on them (#4670): for
    the DM (`dm`), every map DMbot may use; otherwise only the table's (section 8.1's one clause,
    `_seen`); never a map another one replaces (§4.4: the newer version answers), and only maps
    `may_use_map` allows. Rejected pins never count. `measure.MapInfo` and `measure.Pin` rows."""
    from dmbot.maps import measure

    cur = await conn.execute(
        f"SELECT {COLUMNS}, scale_value, scale_unit, north_angle, north_from"
        " FROM campaign_maps WHERE guild_id = %s AND campaign_id = %s"
        + _seen(Viewer(dm=dm, table=not dm)),
        (guild_id, campaign_id),
    )
    rows = await cur.fetchall()
    cur = await conn.execute(
        "SELECT map_id, place_id, points FROM map_features WHERE guild_id = %s"
        " AND campaign_id = %s AND shape = 'pin' AND status <> 'rejected'"
        " AND place_id = ANY(%s)",
        (guild_id, campaign_id, place_ids),
    )
    pins = [
        measure.Pin(str(r["map_id"]), str(r["place_id"]), r["points"][0], r["points"][1])
        for r in await cur.fetchall()
    ]
    replaced = {str(r["replaces"]) for r in rows if r["replaces"]}
    maps = [
        measure.MapInfo(
            m.id, m.title, m.kind, m.width, m.height, r["scale_value"],
            str(r["scale_unit"] or ""), float(r["north_angle"]), r["north_from"] != "assumed",
            m.created_at,
        )
        for r in rows
        if (m := _map(r)).id not in replaced
        and may_use_map(m)  # (the one answer to "may DMbot use this map?")
    ]  # fmt: skip
    return maps, pins


@dataclass(frozen=True, slots=True)
class RoadOff:
    """A road whose stated length a kept map's straight line disagrees with (#4670 4c, §9)."""

    relation_id: str
    a_id: str
    b_id: str
    stated: str  # the length's own words ("20 miles")
    session_at: int | None  # the session the road came from; None for lore or an upload
    created_at: int
    off: Distance  # the map and both figures


async def roads_off(
    conn: Conn, guild_id: int, campaign_id: str, *, read_only: bool, relation_ids: list[str] | None
) -> list[RoadOff]:
    """Roads whose stated length in miles is far shorter than a kept map's straight line between
    their two kept places (`disagree.check_distance`), worked out now and never stored (Supervisor's
    ruling on #4670). DM's maps only (`dm=True`): both notes are the DM's alone. `read_only`: only
    lengths DMbot read and the DM hasn't kept (World notes; a length the DM kept gets no note
    later, ruling 3). `relation_ids`: only these roads (the card's own). A travel time is never
    compared (§9), so only `length_unit = 'miles'`."""
    from dmbot.maps import disagree

    cur = await conn.execute(
        "SELECT r.id, r.subject_id, r.object_id, r.length_words, r.length_amount,"
        " r.from_session_at, r.created_at FROM memory_relations r"
        " JOIN memory_entities a ON a.guild_id = r.guild_id AND a.campaign_id = r.campaign_id"
        " AND a.id = r.subject_id AND a.status IN ('confirmed', 'proposed')"
        " AND a.merged_into IS NULL"
        " JOIN memory_entities b ON b.guild_id = r.guild_id AND b.campaign_id = r.campaign_id"
        " AND b.id = r.object_id AND b.status IN ('confirmed', 'proposed')"
        " AND b.merged_into IS NULL"
        " WHERE r.guild_id = %s AND r.campaign_id = %s AND r.predicate = 'route_to'"
        # (a kept road only: one still waiting is asked about on its own, never twice)
        " AND r.status = 'confirmed' AND r.to_session_at IS NULL AND r.length_unit = 'miles'"
        + (" AND r.length_from = 'read'" if read_only else "")
        + (" AND r.id = ANY(%s)" if relation_ids is not None else "")
        + " ORDER BY r.created_at, r.id",
        (guild_id, campaign_id, *([relation_ids] if relation_ids is not None else [])),
    )
    roads = await cur.fetchall()
    if not roads:
        return []
    places = sorted({str(r[k]) for r in roads for k in ("subject_id", "object_id")})
    maps, pins = await maps_and_pins(conn, guild_id, campaign_id, places, dm=True)
    out: list[RoadOff] = []
    for r in roads:
        a, b = str(r["subject_id"]), str(r["object_id"])
        off = disagree.check_distance(maps, pins, a, b, float(r["length_amount"]))
        if off is not None:
            out.append(
                RoadOff(
                    str(r["id"]), a, b, str(r["length_words"]), r["from_session_at"],
                    int(r["created_at"]), off,
                )
            )  # fmt: skip
    return out


async def _shown_at(w: Changes, map_id: str) -> int:
    """The story time a map was shown (§7.4): the start of the last session that began before its
    picture was posted (not when it was kept as a map, which can be later), else the campaign's
    own start."""
    cur = await w.conn.execute(
        "SELECT COALESCE((SELECT max(s.started_at) FROM transcript_sessions s, campaign_maps m"
        " JOIN campaign_pictures p ON p.guild_id = m.guild_id AND p.campaign_id = m.campaign_id"
        " AND p.id = m.picture_id"
        " WHERE s.guild_id = %(g)s AND s.campaign_id = %(c)s AND m.guild_id = %(g)s"
        " AND m.campaign_id = %(c)s AND m.id = %(m)s AND s.started_at <= p.made_at),"
        " (SELECT created_at FROM campaigns WHERE guild_id = %(g)s AND id = %(c)s)) AS at",
        {"g": w.guild_id, "c": w.campaign_id, "m": map_id},
    )
    row = await cur.fetchone()
    return int(row["at"]) if row is not None and row["at"] is not None else int(w.now)


_MILES = re.compile(r"^(?:about\s+)?(\d+(?:\.\d+)?)\s*(?:miles?|mi)\.?$", re.I)


async def _road_route(
    scope: Any,
    a: str,
    b: str,
    viewer: Any,
    ends: tuple[str, str],
    *,
    known: Collection[str] | None = None,
) -> tuple[Route, Total | str, dict[str, str]] | None:
    """A route of kept, open roads from `a` to `b` taken leg by leg (#4260 10d, `maps.legs`), when
    no road joins them directly: the route, its total (or why there is none, `legs.total`) and
    the stops' names. `known`: for a player's answer, the connections and places the
    party knows; the route is then found among those only, so it never hints at a road or a place
    the party doesn't know (Supervisor's ruling on #4260), and a length DMbot read and the DM
    hasn't kept is no length (#4255). A secret or not-yet-said road, or one known only through a
    secret other name, is never a player's leg, and a player's stops are named as the lists name
    them (`gate.names_for`, never a secret main name; the reviewer's); a stop with no name for
    them is no route. `ends`: the two names as the asker said them (as every map answer does).
    Conditions through THE reader."""
    from dmbot.maps import legs
    from dmbot.memory import gate, map_fact_store

    cur = await scope.conn.execute(
        "SELECT r.id, r.subject_id, r.object_id, r.length_words, r.length_from, r.secret,"
        " r.unrevealed, r.via_alias_id FROM memory_relations r"
        " JOIN memory_entities s ON s.guild_id = r.guild_id AND s.campaign_id = r.campaign_id"
        " AND s.id = r.subject_id JOIN memory_entities o ON o.guild_id = r.guild_id"
        " AND o.campaign_id = r.campaign_id AND o.id = r.object_id"
        " WHERE r.guild_id = %s AND r.campaign_id = %s AND r.predicate = 'route_to'"
        " AND r.status = 'confirmed' AND r.to_session_at IS NULL"
        " AND s.status = 'confirmed' AND o.status = 'confirmed' ORDER BY r.id",
        (scope.guild_id, scope.campaign_id),
    )
    rows = [
        r for r in await cur.fetchall()
        if known is None or (
            str(r["id"]) in known and str(r["subject_id"]) in known and str(r["object_id"]) in known
            and not r["secret"] and not r["unrevealed"] and r["via_alias_id"] is None
        )
    ]  # fmt: skip
    places = {str(r[k]) for r in rows for k in ("subject_id", "object_id")} | {a, b}
    cur = await scope.conn.execute(
        "SELECT id, name FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
        " AND id = ANY(%s) AND status = 'confirmed'",
        (scope.guild_id, scope.campaign_id, sorted(places)),
    )
    names = {str(r["id"]): str(r["name"]) for r in await cur.fetchall()}
    # (a length DMbot read is the DM's to check (#4255): a player's leg shows no length for it)
    roads = [
        legs.Road(
            str(r["id"]), str(r["subject_id"]), str(r["object_id"]),
            "" if known is not None and r["length_from"] == "read"
            else str(r["length_words"] or "").strip(),
        )
        for r in rows
    ]  # fmt: skip
    found = legs.find(roads, a, b, names)
    if found is None:
        return None
    if known is not None:
        wanted = [*found.stops, *([found.other_via] if found.other_via else [])]
        said = await gate.names_for(scope, viewer, wanted)
        if any(said.get(i) is None for i in wanted):
            return None  # (a stop the lists wouldn't name for them: today's answer)
        names = {i: str(said[i]) for i in wanted}
    names = {**names, a: ends[0], b: ends[1]}
    ids = [leg.road.id for leg in found.legs]
    now = await map_fact_store.conditions_for(scope, viewer, relation_ids=ids, shown=known)
    shut = {i for i, c in now.items() if c.now.value == "closed"}
    found = dataclasses.replace(
        found,
        legs=tuple(
            dataclasses.replace(leg, road=dataclasses.replace(leg.road, closed=leg.road.id in shut))
            for leg in found.legs
        ),
    )
    return found, legs.total(found), names


async def _trip(
    scope: Any,
    a: str,
    b: str,
    asked: Any,
    rules: tuple[str, str],
    *,
    offer: bool = False,
    stated_days: float | None = None,
) -> str | None:
    """The travel-time suggestion's words (#4260 slice 10a; design §9), for the DM. The miles: a
    stated length in miles on the road between them, else a map's straight line. A stated length
    that isn't miles (a time, "two days by cart") is given back as it is, never summed. With no
    road between them but a chain of roads (#4260 10d), the legs, and their total when it is in
    miles. The pace: on foot, capped by the land at either end (at every stop on a route). Nothing
    is stored or changed. `offer`: for the unasked offer (design §9 "When it is offered"), None
    unless a time was worked out (a stated
    time on the road is the DM's world already, and what is missing is no offer), and None when
    `stated_days`, the DM's own time, is already within the estimate's paces (nothing new to say:
    the ux-critic's and the reviewer's, so a DM who is right is never interrupted)."""
    from dmbot.maps import legs, legs_words, measure, travel, travel_words
    from dmbot.maps.measure_words import LORE_SAYS, SESSION_SAID
    from dmbot.memory import gate, map_fact_store, route_length
    from dmbot.memory import map_facts as mf
    from dmbot.rules.index import srd
    from dmbot.sidebar.context import source_of

    ids = (scope.guild_id, scope.campaign_id)
    cur = await scope.conn.execute(
        "SELECT id, length_words, length_from, from_session_at FROM memory_relations"
        " WHERE guild_id = %s AND campaign_id = %s AND predicate = 'route_to'"
        " AND status = 'confirmed' AND to_session_at IS NULL AND ((subject_id = %s AND"
        " object_id = %s) OR (subject_id = %s AND object_id = %s))"
        " ORDER BY length_words IS NULL, id LIMIT 1",
        (*ids, a, b, b, a),
    )
    road = await cur.fetchone()
    # (through THE readers of the layer rows, as the DM sees them: a row DMbot read and the DM
    # hasn't checked counts too, since the answer is the DM's own and says "about")
    dm_view = gate.Viewer(dm=True)
    rows = await map_fact_store.facts_for(scope, [a, b], dm_view)
    lands = [
        f.value for found in rows.values() for f in found
        if f.layer == mf.TERRAIN and f.value != mf.OTHER
    ]  # fmt: skip
    shut = False
    if road is not None:
        now = await map_fact_store.conditions_for(scope, dm_view, relation_ids=[str(road["id"])])
        shut = any(c.now.value == "closed" for c in now.values())
    names = (asked.a, asked.b)
    stated = str(road["length_words"] or "").strip() if road is not None else ""
    if stated:
        assert road is not None
        by_dm, in_session = road["length_from"] == "dm", road["from_session_at"] is not None
        found = _MILES.match(stated)
        if found is None:  # (a time or another unit: the DM's own words, given back)
            if offer:
                return None
            said = "you set" if by_dm else SESSION_SAID if in_session else LORE_SAYS
            text = travel_words.stated_time(stated, *names, said)
            return f"{text} {travel_words.closed()}" if shut else text
        where = "as you set it" if by_dm else "from a session" if in_session else "from your lore"
        miles = float(found[1])
        distance = travel_words.by_road(stated, *names, where)
    elif (route := await _road_route(scope, a, b, dm_view, (asked.a, asked.b))) is not None:
        # (#4260 10d: no road between them, but a chain of roads: the legs, and a time only from a
        # total in miles; any other total, or none, is the legs as they were said)
        on_road, whole, called = route
        distance = legs_words.route(on_road, called, whole)
        if not isinstance(whole, legs.Total) or whole.unit != route_length.MILES:
            return None if offer else distance
        miles = whole.amount
        at_stops = await map_fact_store.facts_for(scope, list(on_road.stops), dm_view)
        lands = [
            f.value for kept in at_stops.values() for f in kept
            if f.layer == mf.TERRAIN and f.value != mf.OTHER
        ]  # fmt: skip
    else:
        maps, pins = await maps_and_pins(scope.conn, *ids, [a, b], dm=True)
        far = measure.answer("far", maps, pins, (a, asked.a), (b, asked.b), dm=True)
        used, _ = measure.choose(maps, pins, a, b, need_scale=True)
        pa = measure.pin_of(pins, used.id, a) if used is not None else None
        pb = measure.pin_of(pins, used.id, b) if used is not None else None
        d = measure.distance(used, pa, pb) if used and pa and pb else None
        amount = measure.rounded(d, "miles") if d is not None else None
        if used is None or d is None or used.unit != "miles" or amount is None:
            return None if offer else far  # (what is missing, and its fix: no time without miles)
        miles = float(amount.split()[0])  # (the miles the answer says, so its sums match them)
        distance = travel_words.straight(amount, *names, used.title, road=road is not None)
    index = srd()
    pace = index.lookup("travel pace table", *rules, kind="rule") or index.lookup(
        "travel pace", *rules, kind="rule"
    )
    # (the land's cap is SRD 5.2's Travel Terrain; a 2014 campaign has none of its own, so the
    # newer one is used and cited, tagged, as the edition rule says: never applied uncited)
    terrain = index.lookup("travel terrain", rules[0], "2024", kind="rule") if lands else None
    plan = travel.plan(miles, lands if terrain is not None else [])
    if offer and stated_days is not None and plan.days:
        quickest, slowest = min(d.days for d in plan.days), max(d.days for d in plan.days)
        if quickest <= stated_days <= slowest:
            return None
    on_foot = asked.how in ("", "foot", "mounted", "wagon")  # (a boat's time on foot means nothing)
    parts: list[str] = []
    if on_foot:
        parts.append(travel_words.lead(plan, miles))
    if asked.how not in ("", "foot"):
        parts.append(travel_words.cannot(asked.how))
    if shut:
        parts.append(travel_words.closed())
    parts.append(distance)
    if on_foot:
        if not lands:
            parts.append(travel_words.no_land())
        sources = [source_of(h) for h in (pace, terrain) if h is not None]
        if sources:
            parts.append(travel_words.cite(sources))
        parts.append(travel_words.YOUR_CALL)
    return " ".join(parts)


async def _other_map_showing(w: Changes, fact_id: str, map_id: str) -> str | None:
    """The oldest other map DMbot may use whose area (not one the DM turned down) points at this
    fact (#4696), or None."""
    cur = await w.conn.execute(
        "SELECT m.id FROM map_features f JOIN campaign_maps m ON m.guild_id = f.guild_id"
        " AND m.campaign_id = f.campaign_id AND m.id = f.map_id"
        " WHERE f.guild_id = %s AND f.campaign_id = %s AND f.fact_id = %s AND m.id <> %s"
        " AND f.status <> 'rejected' AND m.use_ok IS TRUE ORDER BY m.created_at, m.id LIMIT 1",
        (w.guild_id, w.campaign_id, fact_id, map_id),
    )
    row = await cur.fetchone()
    return None if row is None else str(row["id"])


@dataclass(frozen=True, slots=True)
class SaidTrip:
    """A travel time the DM said (#4260): what was said, the two places' names, and the
    suggestion's words for the same trip."""

    said: Said
    a_name: str
    b_name: str
    text: str


@dataclass(frozen=True, slots=True)
class SaidOff:
    """A distance or direction the DM said that a kept map disagrees with (#4670 4c-2): what was
    said, the two places' names, and `disagree.Distance` or `disagree.Direction`."""

    said: Said
    a_name: str
    b_name: str
    off: Distance | Direction


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
        from_notice: str | None = None,
        right_at: int | None = None,
    ) -> Written[Map]:
        """Keep a kept picture as a map. Refused (`MapError`) unless the uploader said yes to "Is
        this a map you're allowed to use and save in this campaign?" (`right_to_use`). A DM's own
        upload carries the DM's yes to using it as well; a player's waits for a DM. The picture is
        made permanent in the same step (so the rolling removal never takes it), refused when
        permanent pictures are full. The result's batch is the Undo. `from_notice` (#4259): the
        upload notice of the document it came from, whose Undo removes it; `right_at`: when the
        right-to-use yes was given, when it was given before now (the upload's own stamp)."""
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
            if from_notice is not None:  # (its upload was undone: its pictures are going, #4259)
                cur = await w.conn.execute(
                    "SELECT 1 FROM upload_notices WHERE guild_id = %s AND campaign_id = %s"
                    " AND id = %s AND undone_at IS NOT NULL",
                    (guild_id, campaign_id, from_notice),
                )
                if await cur.fetchone() is not None:
                    raise MapError(NOT_KEPT)
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
                    "right_at": right_at if right_at is not None else now,
                    "use_ok": True if uploader_is_dm else None,
                    "use_by": uploader_id if uploader_is_dm else None,
                    "use_at": now if uploader_is_dm else None, "as_of": None, "replaces": None,
                    "grid_from": None,
                    "created_at": now,
                },
            )  # fmt: skip
            if from_notice is not None:
                # (not a logged column: an Undo that puts a removed map back must never point it at
                # a notice cleaned up since; the upload's own Undo is what reads it). Tied only to
                # a notice still there: one cleaned up since leaves the map as any other kept one.
                await w.conn.execute(
                    "UPDATE campaign_maps SET from_notice = %s WHERE guild_id = %s"
                    " AND campaign_id = %s AND id = %s AND EXISTS (SELECT 1 FROM upload_notices n"
                    " WHERE n.guild_id = %s AND n.campaign_id = %s AND n.id = %s)",
                    (from_notice, guild_id, campaign_id, row["id"], guild_id, campaign_id,
                     from_notice),
                )  # fmt: skip
            return Written(_map(row), w.batch)

    async def for_notice(self, guild_id: int, campaign_id: str, notice_id: str) -> list[str]:
        """The ids of the maps an upload's document gave (#4259), for its Undo."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT id FROM campaign_maps WHERE guild_id = %s AND campaign_id = %s"
                " AND from_notice = %s ORDER BY created_at, id",
                (guild_id, campaign_id, notice_id),
            )
            return [str(r["id"]) for r in await cur.fetchall()]

    async def answer_use(
        self,
        guild_id: int,
        campaign_id: str,
        map_id: str,
        *,
        dm_id: int,
        yes: bool,
        first_only: bool = False,
    ) -> Written[Map]:
        """A DM's yes or no to "A map from <player>: use it?". A no ends DMbot's use of the map:
        what it said goes by the deletion rule (`_forget_said`, §7.5; Supervisor's ruling 1 on
        #4694, #4695) in the same write, one Undo. DMbot's reads resting only on it go, the DM's
        word on a place stays, and every pin on it goes (the DM's own too: a refused map is never
        shown; Undo brings them back). The map row stays, refused, so a second DM is told the
        first answer, the poster still sees it as theirs, and nothing reads it again. The caller
        has checked that `dm_id` is a DM of this campaign. Without `first_only` (no caller today)
        answering again changes the answer; a Yes after a No brings nothing back, only Undo does.
        With `first_only` an answered map is left as it is and `AlreadyAnswered` says so (two DMs
        pressing at once: the first answer stands). The check and the write are one step, under
        the picture store's lock."""
        async with self._memory.map_write(guild_id, campaign_id) as w:
            await _lock(w.conn, guild_id, campaign_id)
            current = await w.get(MAPS, map_id)
            if current is None:
                raise MapError(GONE)
            if first_only and current["use_ok"] is not None:
                raise AlreadyAnswered(_map(current))
            row = await w.update(
                MAPS, map_id, {"use_ok": yes, "use_by": dm_id, "use_at": int(self._clock())}
            )
            if not yes:  # (the answer is logged first, so Undo puts the question back as it was)
                await self._forget_said(w, map_id)
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
            current = await w.get(MAPS, map_id)
            if current is None:
                raise MapError(GONE)
            if audience == TABLE:  # (a DM's file's map: its picture is for the DMs alone, #4259)
                cur = await w.conn.execute(
                    "SELECT dm_only FROM campaign_pictures WHERE guild_id = %s"
                    " AND campaign_id = %s AND id = %s",
                    (guild_id, campaign_id, int(current["picture_id"])),
                )
                pic = await cur.fetchone()
                if pic is not None and pic["dm_only"]:
                    raise MapError(DMS_FILE)
            row = await w.update(MAPS, map_id, {"audience": audience})
            return Written(_map(row), w.batch)

    async def remove_map(self, guild_id: int, campaign_id: str, map_id: str) -> Written[Map | None]:
        """Take a picture's map status away (a DM can always; its poster can too). The picture
        stays a picture, no longer permanent (Supervisor's ruling on #4344: a pin the DM set before
        the map existed is cleared too). What the map said goes by §7.5 (`_remove`), through the
        log, so one Undo puts it back with it. No such map: nothing, and no batch."""
        async with self._memory.map_write(guild_id, campaign_id) as w:
            await _lock(w.conn, guild_id, campaign_id)
            gone = await self._remove(w, map_id)
            return Written(gone, None if gone is None else w.batch)

    async def take_back(
        self, guild_id: int, campaign_id: str, picture_id: int, user_id: int
    ) -> Written[Map | None]:
        """A poster takes their own picture's map back (the Discord button and the website's
        "Take it back"): only the map goes, as `remove_map`, and the change's batch is written on
        the picture in the same transaction, which is the one batch the poster's Undo accepts
        (`PictureStore.taken_back_by`, #4390). Raises `MapError(NOT_YOURS)` for anyone but the
        picture's poster; no map for it: nothing, and no batch."""
        async with self._memory.map_write(guild_id, campaign_id) as w:
            await _lock(w.conn, guild_id, campaign_id)
            cur = await w.conn.execute(
                "SELECT poster_id FROM campaign_pictures WHERE guild_id = %s AND campaign_id = %s"
                " AND id = %s AND deleted_at IS NULL",
                (guild_id, campaign_id, picture_id),
            )
            posted = await cur.fetchone()
            if posted is None:
                raise MapError(NOT_KEPT)
            if int(posted["poster_id"]) != user_id:
                raise MapError(NOT_YOURS)
            found = await w.select(MAPS, " AND picture_id = %s", [picture_id])
            gone = await self._remove(w, str(found[0]["id"])) if found else None
            if gone is None:
                return Written(None, None)
            await w.conn.execute(
                "UPDATE campaign_pictures SET map_taken_back_batch = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s",
                (w.batch, guild_id, campaign_id, picture_id),
            )
            return Written(gone, w.batch)

    @staticmethod
    async def _remove(w: Changes, map_id: str) -> Map | None:
        """The removal itself, inside a write the caller has opened and locked."""
        row = await w.get(MAPS, map_id)
        if row is None:
            return None
        await MapStore._forget_said(w, map_id)
        for other in await w.select(MAPS, " AND replaces = %s", [map_id]):
            await w.update(MAPS, str(other["id"]), {"replaces": None})
        await w.delete(MAPS, map_id)
        await w.conn.execute(
            "UPDATE campaign_pictures SET permanent = FALSE WHERE guild_id = %s"
            " AND campaign_id = %s AND id = %s",
            (w.guild_id, w.campaign_id, int(row["picture_id"])),
        )
        return _map(row)

    @staticmethod
    async def _forget_said(w: Changes, map_id: str) -> None:
        """What a map said goes by the deletion rule, in the caller's write (§7.5): its pins, and
        its rows as below. A removal and a DM's No (#4695) both run it."""
        for feature in await w.select(MAP_FEATURES, " AND map_id = %s", [map_id]):
            # (its pins go with it, logged, so one Undo puts the map back with them: #4670)
            await w.delete(MAP_FEATURES, str(feature["id"]))
        # What the map said (§7.5, Supervisor's ruling on #4236 f; #4259 slice 8b), in the same
        # write, so one Undo puts it all back: DMbot's read resting only on this map goes with it;
        # a read the text also stated stays, as the text's; the DM's own word (and the DM's no)
        # stays, without the pointer; a fact another usable map's area still shows is first kept
        # and re-pointed to the oldest such map (#4696). A picture deleted with its map follows
        # the same rule
        # (`PictureStore.delete`); only a row whose map is gone some other way loses just the
        # pointer (the foreign key's ON DELETE SET NULL).
        for fact in await w.select(MAP_FACTS, " AND map_id = %s", [map_id]):
            fact_id = str(fact["id"])
            other = await _other_map_showing(w, fact_id, map_id)
            if other is not None:
                # (another usable map's area still shows it: it rests on that map now, kept and
                # re-pointed whoever said it, its area's pointer untouched; Supervisor's ruling 2
                # on #4694, #4696. The oldest such map, as the ruling says.)
                await w.update(MAP_FACTS, fact_id, {"map_id": other})
            elif fact["status"] == "read" and fact["source"] == "map" and not fact["also_text"]:
                for pinned in await w.select(MAP_FEATURES, " AND fact_id = %s", [fact_id]):
                    await w.update(MAP_FEATURES, str(pinned["id"]), {"fact_id": None})
                await w.delete(MAP_FACTS, fact_id)
            elif fact["status"] == "read" and fact["source"] == "map":
                # (the text is its source now, so "also the text" says nothing more)
                await w.update(
                    MAP_FACTS, fact_id, {"source": "text", "also_text": False, "map_id": None}
                )
            else:
                await w.update(MAP_FACTS, fact_id, {"map_id": None})

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
        """One map this viewer may see. With `Viewer.own` it can be the person's own map that is
        still waiting or was refused: a reader of a map's content must check `may_use_map` too."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM campaign_maps WHERE guild_id = %s AND campaign_id = %s"
                f" AND id = %s{_seen(viewer)}",
                (guild_id, campaign_id, map_id),
            )
            row = await cur.fetchone()
        return None if row is None else _map(row)

    # ---- the close read's sweep (#4349, MapBot 6b-2; migration 0488 says what the marks mean) ----

    async def close_read_tries(self, guild_id: int, campaign_id: str, map_id: str) -> int:
        """How many times the sweep has tried this map's close read (0 for a map that is gone):
        a count only, for `sample_run --map` to stop waiting once the bot has given up (#4423)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT close_read_tries FROM campaign_maps WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, map_id),
            )
            row = await cur.fetchone()
        return int(row["close_read_tries"]) if row else 0

    async def close_read_waiting(self, guild_id: int) -> list[tuple[str, Map]]:
        """The maps in this server still to be read closely, oldest first, each with its campaign:
        DMbot may use them (`may_use_map`), none has been read, and none failed 3 times. Claimed
        ones come back once their claim is older than `CLOSE_READ_LEASE_S`."""
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT campaign_id, {COLUMNS} FROM campaign_maps WHERE guild_id = %s"
                f" AND {_CLAIMABLE} ORDER BY created_at, id",
                (guild_id, CLOSE_READ_TRIES, now - CLOSE_READ_LEASE_S),
            )
            found = [(str(r["campaign_id"]), _map(r)) for r in await cur.fetchall()]
        return [(c, m) for c, m in found if may_use_map(m)]

    async def claim_close_read(self, guild_id: int, campaign_id: str, map_id: str) -> int | None:
        """Mark this map as being read, in the same statement that checks it still may be, so two
        sweeps never read one map (Supervisor's ruling). The claim's time, which the outcome must
        name (`close_read_done`, `close_read_failed`), or None when it can't be claimed now."""
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE campaign_maps SET close_read_at = %s,"
                " close_read_tries = close_read_tries + 1"
                f" WHERE guild_id = %s AND campaign_id = %s AND id = %s AND {_CLAIMABLE}"
                " RETURNING close_read_at",
                (now, guild_id, campaign_id, map_id, CLOSE_READ_TRIES, now - CLOSE_READ_LEASE_S),
            )
            row = await cur.fetchone()
        return None if row is None else int(row["close_read_at"])

    async def close_read_done(
        self, guild_id: int, campaign_id: str, map_id: str, claimed: int
    ) -> None:
        """The read is kept: the mark stays and the tries go back to 0, so it isn't read again."""
        await self._close_read_mark(guild_id, campaign_id, map_id, claimed, "close_read_tries = 0")

    async def close_read_failed(
        self, guild_id: int, campaign_id: str, map_id: str, claimed: int, *, counted: bool = True
    ) -> None:
        """The read didn't work. Counted: the mark stays, set to now, so the next try waits a lease
        (`CLOSE_READ_LEASE_S`: a short AI outage doesn't use up the tries); at `CLOSE_READ_TRIES`
        it is left alone. Not `counted` when it was put off rather than failed (no AI credits, the
        day's picture limit, the poster stopped mid-read): the try is given back and the mark
        cleared."""
        change = (
            f"close_read_at = {int(self._clock())}"
            if counted
            else "close_read_at = NULL, close_read_tries = GREATEST(close_read_tries - 1, 0)"
        )
        await self._close_read_mark(guild_id, campaign_id, map_id, claimed, change)

    async def _close_read_mark(
        self, guild_id: int, campaign_id: str, map_id: str, claimed: int, change: str
    ) -> None:
        # (only the claim that read it may say how it went: a later claim after a lapsed lease is
        # never undone by an earlier read finishing late)
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                f"UPDATE campaign_maps SET {change}"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND close_read_at = %s",
                (guild_id, campaign_id, map_id, claimed),
            )

    # ---- positions (#4670, MapBot slice 4a; Supervisor's ruling 1 on #4670) ----

    async def record_positions(
        self, guild_id: int, campaign_id: str, map_id: str, read: Any, *, layers: bool = False
    ) -> Any:
        """A close read's pins, scale, grid and north, as DMbot's own write (one batch),
        and the map marked as read for pins (`pins_read_at`, in the same transaction). Counts only
        come back (`positions.Recorded`). `layers` (DMBOT_MAP_LAYERS, #4259 slice 8a): what the
        map shows outright is written too, in the same transaction but a batch of its own
        (`Recorded.batch`: the note's Undo takes back what the note reports and leaves the pins,
        #4700), as layer rows for the DM to check (`source = 'map'`, this map, from the story time
        it was shown: §7.4)."""
        from collections import Counter

        from dmbot.maps import layers_read, positions
        from dmbot.memory import map_fact_store
        from dmbot.memory import map_facts as mf
        from dmbot.memory._changes import ALIASES, ENTITIES
        from dmbot.memory.store import _load_ontology

        async with self._memory._write(guild_id, campaign_id, "extractor") as w:
            await _lock(w.conn, guild_id, campaign_id)
            current = await w.get(MAPS, map_id)
            if current is None or current["use_ok"] is not True:
                # (asked again under the lock: a No that landed since the caller's check has
                # already forgotten what the map said, and nothing may be written onto it, #4695)
                return positions.Recorded()
            onto = await _load_ontology(w)
            entities = await w.select(ENTITIES, " AND status = 'confirmed'")
            aliases = await w.select(ALIASES, " AND status = 'confirmed'")
            names = positions.place_names(entities, aliases, lambda kind: onto.is_a(kind, "place"))
            done = await positions.record_in(w, map_id, read, names)
            if layers and read.shows:
                rulers = positions.place_names(
                    entities, aliases,
                    lambda kind: onto.is_a(kind, "faction") or onto.is_a(kind, "place"),
                )  # fmt: skip
                rows = layers_read.reads(read.shows, names, rulers)
                written: Counter[str] = Counter()
                w.new_batch()  # (the rows' own Undo, apart from the pins', #4700; anything logged
                # after this joins the rows' batch: only the unlogged `pins_read_at` follows)
                counts = await map_fact_store.add_read(
                    w, onto, rows, source=mf.FROM_MAP, map_id=map_id,
                    shown_at=await _shown_at(w, map_id), layers_written=written,
                )  # fmt: skip
                done.layers, done.written = dict(counts), dict(written)
                done.batch = w.batch
            await w.conn.execute(
                "UPDATE campaign_maps SET pins_read_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s",
                (int(self._clock()), guild_id, campaign_id, map_id),
            )
        return done

    async def measure(
        self,
        guild_id: int,
        campaign_id: str,
        asked: Any,
        *,
        dm: bool,
        rules: tuple[str, str] = ("2024", "none"),
        live_at: int | None = None,
    ) -> str | NotShown | None:
        """The words of a "how far" or "which way" answer (#4670 4b, `measure.answer`), or None
        when the usual answer should go on instead: the two names are not both kept places the
        asker may know, or (for "how far") the story or the DM has stated a length between them,
        which beats a measured one (§9). `dm`: every map DMbot may use; otherwise only the table's
        maps, and places the players have been shown, worked out as every list does (the knowledge
        gate; with no party, common knowledge only), so a DM-only map's pins, scale and distances
        never reach a player (Supervisor's condition). `NOT_SHOWN` (#4692): not `dm`, and both
        names are kept places to the DM, but one is not shown to the players (hidden, limited,
        not yet known, or named by a secret other name): a DM's question then goes privately.
        "How long" (#4260 slice 10a) is the DM's only (`_trip`); `rules` are the campaign's target
        and fallback rulesets, for its citation."""
        from dmbot.maps import legs_words, measure, measure_phrase, positions
        from dmbot.maps.measure_words import NOT_SHOWN
        from dmbot.memory import gate
        from dmbot.memory._changes import ALIASES, ENTITIES
        from dmbot.memory.models import name_key
        from dmbot.memory.store import _load_ontology

        if asked.kind == measure_phrase.LONG and not dm:
            return None  # (travel time is the DM's: the sidebar says so before asking here)

        async with self._memory._read(guild_id, campaign_id, snapshot=True) as scope:
            onto = await _load_ontology(scope)
            entities = await scope.select(ENTITIES, " AND status = 'confirmed'")
            # (a secret other name is the DM's: a player's question never matches by one)
            aliases = await scope.select(
                ALIASES, " AND status = 'confirmed'" + ("" if dm else " AND NOT secret")
            )
            names = positions.place_names(entities, aliases, lambda kind: onto.is_a(kind, "place"))
            shown: set[str] | None = None
            # (`live_at`: the start of this campaign's live table, if the bot is at one: #4537 3b)
            # (the table: the party at the live table's session, else today's party, else common
            # knowledge only, failing closed: Supervisor's ruling M on #4537, slice 5c)
            table = await gate.table_viewer(scope, live_at) if not dm else gate.Viewer.the_party()
            if not dm:
                shown = set(await gate.entries_for(scope, table))

            def place(said: str) -> str | None:
                for key in (name_key(said), name_key(measure_phrase.without_article(said))):
                    found = names.get(key)
                    if found is not None:
                        return found if shown is None or found in shown else None
                return None

            a, b = place(asked.a), place(asked.b)
            if not dm and (a is None or b is None):
                # (both kept places to the DM, one not shown to the table: say so, never what. The
                # DM's view reads every other name, a secret one included, which the table's skips)
                every = await scope.select(ALIASES, " AND status = 'confirmed'")
                to_dm = positions.place_names(
                    entities, every, lambda kind: onto.is_a(kind, "place")
                )

                def dm_place(said: str) -> str | None:
                    for key in (name_key(said), name_key(measure_phrase.without_article(said))):
                        found = to_dm.get(key)
                        if found is not None:  # (as `place`: a name two places share is none)
                            return found
                    return None

                da, db = dm_place(asked.a), dm_place(asked.b)
                if da is not None and db is not None and da != db:
                    return NOT_SHOWN
            if a is None or b is None or a == b:
                return None
            if asked.kind == measure_phrase.LONG:
                return await _trip(scope, a, b, asked, rules)
            if asked.kind == measure_phrase.FAR:
                cur = await scope.conn.execute(
                    "SELECT 1 FROM memory_relations WHERE guild_id = %s AND campaign_id = %s"
                    " AND predicate = 'route_to' AND status = 'confirmed' AND to_session_at IS NULL"
                    " AND length_words IS NOT NULL AND ((subject_id = %s AND object_id = %s)"
                    " OR (subject_id = %s AND object_id = %s)) LIMIT 1",
                    (guild_id, campaign_id, a, b, b, a),
                )
                if await cur.fetchone() is not None:
                    return None  # (a stated length: the usual answer gives it, by its own rules)
                # (#4260 10d: no road between them, but a chain of roads with a length on some leg:
                # the legs. A player's only among the roads and places the party knows, so a
                # route the party doesn't know leaves today's answer as it was: no hint of it)
                known: set[str] | None = None
                if not dm:
                    statements = await gate.load(scope)
                    if (statements.player_characters or table.characters) and shown is not None:
                        full = gate.visible(table, statements)
                        known = shown | {i for (k, i) in full.sureness if k == gate.CONNECTION}
                if dm or known is not None:
                    viewer = gate.Viewer(dm=True) if dm else table
                    route = await _road_route(scope, a, b, viewer, (asked.a, asked.b), known=known)
                    if route is not None and any(leg.road.length_words for leg in route[0].legs):
                        return legs_words.route(route[0], route[2], route[1])
            maps, pins = await maps_and_pins(scope.conn, guild_id, campaign_id, [a, b], dm=dm)
        # (the names as the asker said them, so a misheard name is easy to spot)
        return measure.answer(asked.kind, maps, pins, (a, asked.a), (b, asked.b), dm=dm)

    async def road_off(self, guild_id: int, campaign_id: str, relation_id: str) -> RoadOff | None:
        """This road's stated length against the kept maps (#4670 4c): the disagreement, or None
        when no map with a scale has both places or the length is within the limit (§9). For the
        road card's private line after the DM sets or keeps the length; the DM's maps only."""
        async with self._memory._read(guild_id, campaign_id, snapshot=True) as scope:
            found = await roads_off(
                scope.conn, guild_id, campaign_id, read_only=False, relation_ids=[relation_id]
            )
        return found[0] if found else None

    async def said_off(self, guild_id: int, campaign_id: str, line: str) -> list[SaidOff]:
        """What a line the DM said states that a kept map disagrees with (#4670 4c-2; design §9):
        a distance more than 20% shorter than the map's straight line, or a direction 45° or more
        off on a map whose north was read or set. Names are kept places only, the DM's secret
        other names included (the note is the DM's alone); the DM's maps (`dm=True`). Nothing is
        stored or changed. Empty when the line states nothing in a set shape."""
        from dmbot.maps import disagree, positions, said_phrases
        from dmbot.memory._changes import ALIASES, ENTITIES
        from dmbot.memory.store import _load_ontology

        if not said_phrases.may_state(line):
            return []
        async with self._memory._read(guild_id, campaign_id, snapshot=True) as scope:
            onto = await _load_ontology(scope)
            entities = await scope.select(ENTITIES, " AND status = 'confirmed'")
            aliases = await scope.select(ALIASES, " AND status = 'confirmed'")
            found = positions.place_names(entities, aliases, lambda kind: onto.is_a(kind, "place"))
            names = {key: entry for key, entry in found.items() if entry is not None}
            said = [s for s in said_phrases.read(line, names) if s.kind != said_phrases.TIME]
            if not said:
                return []
            places = sorted({i for s in said for i in (s.a_id, s.b_id)})
            maps, pins = await maps_and_pins(scope.conn, guild_id, campaign_id, places, dm=True)
        called = {str(e["id"]): str(e["name"]) for e in entities}
        out: list[SaidOff] = []
        for s in said:
            off: Distance | Direction | None = (
                disagree.check_distance(maps, pins, s.a_id, s.b_id, s.value)
                if s.kind == said_phrases.FAR
                else disagree.check_direction(maps, pins, s.a_id, s.b_id, s.value)
            )
            if off is not None:
                out.append(SaidOff(s, called[s.a_id], called[s.b_id], off))
        return out

    async def said_trips(
        self, guild_id: int, campaign_id: str, line: str, rules: tuple[str, str] = ("2024", "none")
    ) -> list[SaidTrip]:
        """The unasked travel-time offer (#4260; design §9 "When it is offered"): a travel time the
        DM said between two kept places DMbot can measure, each with the suggestion "how long will
        it take?" would give, on foot. Never a check of the DM's time and never stored: the DM's
        own words are only the cue. Names as `said_off` (the DM's: secret other names too). No
        offer where no time could be worked out (no miles, or the road's own length is a time),
        for a time said riding, by cart or by boat (the estimate is on foot only), or when the DM's
        time is already within the estimate's paces."""
        from dmbot.maps import measure_phrase, positions, said_phrases
        from dmbot.memory._changes import ALIASES, ENTITIES
        from dmbot.memory.store import _load_ontology

        if not said_phrases.may_state_time(line):
            return []
        out: list[SaidTrip] = []
        async with self._memory._read(guild_id, campaign_id, snapshot=True) as scope:
            onto = await _load_ontology(scope)
            entities = await scope.select(ENTITIES, " AND status = 'confirmed'")
            aliases = await scope.select(ALIASES, " AND status = 'confirmed'")
            found = positions.place_names(entities, aliases, lambda kind: onto.is_a(kind, "place"))
            names = {key: entry for key, entry in found.items() if entry is not None}
            said = [
                s
                for s in said_phrases.read(line, names)
                if s.kind == said_phrases.TIME and not said_phrases.NOT_ON_FOOT.search(s.words)
            ]
            called = {str(e["id"]): str(e["name"]) for e in entities}
            for s in said:
                a_name, b_name = called[s.a_id], called[s.b_id]
                asked = measure_phrase.Asked(measure_phrase.LONG, a_name, b_name)
                text = await _trip(
                    scope, s.a_id, s.b_id, asked, rules, offer=True, stated_days=s.value
                )
                if text is not None:
                    out.append(SaidTrip(s, a_name, b_name, text))
        return out

    async def pins_waiting(self, guild_id: int) -> list[tuple[str, Map]]:
        """The maps in this server read closely before pins were read (Supervisor's ruling 1: a
        one-time re-read), oldest first, each with its campaign: DMbot may use them, their close
        read is done, and they have no pins read yet."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                f"SELECT campaign_id, {COLUMNS} FROM campaign_maps WHERE guild_id = %s"
                " AND use_ok IS TRUE AND close_read_at IS NOT NULL AND close_read_tries = 0"
                " AND pins_read_at IS NULL ORDER BY created_at, id",
                (guild_id,),
            )
            found = [(str(r["campaign_id"]), _map(r)) for r in await cur.fetchall()]
        return [(c, m) for c, m in found if may_use_map(m)]

    async def claim_pins(self, guild_id: int, campaign_id: str, map_id: str) -> bool:
        """Mark a map's one-time re-read as taken, in the statement that checks it isn't yet, so
        it is read at most once whatever happens next (a failure isn't tried again)."""
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE campaign_maps SET pins_read_at = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s AND pins_read_at IS NULL RETURNING id",
                (int(self._clock()), guild_id, campaign_id, map_id),
            )
            return await cur.fetchone() is not None

    async def release_pins(self, guild_id: int, campaign_id: str, map_id: str) -> None:
        """A re-read put off (no credits, the day's picture limit, shutting down): its mark is
        given back, so it is taken another day."""
        async with self._db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE campaign_maps SET pins_read_at = NULL WHERE guild_id = %s"
                " AND campaign_id = %s AND id = %s",
                (guild_id, campaign_id, map_id),
            )

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


__all__ = [
    "MB", "Map", "MapError", "MapStore", "RoadOff", "SaidOff", "SaidTrip", "Viewer",
    "maps_and_pins", "may_use_map", "roads_off",
]  # fmt: skip
