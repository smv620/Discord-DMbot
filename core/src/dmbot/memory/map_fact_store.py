"""The rows of a place's land, climate and who rules it (#4256, MapBot layers slice 2): the one
reader (`facts_for`, which applies the players' rule, design §8.3) and the writes, each through the
change log so one change is one Undo. `MemoryStore` calls these inside its own read or write; the
rules of a value, the readers' phrases and reading up the tree are in `map_facts` (pure)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from dmbot.memory import gate, place_levels, place_peoples, route_words
from dmbot.memory import map_facts as mf
from dmbot.memory._changes import CULTURES, ENTITIES, MAP_FACTS, RELATIONS, Changes, Scope
from dmbot.memory.models import REJECTED as ENTRY_REJECTED
from dmbot.memory.models import MemoryRuleError, new_id
from dmbot.memory.ontology import Ontology

ONLY_PLACES = "Only a place has land, a climate or a ruler."
ONLY_LAND = "Only land has a land type: this place is water or a road."
ONLY_BIG = "Only a region or bigger has its own climate. Set it on the region this place is in."
NOT_A_GROUP = "Only a group can rule a place."
GONE = "That's gone. Open the card again to see it as it is now."
BAD_WORD = "That isn't one of the choices."
ONLY_ROUTES = (
    "Only a road, a pass or a bridge has a condition. To make this place one, tap 📏 Size and kind"
    " and pick \u201cA road or route\u201d."
)  # (the ux-critic's words on #4258)


# What a World notes 🚫 turned down (`reject_row`): nothing (checked or gone already), the row, or
# a named culture DMbot read with every row of it, at every place (#4257 slice 3b)
GONE_ALREADY, ROW_ONLY, NAME_TOO = 0, 1, 2

# Layers whose values sit side by side (#4257): a town can be strongly dwarven and a little elven,
# and home to mostly halflings and some humans. A different lineage never corrects another.
BESIDE = frozenset({mf.CULTURE, mf.POPULATION})


def _fact(r: Mapping[str, Any], names: Mapping[str, str] | None = None) -> mf.Fact:
    """A row as the card and the answers use it. A named culture's value is its id, as control's
    is the group's, and its words its name (from `names`, the campaign's cultures by id)."""
    culture = r.get("culture_id")
    return mf.Fact(
        id=str(r["id"]),
        place_id=str(r["entity_id"] or ""),  # ('' for a condition on a connection, #4258)
        layer=str(r["layer"]),
        value=str(
            r["faction_id"] if r["layer"] == mf.CONTROL else culture if culture else r["value"]
        ),
        words=str((names or {}).get(str(culture), "") if culture else r["words"]),
        how=str(r["how"]),
        part=str(r["part"]),
        status=str(r["status"]),
        source=str(r["source"]),
        table_knows=bool(r["table_knows"]),
        relation_id=str(r.get("relation_id") or ""),
    )


_OPEN = " AND status <> 'rejected' AND to_session_at IS NULL"


async def facts_for(
    scope: Scope,
    place_ids: Sequence[str],
    viewer: gate.Viewer,
    shown: Collection[str] | None = None,
) -> dict[str, list[mf.Fact]]:
    """THE reader of `memory_map_facts` (the CI test `test_map_fact_readers` holds every other file
    to it): each place's open rows, in layer order. The DM sees them all. A player sees a row only
    when the place and its group are entries that player knows (the gate), the DM has confirmed it,
    and it is marked as known to the table: a value DMbot read from the DM's notes is the DM's
    until the DM confirms it (the ux-critic on #4256; stricter than §8.3, on the safe side).
    `shown`: the entries the caller's own list already shows this viewer (a card's names for a
    player), used instead of asking the gate again; never wider, since the caller filtered it."""
    if not place_ids:
        return {}
    rows = await scope.select(
        MAP_FACTS, " AND entity_id = ANY(%s)" + _OPEN + " ORDER BY created_at, id",
        [list(place_ids)],
    )  # fmt: skip
    named = sorted({str(r["culture_id"]) for r in rows if r.get("culture_id")})
    names = await culture_names(scope, named)
    facts = [_fact(r, names) for r in rows]
    if not viewer.dm:
        if shown is None:
            seen = await gate.visible_to(scope, viewer)
            known = {f.place_id for f in facts if seen.knows(gate.ENTRY, f.place_id)}
            known |= {
                f.value for f in facts if f.layer == mf.CONTROL and seen.knows(gate.ENTRY, f.value)
            }
        else:
            known = set(shown)
        facts = mf.for_players(facts, known)
    out: dict[str, list[mf.Fact]] = {}
    for f in sorted(facts, key=lambda f: mf.LAYERS.index(f.layer)):
        out.setdefault(f.place_id, []).append(f)
    return out


async def culture_names(scope: Scope, ids: Sequence[str]) -> dict[str, str]:
    """The campaign's named cultures by id (#4257 slice 3b): their names, for the card."""
    if not ids:
        return {}
    rows = await scope.select(CULTURES, " AND id = ANY(%s)", [list(ids)])
    return {str(r["id"]): str(r["name"]) for r in rows}


