"""Positions on a map (#4670, MapBot layers slice 4a; docs/design/mapbot_layers.md §4; Supervisor's
ruling on #4670): pins for a kept map's labelled places, and the map's scale, grid and north, from
its close read (`images.parse_close_map`).

Rules kept here:
- **A pin's target is an exact name or kept other name of a kept place, and nothing else**
  (ruling 2). A label that matches no kept place, or more than one, stays an unmatched pin; when
  the DM keeps that name later, `rematch_in` ties it, through the change log.
- **A read only adds read rows where none exist** (ruling 1): a place that already has a pin on
  the map (the DM's, an import's or an earlier read) keeps it, and a map whose scale, grid or north
  the DM set or an import gave keeps that.
- **No scale is no number** (§4.3): a scale comes only from a drawn scale bar (its written
  distance over its drawn length) or a grid whose cell distance is written; north only from a
  drawn compass, else it stays "assumed" (up).
- **A place entry never has a coordinate** (§4.1): positions are always "on this map".
Nothing here shows anything to anyone: the answers are slice 4b, behind DMBOT_MAP_MEASURE.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from dmbot.images import Read
from dmbot.memory._changes import ALIASES, ENTITIES, MAP_FEATURES, MAPS, Changes
from dmbot.memory.models import CONFIRMED, name_key, new_id

PIN, READ_SOURCE, READ_STATUS = "pin", "read", "read"
LABEL_MAX = 80  # `map_features.label`


@dataclass(frozen=True, slots=True)
class MapScale:
    """What a read gives the map's row: distance per pixel of the full-size picture and its unit,
    or nothing."""

    per_px: float
    unit: str


def scale_from(read: Read, width: int | None, height: int | None) -> MapScale | None:
    """The map's scale from a drawn scale bar (its written distance over its length in pixels),
    else from a grid whose one cell's distance is written. None without the picture's size, or
    with neither: never a guess."""
    if not width or not height:
        return None
    if read.scale is not None:
        x1, y1, x2, y2 = read.scale.ends
        length = math.hypot((x2 - x1) * width, (y2 - y1) * height)
        if length >= 1.0:
            return MapScale(read.scale.distance / length, read.scale.unit)
    if read.grid is not None and read.grid.distance and read.grid.unit:
        cell_px = width / read.grid.across
        if cell_px >= 1.0:
            return MapScale(read.grid.distance / cell_px, read.grid.unit)
    return None


def place_names(
    entities: Sequence[Mapping[str, Any]],
    aliases: Sequence[Mapping[str, Any]],
    is_place: Any,
) -> dict[str, str | None]:
    """Name key → the one kept place it names, or None when it names more than one (then no pin
    is tied: DMbot never picks between two). Kept places only, by their name and their kept other
    names (ruling 2)."""
    places = {
        str(e["id"]): e for e in entities if e["status"] == CONFIRMED and is_place(str(e["type"]))
    }
    out: dict[str, str | None] = {}

    def add(key: str, entity_id: str) -> None:
        if not key:
            return
        if key in out and out[key] != entity_id:
            out[key] = None
        else:
            out[key] = entity_id

    for e in places.values():
        add(name_key(str(e["name"])), str(e["id"]))
    for a in aliases:
        if a["status"] == CONFIRMED and str(a["entity_id"]) in places:
            add(name_key(str(a["text"])), str(a["entity_id"]))
    return out


@dataclass(slots=True)
class Recorded:
    """What one read wrote, as counts (logs hold counts only)."""

    pins: int = 0
    matched: int = 0
    kept: int = 0  # places that already had a pin on this map, left as they were
    scale: bool = False
    north: bool = False
    grid: bool = False
    skipped: list[str] = field(default_factory=list)  # (never logged: the caller counts it)
    # what the map showed outright, written as layer rows (#4259 slice 8a): the store's counts
    # (`map_fact_store.add_read`) and the written rows' layers, for the DM's note
    layers: dict[str, int] = field(default_factory=dict)
    written: dict[str, int] = field(default_factory=dict)
    batch: int | None = None  # (the write's one batch, for the note's Undo; set with the layers)


async def record_in(
    w: Changes, map_id: str, read: Read, names: Mapping[str, str | None]
) -> Recorded:
    """Write one close read's pins and the map's scale, grid and north, in the open write `w`
    (one batch, one Undo). The map must still exist; nothing is written otherwise."""
    done = Recorded()
    row = await w.get(MAPS, map_id)
    if row is None:
        return done
    # (a turned-down pin counts as there: a read never puts back what the DM or a merge took off)
    have = await w.select(MAP_FEATURES, " AND map_id = %s", [map_id])
    pinned = {str(f["place_id"]) for f in have if f["place_id"] is not None}
    labelled = {name_key(str(f["label"])) for f in have if f["label"]}
    loose = {
        name_key(str(f["label"])): f
        for f in have
        if f["place_id"] is None and f["relation_id"] is None and f["fact_id"] is None
        and f["shape"] == PIN and f["status"] != "rejected"
    }  # fmt: skip
    for place in read.places:
        if place.at is None:
            continue  # (no position, no pin: "where" words stay in the note)
        key = name_key(place.name)
        target = names.get(key)
        if target is not None and target not in pinned and key in loose:
            # (an earlier read's unmatched pin with this name: tied now, never doubled)
            await w.update(MAP_FEATURES, str(loose.pop(key)["id"]), {"place_id": target})
            pinned.add(target)
            done.kept += 1
            done.matched += 1
            continue
        if (target is not None and target in pinned) or (target is None and key in labelled):
            done.kept += 1
            continue
        await w.insert(
            MAP_FEATURES,
            {
                "id": new_id(),
                "map_id": map_id,
                "shape": PIN,
                "points": [place.at[0], place.at[1]],
                "place_id": target,
                "relation_id": None,
                "fact_id": None,
                "label": place.name[:LABEL_MAX],
                "status": READ_STATUS,
                "source": READ_SOURCE,
                "created_at": w.now,
            },
        )
        done.pins += 1
        done.matched += target is not None
        if target is not None:
            pinned.add(target)
        labelled.add(key)
    change: dict[str, Any] = {}
    scale = scale_from(read, row["width"], row["height"])
    if scale is not None and row["scale_from"] is None:
        change.update(scale_value=scale.per_px, scale_unit=scale.unit, scale_from=READ_SOURCE)
        done.scale = True
    if read.grid is not None and row["grid_from"] is None and row["width"]:
        # (a cell's distance has no unit of its own: kept only in the map's scale unit)
        unit = change.get("scale_unit", row["scale_unit"])
        change.update(
            grid_kind=read.grid.kind,
            grid_cell_px=row["width"] / read.grid.across,
            grid_cell_distance=read.grid.distance if read.grid.unit == unit else None,
            grid_from=READ_SOURCE,
        )
        done.grid = True
    if read.north is not None and row["north_from"] == "assumed":
        change.update(north_angle=read.north, north_from=READ_SOURCE)
        done.north = True
    if change:
        await w.update(MAPS, map_id, change)
    return done


async def rematch_in(w: Changes, entity_id: str, is_place: Any) -> int:
    """The DM kept a place (or its other name): an unmatched pin whose label is that name, on any
    map, is tied to it now (ruling 2), through the change log. Only when the name points at this
    one kept place, and only where the map has no pin for it yet. How many were tied. Reads only
    this entry's names and the pins with them first; the whole names list only when one fits."""
    entity = await w.get(ENTITIES, entity_id)
    if entity is None or entity["status"] != CONFIRMED or not is_place(str(entity["type"])):
        return 0
    own = await w.select(ALIASES, " AND entity_id = %s AND status = %s", [entity_id, CONFIRMED])
    keys = {name_key(str(entity["name"]))} | {name_key(str(a["text"])) for a in own}
    unmatched = await w.select(
        MAP_FEATURES,
        " AND place_id IS NULL AND relation_id IS NULL AND fact_id IS NULL"
        " AND shape = 'pin' AND status <> 'rejected'",
        [],
    )
    loose = [f for f in unmatched if name_key(str(f["label"])) in keys]
    if not loose:  # (the common case: nothing on a map carries this name)
        return 0
    names = place_names(
        await w.select(ENTITIES, " AND status = %s", [CONFIRMED]),
        await w.select(ALIASES, " AND status = %s", [CONFIRMED]),
        is_place,
    )
    tied = 0
    for f in loose:
        if names.get(name_key(str(f["label"]))) != entity_id:
            continue  # (the name is another place's too: never a pick between two)
        if await w.select(
            MAP_FEATURES, " AND map_id = %s AND place_id = %s", [f["map_id"], entity_id]
        ):
            continue  # (the map has a pin for it already, even a turned-down one)
        await w.update(MAP_FEATURES, str(f["id"]), {"place_id": entity_id})
        tied += 1
    return tied