async def cultures(scope: Scope) -> list[tuple[str, str, str]]:
    """The campaign's named cultures the DM hasn't turned down, (id, name, status), kept ones
    first then by name: the DM's picker (#4257 slice 3b)."""
    rows = await scope.select(CULTURES, " AND status <> 'rejected'", [])
    rows.sort(key=lambda r: (r["status"] != mf.CONFIRMED, str(r["name_key"])))
    return [(str(r["id"]), str(r["name"]), str(r["status"])) for r in rows]


async def culture_for(w: Changes, name: str, *, by_dm: bool) -> str | None:
    """The id of the named culture `name`, made when the campaign has none of that name: as the
    DM's (confirmed) when the DM typed it, else as DMbot's read (`read`, for World notes). A name
    the DM turned down gives None to a reader, and comes back only by the DM's own word."""
    name = place_peoples.culture_name(name)[: mf.WORDS_MAX]
    key = place_peoples.culture_key(name)
    if not key:
        raise MemoryRuleError(BAD_WORD)
    rows = await w.select(CULTURES, " AND name_key = %s", [key])
    if rows:
        row = rows[0]
        if by_dm and row["status"] != mf.CONFIRMED:
            await w.update(CULTURES, str(row["id"]), {"status": mf.CONFIRMED, "source": mf.FROM_DM})
        elif row["status"] == mf.REJECTED:
            return None
        return str(row["id"])
    cid = new_id()
    await w.insert(
        CULTURES,
        {
            "id": cid, "name": name, "name_key": key, "lineage": "",
            "status": mf.CONFIRMED if by_dm else mf.READ,
            "source": mf.FROM_DM if by_dm else mf.FROM_TEXT, "created_at": w.now,
        },
    )  # fmt: skip
    return cid


async def _keep_cultures(w: Changes, rows: Sequence[Mapping[str, Any]]) -> None:
    """Keeping a row that names a culture DMbot read keeps the culture too."""
    ids = sorted({str(r["culture_id"]) for r in rows if r.get("culture_id")})
    if not ids:
        return
    waiting = await w.select(CULTURES, " AND id = ANY(%s) AND status = 'read'", [ids])
    await w.update_rows(
        CULTURES, {str(c["id"]): {"status": mf.CONFIRMED, "source": mf.FROM_DM} for c in waiting}
    )


async def _place(w: Changes, onto: Ontology, entity_id: str) -> dict[str, Any]:
    row = await w.get(ENTITIES, entity_id)
    if row is None or row.get("merged_into"):
        raise MemoryRuleError(GONE)
    if not onto.is_a(str(row["type"]), "place"):
        raise MemoryRuleError(ONLY_PLACES)
    return row


async def _check_value(
    w: Changes,
    onto: Ontology,
    place: Mapping[str, Any],
    layer: str,
    value: str,
    how: str,
    words: str = "",
) -> None:
    # (the table's own checks, said in plain words first: "other" carries the text's words, at
    # most WORDS_MAX, and nothing else does)
    if not mf.valid(layer, value, how) or (layer != mf.CONTROL and not value):
        raise MemoryRuleError(BAD_WORD)
    if (value == mf.OTHER) != bool(words.strip()) or len(words) > mf.WORDS_MAX:
        raise MemoryRuleError(BAD_WORD)
    if not mf.allowed_on(layer, form=place.get("form"), level=place.get("place_level")):
        raise MemoryRuleError(ONLY_LAND if layer == mf.TERRAIN else ONLY_BIG)
    if mf.is_named(layer, value):
        culture = await w.get(CULTURES, value)
        if culture is None or culture["status"] == mf.REJECTED:
            raise MemoryRuleError(GONE)
    if layer == mf.CONTROL:
        group = await w.get(ENTITIES, value)
        if (
            group is None
            or group.get("merged_into")
            or not onto.is_a(str(group["type"]), "faction")
        ):
            raise MemoryRuleError(NOT_A_GROUP)


def _row_value(layer: str, value: str) -> dict[str, Any]:
    if layer == mf.CONTROL:
        return {"faction_id": value, "value": "", "culture_id": None}
    if mf.is_named(layer, value):
        return {"faction_id": None, "value": "", "culture_id": value}
    return {"faction_id": None, "value": value, "culture_id": None}


async def set_by_dm(
    w: Changes,
    onto: Ontology,
    place_id: str,
    layer: str,
    value: str | None,
    *,
    how: str = "",
    words: str = "",
) -> None:
    """The DM's word for a whole place in one layer (the edit screen), written confirmed. What it
    replaces is rejected (the DM says what it is: a correction, §7.3):
    - land and climate: every other open value of the layer;
    - who rules it: another group that "rules" it when this one now rules it, and this group's own
      row; a different group that only claims, occupies or protects it stays beside it (§6);
    - culture and who lives there (#4257): only this lineage's own row; another lineage stays
      beside it ("strongly dwarven, a little elven", "mostly halflings, some humans").
    None ("Not sure") clears the whole layer. The same value again only confirms it, with its new
    shade. Whether players see it follows the place's other rows (one 🙈 / 👁️ for the place)."""
    place = await _place(w, onto, place_id)
    if layer == mf.CULTURE and value == mf.OTHER:
        # ("Something else…" on a culture is always a named culture, the DM's: Supervisor, #4257)
        value, words = await culture_for(w, words, by_dm=True), ""
    if value is not None:
        await _check_value(w, onto, place, layer, value, how, words)
    if value is not None and mf.is_named(layer, value):
        await _keep_cultures(w, [{"culture_id": value}])  # (the DM picked it: it is theirs)
    if layer == mf.CONDITION:  # (one value at a time, kept as history: its own rule, #4258)
        await set_condition(w, onto, value, words=words, place_id=place_id)
        return
    every = await w.select(MAP_FACTS, " AND entity_id = %s" + _OPEN, [place_id])
    known = any(bool(r["table_knows"]) for r in every)
    # (a part's row, "the dwarven quarter", is read only: the DM's set acts on the whole place, but
    # "Not sure" for culture or who lives there clears every row of the layer, parts too; #4257)
    now = [
        r
        for r in every
        if r["layer"] == layer and (r["part"] == "" or (value is None and layer in BESIDE))
    ]

    def replaced(f: mf.Fact) -> bool:
        if value is None or layer in (mf.TERRAIN, mf.CLIMATE):
            return True
        if layer in BESIDE:
            return f.value == value
        return f.value == value or (how == "rules" and f.how == "rules")

    keeps = value is not None and any(_fact(r).value == value for r in now)
    for r in now:
        f = _fact(r)
        if keeps and f.value == value:
            change = {
                "status": mf.CONFIRMED, "source": mf.FROM_DM, "how": how,
                "words": words if value == mf.OTHER else "", "table_knows": known,
            }  # fmt: skip
            if any(r[k] != v for k, v in change.items()):
                await w.update(MAP_FACTS, f.id, change)
        elif replaced(f):
            await w.update(MAP_FACTS, f.id, {"status": mf.REJECTED})
    if value is None or keeps:
        return
    await w.insert(
        MAP_FACTS,
        {
            "id": new_id(), "entity_id": place_id, "layer": layer, **_row_value(layer, value),
            "words": words if value == mf.OTHER else "", "how": how, "part": "",
            "from_session_at": None, "to_session_at": None, "from_game_time": None,
            "to_game_time": None, "status": mf.CONFIRMED, "source": mf.FROM_DM,
            "also_text": False, "table_knows": known, "created_at": w.now,
        },
    )  # fmt: skip


async def prune(w: Changes, onto: Ontology, place_id: str) -> int:
    """After a place's size, form or merge changes: its land or climate that the place can no
    longer hold (land on what is now water, a climate on what is now a town) is rejected, in the
    same change, so Undo of that change gives it back. How many."""
    place = await w.get(ENTITIES, place_id)
    if place is None:
        return 0
    rows = await w.select(
        MAP_FACTS, " AND entity_id = %s AND layer <> 'control'" + _OPEN, [place_id]
    )
    gone = [
        str(r["id"])
        for r in rows
        if not mf.allowed_on(
            str(r["layer"]), form=place.get("form"), level=place.get("place_level")
        )
    ]
    await w.update_rows(MAP_FACTS, {i: {"status": mf.REJECTED} for i in gone})
    return len(gone)


async def show_to_table(w: Changes, onto: Ontology, place_id: str, shown: bool) -> int:
    """The DM's 🙈 / 👁️ for a place's land, climate and ruler, all at once: how many rows changed."""
    await _place(w, onto, place_id)
    # (a condition's closed rows too, so what players are told it was before matches what they are
    # told it is now, #4258)
    rows = await w.select(
        MAP_FACTS,
        " AND entity_id = %s AND status <> 'rejected'"
        " AND (to_session_at IS NULL OR layer = 'condition')",
        [place_id],
    )
    changed = {
        str(r["id"]): {"table_knows": shown} for r in rows if bool(r["table_knows"]) != shown
    }
    await w.update_rows(MAP_FACTS, changed)
    return len(changed)