def _weight(f: Mapping[str, Any]) -> int:
    """Which of two pins for one place on one map stays: the DM's (or a confirmed one) over an
    import's over a read's (§7.1)."""
    return 2 if f["source"] == "dm" or f["status"] == CONFIRMED else int(f["source"] == "import")


async def move_on_merge(w: Changes, keep_id: str, gone_id: str) -> None:
    """Two places said to be one (§4.1): the gone place's pins move to the kept one. On a map where
    both have a pin, the weaker one is taken off (rejected, logged; the DM's own pin always stays,
    the reviewer's point), so one open pin per place per map holds."""
    for f in await w.select(MAP_FEATURES, " AND place_id = %s", [gone_id]):
        theirs = (
            []
            if f["status"] == "rejected"
            else await w.select(
                MAP_FEATURES,
                " AND map_id = %s AND place_id = %s AND status <> 'rejected'",
                [f["map_id"], keep_id],
            )
        )
        if theirs and _weight(theirs[0]) >= _weight(f):
            await w.update(MAP_FEATURES, str(f["id"]), {"status": "rejected"})
            continue
        for other in theirs:  # (the gone place's pin is the DM's: the kept one's read goes)
            await w.update(MAP_FEATURES, str(other["id"]), {"status": "rejected"})
        await w.update(MAP_FEATURES, str(f["id"]), {"place_id": keep_id})