async def keep_read(
    w: Changes, onto: Ontology, place_id: str, layers: Collection[str] | None = None
) -> int:
    """The DM's ✅ Keep what DMbot read: every row DMbot read for this place, in `layers` (all when
    None), becomes the DM's word, keeping whether players see it. How many. Each screen keeps only
    the layers it shows: a confirmed row the table knows reaches players, so ✅ never keeps what
    the DM didn't see (#4257)."""
    await _place(w, onto, place_id)
    rows = [
        r
        for r in await w.select(
            MAP_FACTS, " AND entity_id = %s AND status = 'read'" + _OPEN, [place_id]
        )
        if layers is None or r["layer"] in layers
    ]
    await _keep_cultures(w, rows)
    await w.update_rows(
        MAP_FACTS,
        {str(r["id"]): {"status": mf.CONFIRMED, "source": mf.FROM_DM} for r in rows},
    )
    return len(rows)


class Read(NamedTuple):
    """One fact a text reader found about a place (`map_facts.read_facts`), the faction already
    found by its id for control."""

    place_id: str
    layer: str
    value: str
    how: str = ""
    session_at: int | None = None  # the session it was read from: said at the table
    part: str = ""  # only part of the place: "the dwarven quarter" (#4257)
    culture: str = ""  # a named culture's name, as the text wrote it; `value` then '' (3b)
    relation_id: str = ""  # a condition of a known road between two places (#4258); no place
    changed: bool = False  # said with words of change ("reopened", #4258)


async def add_read(
    w: Changes,
    onto: Ontology,
    found: Iterable[Read],
    *,
    source: str = mf.FROM_TEXT,
    map_id: str | None = None,
    shown_at: int | None = None,
    layers_written: Counter[str] | None = None,
) -> Counter[str]:
    """What a text reader found, written as `read` for the DM to check (§7.2; never confirmed).
    Each follows §7.3 against an open row of the same place, layer and part (`map_facts.meet`):
    the same value again only marks it as said in the text too; a different one corrects, closes
    or is only counted for World notes (#4285). Never an alert or a question. Counts only:
    `written`, `same`, `corrected`, `closed`, `noted`, `not_allowed`.

    `source` FROM_MAP (#4259, MapBot slice 8a): what a map shows outright, from `map_id`, ranked
    above the text (§7.1). The same value DMbot read from the text becomes the map's, said by the
    text too (one row, never two); a row starts at `shown_at`, the story time the map was shown
    (§7.4); a map never ends anything by leaving it out (only what it shows is passed)."""
    by_map = source == mf.FROM_MAP
    counts: Counter[str] = Counter()
    for f in found:
        try:
            if f.relation_id:  # (a road between two places: its condition only, #4258)
                await _known_road(w, f.relation_id)
                if f.layer != mf.CONDITION or not mf.valid(f.layer, f.value):
                    raise MemoryRuleError(BAD_WORD)
                place: dict[str, Any] = {"form": place_levels.ROUTE, "place_level": None}
            else:
                place = await _place(w, onto, f.place_id)
            if str(place.get("status")) == ENTRY_REJECTED:
                raise MemoryRuleError(GONE)  # (a waiting name may hold them: they are DM-only)
            if f.culture and f.layer == mf.CULTURE:
                if not mf.valid(f.layer, mf.OTHER, f.how) or not mf.allowed_on(
                    f.layer, form=place.get("form"), level=place.get("place_level")
                ):  # (checked before a new name is made, so none is left with no row)
                    raise MemoryRuleError(BAD_WORD)
                culture = await culture_for(w, f.culture, by_dm=False)
                if culture is None:  # (a name the DM turned down: never read again)
                    counts["rejected_before"] += 1
                    continue
                f = f._replace(value=culture)
            await _check_value(w, onto, place, f.layer, f.value, f.how)
        except MemoryRuleError:
            counts["not_allowed"] += 1
            continue
        target, at = _target(f.place_id, f.relation_id)
        rows = await w.select(
            MAP_FACTS, target + " AND layer = %s AND lower(part) = lower(%s)",
            [at, f.layer, f.part],
        )  # fmt: skip
        if any(
            r["status"] == mf.REJECTED and _fact(r).value == f.value for r in rows
        ):  # (the DM said no to it, or something surer did: it is never read again)
            counts["rejected_before"] += 1
            continue
        now = [_fact(r) for r in rows if r["status"] != mf.REJECTED and r["to_session_at"] is None]
        same = [x for x in now if x.value == f.value]
        if same and by_map:
            counts["same"] += 1
            for x in same:  # (the text said it first: the map's now, the text's too, §7.1)
                if x.source == mf.FROM_TEXT and x.status != mf.CONFIRMED:
                    await w.update(
                        MAP_FACTS,
                        x.id,
                        {"source": mf.FROM_MAP, "map_id": map_id, "also_text": True},
                    )
            continue
        if same:
            counts["same"] += 1
            for x in same:
                change: dict[str, Any] = {"also_text": True}
                if f.session_at is not None and not x.table_knows and x.status != mf.CONFIRMED:
                    change["table_knows"] = True  # (said at the table now)
                await w.update(MAP_FACTS, x.id, change)
            continue
        if f.layer == mf.CONTROL:
            # (a second group that rules it is a World note, never a correction: §6. Claims,
            # occupies and protects sit beside a ruler, so they are simply added.)
            if f.how == "rules" and any(x.how == "rules" for x in now):
                counts["noted"] += 1
                continue
            others: list[mf.Fact] = []
        elif f.layer in BESIDE:  # (another lineage sits beside it: never a correction, #4257)
            others = []
        else:
            others = now
        if f.changed and f.layer in mf.HISTORY and not others:
            counts["change_alone"] += 1  # ("reopened" with nothing it ends says nothing, #4258)
            continue
        outcome = mf.KEEP
        says_change = f.changed and f.layer in mf.HISTORY  # (only a history layer's words count)
        for x in others:
            outcome = mf.meet(f.layer, x.source, x.status, source, changed=says_change)
            if outcome == mf.NOTE:
                break
        if outcome == mf.NOTE:
            counts["noted"] += 1
            continue
        since = shown_at if by_map else f.session_at
        if outcome == mf.CLOSE:  # (the story changed it: the old row is history from now)
            ended = [r for r in rows if str(r["id"]) in {x.id for x in others}]
            when = f.session_at if f.session_at is not None else await story_now(w)
            since = _not_before(when, ended)
            await _close(w, ended, since)
            counts["closed"] += len(others)
        else:
            for x in others:
                await w.update(MAP_FACTS, x.id, {"status": mf.REJECTED})
                counts["corrected"] += 1
        await w.insert(
            MAP_FACTS,
            {
                "id": new_id(), "entity_id": f.place_id or None,
                "relation_id": f.relation_id or None, "layer": f.layer,
                **_row_value(f.layer, f.value), "words": "", "how": f.how,
                "part": f.part[: mf.WORDS_MAX],
                "from_session_at": since, "to_session_at": None,
                "from_game_time": None, "to_game_time": None, "status": mf.READ,
                "source": source, "also_text": not by_map, "map_id": map_id,
                "table_knows": f.session_at is not None and not by_map, "created_at": w.now,
            },
        )  # fmt: skip
        counts["written"] += 1
        if layers_written is not None:  # (new rows only, by layer: a map's note counts these)
            layers_written[f.layer] += 1
    return counts


async def move_on_merge(
    w: Changes, onto: Ontology, keep_type: str, keep_id: str, gone_id: str
) -> None:
    """A merge moves the gone entry's rows to the kept one: a place's land, climate and ruler, and
    a group's rule. What the kept entry can't hold goes as a kind change takes it (a place merged
    into a person, a group into a person: deleted, logged, so Undo gives it back), and land or a
    climate the kept place's size or form can't carry is rejected (`prune`). Two that would land on
    one open key keep the higher source, the DM's word first; the other is rejected first (so the
    one-open-row rule never trips). One read for both."""
    both = [keep_id, gone_id]
    rows = await w.select(
        MAP_FACTS, " AND (entity_id = ANY(%s) OR faction_id = ANY(%s))", [both, both]
    )
    if not any(gone_id in (r["entity_id"], r["faction_id"]) for r in rows):
        return
    is_place, is_group = onto.is_a(keep_type, "place"), onto.is_a(keep_type, "faction")
    dropped = {
        str(r["id"])
        for r in rows
        if (r["entity_id"] == gone_id and not is_place)
        or (r["faction_id"] == gone_id and not is_group)
    }
    for rid in sorted(dropped):
        await w.delete(MAP_FACTS, rid)
    rows = [r for r in rows if str(r["id"]) not in dropped]

    def moved(f: mf.Fact) -> tuple[str, ...]:
        place = keep_id if f.place_id == gone_id else f.place_id
        value = keep_id if f.layer == mf.CONTROL and f.value == gone_id else f.value
        return (place, f.layer, value, f.part.lower())  # (as the index and the backup compare)

    live = [_fact(r) for r in rows if r["status"] != mf.REJECTED and r["to_session_at"] is None]
    best: set[tuple[str, ...]] = set()
    for f in sorted(live, key=lambda f: (f.status != mf.CONFIRMED, -mf.RANK[f.source], f.id)):
        if moved(f) in best:
            await w.update(MAP_FACTS, f.id, {"status": mf.REJECTED})
        else:
            best.add(moved(f))
    for r in rows:
        change = {c: keep_id for c in ("entity_id", "faction_id") if r[c] == gone_id}
        if change:
            await w.update(MAP_FACTS, str(r["id"]), change)
    if is_place:
        await prune(w, onto, keep_id)


async def drop_on_kind_change(
    w: Changes, ids: Sequence[str], to_place: bool, to_group: bool
) -> None:
    """An entry that stops being a place loses its land, climate and ruler; one that stops being a
    group stops ruling. Deleted, logged, so Undo of the kind change gives them back. One read, and
    none when the new kind is both (it never is) or the kind keeps them."""
    if to_place and to_group:
        return
    rows = await w.select(
        MAP_FACTS, " AND (entity_id = ANY(%s) OR faction_id = ANY(%s))", [list(ids), list(ids)]
    )
    for r in rows:
        if (not to_place and r["entity_id"] in ids) or (not to_group and r["faction_id"] in ids):
            await w.delete(MAP_FACTS, str(r["id"]))


async def ties_place(w: Changes, place_id: str) -> bool:
    """Whether any row here, open or closed, the DM's or a World note, says something about this
    place: then a place DMbot made for its setting guess is kept when the guess goes (#4495)."""
    return bool(
        await w.select(MAP_FACTS, " AND status <> 'rejected' AND entity_id = %s", [place_id])
    )


async def dm_read_notes(scope: Scope, viewer: gate.Viewer) -> list[tuple[mf.Fact, int | None, int]]:
    """Every open row DMbot read and the DM hasn't checked, for the DM's World notes (#4285):
    (the fact, the session it was read from, when DMbot read it), oldest first. The second reader
    of the table, the DM's only: it refuses anyone else, so `facts_for` stays the one read a player
    is ever given (`test_map_fact_readers` names both)."""
    if not viewer.dm:
        raise MemoryRuleError("Only the DM can see that.")
    rows = await scope.select(
        MAP_FACTS, " AND status = 'read'" + _OPEN + " ORDER BY created_at, id", []
    )
    names = await culture_names(
        scope, sorted({str(r["culture_id"]) for r in rows if r.get("culture_id")})
    )
    return [(_fact(r, names), r["from_session_at"], int(r["created_at"])) for r in rows]


async def keep_rows(w: Changes, ids: Collection[str]) -> int:
    """The DM's ✅ on World notes lines (#4285): each open row DMbot read becomes the DM's word,
    keeping whether players see it, as `keep_read` does for one place. A row already checked or
    gone is left (a second press). How many."""
    if not ids:
        return 0
    rows = await w.select(MAP_FACTS, " AND id = ANY(%s) AND status = 'read'" + _OPEN, [sorted(ids)])
    await _keep_cultures(w, rows)
    await w.update_rows(
        MAP_FACTS,
        {str(r["id"]): {"status": mf.CONFIRMED, "source": mf.FROM_DM} for r in rows},
    )
    return len(rows)


async def reject_row(w: Changes, fact_id: str) -> int:
    """The DM's 🚫 Not right on a World notes line (#4285): the row is turned down, so the same
    reading is never read in again (`add_read` refuses a value the DM rejected). A named culture
    DMbot read and the DM never kept goes too, with all its rows (#4257 slice 3b). GONE_ALREADY,
    ROW_ONLY or NAME_TOO."""
    rows = await w.select(MAP_FACTS, " AND id = %s AND status = 'read'" + _OPEN, [fact_id])
    if not rows:
        return GONE_ALREADY
    await w.update(MAP_FACTS, fact_id, {"status": mf.REJECTED})
    culture = rows[0].get("culture_id")
    if culture:  # (a name DMbot read and the DM never kept: turned down with all its rows)
        found = await w.select(CULTURES, " AND id = %s AND status = 'read'", [culture])
        if found:
            await w.update(CULTURES, str(culture), {"status": mf.REJECTED})
            others = await w.select(MAP_FACTS, " AND culture_id = %s" + _OPEN, [culture])
            await w.update_rows(MAP_FACTS, {str(r["id"]): {"status": mf.REJECTED} for r in others})
            return NAME_TOO
    return ROW_ONLY


# ---- A route's condition (#4258, MapBot layers slice 4a; Supervisor's ruling on #4258) ----------
# A condition is about a road, a pass or a bridge: a route place, or a known `route_to` connection
# between two places (the migration's trigger refuses a travelled or planned leg). It holds one
# value at a time, and it is the first layer whose change in the story is kept as history: the old
# row is closed at the story's time (`to_session_at`) and the new one opens from it, so the card
# can say "closed since session 5 (open before)". Rejecting stays for "that was wrong".

ONLY_KNOWN_ROAD = "A condition goes on a road, not on a trip the party took or planned."


class Condition(NamedTuple):
    """A route's open condition, when it began in the story (None: since before it) and the one it
    ended, if any (the newest closed row the viewer may see)."""

    now: mf.Fact
    since_at: int | None
    before: mf.Fact | None


def _target(place_id: str, relation_id: str) -> tuple[str, str]:
    """The rows of one place or one connection: (the WHERE part, its value)."""
    if relation_id:
        return " AND relation_id = %s", relation_id
    return " AND entity_id = %s", place_id


async def _known_road(w: Changes, relation_id: str) -> dict[str, Any]:
    row = await w.get(RELATIONS, relation_id)
    if row is None or row["status"] == "rejected" or row.get("to_session_at") is not None:
        raise MemoryRuleError(GONE)  # (a road taken away or ended: a stale screen)
    if row["predicate"] != route_words.ROUTE_TO or row.get("route_state") != route_words.KNOWN:
        raise MemoryRuleError(ONLY_KNOWN_ROAD)
    return row


async def story_now(w: Changes) -> int:
    """The story's time for a change said now: the campaign's latest session's start, or, before
    any session, the campaign's own start (Supervisor's ruling on #4258). The card names a session
    only when there is one; with none it says only what it was before (the ux-critic: "since the
    start" read as if it had always been so)."""
    cur = await w.conn.execute(
        "SELECT COALESCE((SELECT max(started_at) FROM transcript_sessions"
        " WHERE guild_id = %(g)s AND campaign_id = %(c)s),"
        " (SELECT created_at FROM campaigns WHERE guild_id = %(g)s AND id = %(c)s)) AS at",
        {"g": w.guild_id, "c": w.campaign_id},
    )
    row = await cur.fetchone()
    return int(row["at"]) if row is not None and row["at"] is not None else int(w.now)


def _not_before(at: int, rows: Iterable[Mapping[str, Any]]) -> int:
    """A change's story time, never before a row it ends began (a session deleted since, or one
    imported out of order), so the new row never starts before the old one."""
    return max([at, *(int(r["from_session_at"]) for r in rows if r["from_session_at"] is not None)])


async def _close(w: Changes, rows: Iterable[Mapping[str, Any]], at: int) -> None:
    """Close open rows as history at `at` (layer-agnostic: any layer in `map_facts.HISTORY`). Never
    before a row began, so a session imported out of order can't break the time order."""
    for r in rows:
        start = r["from_session_at"]
        await w.update(
            MAP_FACTS, str(r["id"]), {"to_session_at": at if start is None else max(at, start)}
        )


async def set_condition(
    w: Changes,
    onto: Ontology,
    value: str | None,
    *,
    words: str = "",
    place_id: str = "",
    relation_id: str = "",
) -> None:
    """The DM's word for a route's condition (the 🚧 menu): one change, so one Undo.
    - the same value again only confirms it;
    - a different value closes the condition the DM kept as history from the story's time and
      opens the new one from then; one DMbot read and the DM never kept is rejected instead (a guess
      the DM corrects is no part of the story);
    - None ("Not sure") rejects the open condition: that was wrong.
    Whether players see it follows the route's other rows."""
    if bool(place_id) == bool(relation_id):
        raise MemoryRuleError(GONE)
    if relation_id:
        await _known_road(w, relation_id)
    else:
        place = await _place(w, onto, place_id)
        if not mf.allowed_on(mf.CONDITION, form=place.get("form"), level=place.get("place_level")):
            raise MemoryRuleError(ONLY_ROUTES)
    if value is not None and (
        not mf.valid(mf.CONDITION, value)
        or (value == mf.OTHER) != bool(words.strip())
        or len(words) > mf.WORDS_MAX
    ):
        raise MemoryRuleError(BAD_WORD)
    target, at = _target(place_id, relation_id)
    every = await w.select(MAP_FACTS, target + _OPEN, [at])
    known = any(bool(r["table_knows"]) for r in every)
    now = [r for r in every if r["layer"] == mf.CONDITION]
    if value is None:
        await w.update_rows(MAP_FACTS, {str(r["id"]): {"status": mf.REJECTED} for r in now})
        return
    words = words.strip() if value == mf.OTHER else ""
    same = [r for r in now if r["value"] == value and (value != mf.OTHER or r["words"] == words)]
    for r in same:
        change = {"status": mf.CONFIRMED, "source": mf.FROM_DM, "table_knows": known}
        if any(r[k] != v for k, v in change.items()):
            await w.update(MAP_FACTS, str(r["id"]), change)
    others = [r for r in now if r not in same]
    kept = [r for r in others if r["status"] == mf.CONFIRMED]
    await w.update_rows(
        MAP_FACTS, {str(r["id"]): {"status": mf.REJECTED} for r in others if r not in kept}
    )
    if kept:
        since: int | None = _not_before(await story_now(w), kept)
    else:  # (a first condition starts before the story; after a "Not sure", where history ends)
        ended = await w.select(
            MAP_FACTS,
            target + " AND layer = 'condition' AND status <> 'rejected'"
            " AND to_session_at IS NOT NULL",
            [at],
        )
        since = max((int(r["to_session_at"]) for r in ended), default=None)
    if since is not None:
        await _close(w, kept, since)
    if same:
        return
    await w.insert(
        MAP_FACTS,
        {
            "id": new_id(), "entity_id": place_id or None, "relation_id": relation_id or None,
            "layer": mf.CONDITION, "value": value, "faction_id": None, "culture_id": None,
            "words": words, "how": "", "part": "", "from_session_at": since,
            "to_session_at": None, "from_game_time": None, "to_game_time": None,
            "status": mf.CONFIRMED, "source": mf.FROM_DM, "also_text": False,
            "table_knows": known, "created_at": w.now,
        },
    )  # fmt: skip


async def conditions_for(
    scope: Scope,
    viewer: gate.Viewer,
    *,
    place_ids: Sequence[str] = (),
    relation_ids: Sequence[str] = (),
    shown: Collection[str] | None = None,
) -> dict[str, Condition]:
    """Each route's open condition with what it ended, by place or connection id, as `viewer` may
    see it: the players' rule of `map_facts.for_players` for both rows (a closed row a player may
    not see is never told as "before"). `shown`: the entries and connections the caller's own
    list shows that player. The third reader of the table, named in `test_map_fact_readers`."""
    if not place_ids and not relation_ids:
        return {}
    rows = await scope.select(
        MAP_FACTS,
        " AND layer = 'condition' AND status <> 'rejected'"
        " AND (entity_id = ANY(%s) OR relation_id = ANY(%s)) ORDER BY created_at, id",
        [list(place_ids), list(relation_ids)],
    )
    facts = [(_fact(r), r) for r in rows]
    if relation_ids:  # (a road taken away, or ended, keeps its rows for Undo but shows nothing)
        # A player is told only of a confirmed road: one DMbot read and the DM hasn't kept is
        # secret and unproved (test_relation_readers), whatever the gate says of its knowers.
        live = {
            str(r["id"])
            for r in await scope.select(
                RELATIONS,
                " AND id = ANY(%s) AND to_session_at IS NULL"
                + (" AND status <> 'rejected'" if viewer.dm else " AND status = 'confirmed'"),
                [list(relation_ids)],
            )
        }
        facts = [(f, r) for f, r in facts if not f.relation_id or f.relation_id in live]
    if not viewer.dm:
        if shown is None:
            seen = await gate.visible_to(scope, viewer)
            known = {f.place_id for f, _ in facts if seen.knows(gate.ENTRY, f.place_id)} | {
                f.relation_id for f, _ in facts if seen.knows(gate.CONNECTION, f.relation_id)
            }
        else:
            known = set(shown)
        allowed = {f.id for f in mf.for_players([f for f, _ in facts], known)}
        facts = [(f, r) for f, r in facts if f.id in allowed]
    out: dict[str, Condition] = {}
    for f, r in facts:
        if r["to_session_at"] is None:
            out[f.relation_id or f.place_id] = Condition(f, r["from_session_at"], None)
    for f, r in sorted(facts, key=lambda p: p[1]["to_session_at"] or 0):
        held = out.get(f.relation_id or f.place_id)
        if (
            r["to_session_at"] is not None
            and held is not None
            and (f.value, f.words) != (held.now.value, held.now.words)
        ):
            out[f.relation_id or f.place_id] = held._replace(before=f)
    return out


async def keep_route(w: Changes, relation_id: str) -> int:
    """The DM's ✅ on a road's condition DMbot read (the connection's edit page). How many."""
    rows = await w.select(
        MAP_FACTS, " AND relation_id = %s AND status = 'read'" + _OPEN, [relation_id]
    )
    await w.update_rows(
        MAP_FACTS, {str(r["id"]): {"status": mf.CONFIRMED, "source": mf.FROM_DM} for r in rows}
    )
    return len(rows)


async def show_route(w: Changes, relation_id: str, shown: bool) -> int:
    """The DM's 🙈 / 👁️ for a road's condition: its open and closed rows, so what players are
    told before matches what they are told now. How many changed."""
    rows = await w.select(
        MAP_FACTS, " AND relation_id = %s AND status <> 'rejected'", [relation_id]
    )
    changed = {
        str(r["id"]): {"table_knows": shown} for r in rows if bool(r["table_knows"]) != shown
    }
    await w.update_rows(MAP_FACTS, changed)
    return len(changed)


async def move_road(w: Changes, old_id: str, new_id: str) -> int:
    """A road written anew (new words, a turn round, #4154) or folded into its twin by a merge:
    its condition, history too, follows it to `new_id`, through the log, so Undo puts it back. When
    the new road has its own open condition already, that one stands and the old open one is
    rejected (the one-open-row rule never trips). Nothing moves to a connection that isn't a known
    road (new words that make it no road): the rows stay with the old one, and Undo of the change
    brings them back with it. How many moved."""
    rows = await w.select(MAP_FACTS, " AND relation_id = %s", [old_id])
    if not rows or old_id == new_id:
        return 0
    target = await w.get(RELATIONS, new_id)
    if (
        target is None
        or target["predicate"] != route_words.ROUTE_TO
        or target.get("route_state") != route_words.KNOWN
    ):
        return 0
    theirs = await w.select(MAP_FACTS, " AND relation_id = %s" + _OPEN, [new_id])
    for r in rows:
        if theirs and r["status"] != mf.REJECTED and r["to_session_at"] is None:
            await w.update(MAP_FACTS, str(r["id"]), {"status": mf.REJECTED})
        await w.update(MAP_FACTS, str(r["id"]), {"relation_id": new_id})
    return len(rows)
