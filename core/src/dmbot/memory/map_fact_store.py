"""The rows of a place's land, climate and who rules it (#4256, MapBot layers slice 2): the one
reader (`facts_for`, which applies the players' rule, design §8.3) and the writes, each through the
change log so one change is one Undo. `MemoryStore` calls these inside its own read or write; the
rules of a value, the readers' phrases and reading up the tree are in `map_facts` (pure)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from dmbot.memory import gate
from dmbot.memory import map_facts as mf
from dmbot.memory._changes import ENTITIES, MAP_FACTS, Changes, Scope
from dmbot.memory.models import REJECTED as ENTRY_REJECTED
from dmbot.memory.models import MemoryRuleError, new_id
from dmbot.memory.ontology import Ontology

ONLY_PLACES = "Only a place has land, a climate or a ruler."
ONLY_LAND = "Only land has a land type: this place is water or a road."
ONLY_BIG = "Only a region or bigger has its own climate. Set it on the region this place is in."
NOT_A_GROUP = "Only a group can rule a place."
GONE = "That's gone. Open the card again to see it as it is now."
BAD_WORD = "That isn't one of the choices."


def _fact(r: Mapping[str, Any]) -> mf.Fact:
    return mf.Fact(
        id=str(r["id"]),
        place_id=str(r["entity_id"]),
        layer=str(r["layer"]),
        value=str(r["faction_id"] if r["layer"] == mf.CONTROL else r["value"]),
        words=str(r["words"]),
        how=str(r["how"]),
        part=str(r["part"]),
        status=str(r["status"]),
        source=str(r["source"]),
        table_knows=bool(r["table_knows"]),
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
    facts = [_fact(r) for r in rows]
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
        return {"faction_id": value, "value": ""}
    return {"faction_id": None, "value": value}


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
      row; a different group that only claims, occupies or protects it stays beside it (§6).
    None ("Not sure") clears the whole layer. The same value again only confirms it, with its new
    shade. Whether players see it follows the place's other rows (one 🙈 / 👁️ for the place)."""
    place = await _place(w, onto, place_id)
    if value is not None:
        await _check_value(w, onto, place, layer, value, how, words)
    every = await w.select(MAP_FACTS, " AND entity_id = %s" + _OPEN, [place_id])
    known = any(bool(r["table_knows"]) for r in every)
    now = [r for r in every if r["layer"] == layer and r["part"] == ""]

    def replaced(f: mf.Fact) -> bool:
        if value is None or layer != mf.CONTROL:
            return True
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
    rows = await w.select(MAP_FACTS, " AND entity_id = %s" + _OPEN, [place_id])
    changed = {
        str(r["id"]): {"table_knows": shown} for r in rows if bool(r["table_knows"]) != shown
    }
    await w.update_rows(MAP_FACTS, changed)
    return len(changed)


async def keep_read(w: Changes, onto: Ontology, place_id: str) -> int:
    """The DM's ✅ Keep what DMbot read: every row DMbot read for this place becomes the DM's word,
    keeping whether players see it. How many."""
    await _place(w, onto, place_id)
    rows = await w.select(MAP_FACTS, " AND entity_id = %s AND status = 'read'" + _OPEN, [place_id])
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


async def add_read(w: Changes, onto: Ontology, found: Iterable[Read]) -> Counter[str]:
    """What a text reader found, written as `read` for the DM to check (§7.2; never confirmed).
    Each follows §7.3 against an open row of the same place, layer and part (`map_facts.meet`):
    the same value again only marks it as said in the text too; a different one corrects, closes
    or is only counted for World notes (#4285). Never an alert or a question. Counts only:
    `written`, `same`, `corrected`, `closed`, `noted`, `not_allowed`."""
    counts: Counter[str] = Counter()
    for f in found:
        try:
            place = await _place(w, onto, f.place_id)
            if str(place["status"]) == ENTRY_REJECTED:
                raise MemoryRuleError(GONE)  # (a waiting name may hold them: they are DM-only)
            await _check_value(w, onto, place, f.layer, f.value, f.how)
        except MemoryRuleError:
            counts["not_allowed"] += 1
            continue
        rows = await w.select(
            MAP_FACTS, " AND entity_id = %s AND layer = %s AND part = ''", [f.place_id, f.layer]
        )
        if any(
            r["status"] == mf.REJECTED and _fact(r).value == f.value for r in rows
        ):  # (the DM said no to it, or something surer did: it is never read again)
            counts["rejected_before"] += 1
            continue
        now = [_fact(r) for r in rows if r["status"] != mf.REJECTED and r["to_session_at"] is None]
        same = [x for x in now if x.value == f.value]
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
        else:
            others = now
        outcome = mf.KEEP
        for x in others:
            outcome = mf.meet(f.layer, x.source, x.status, mf.FROM_TEXT, changed=False)
            if outcome == mf.NOTE:
                break
        if outcome == mf.NOTE:
            counts["noted"] += 1
            continue
        for x in others:
            await w.update(MAP_FACTS, x.id, {"status": mf.REJECTED})
            counts["corrected"] += 1
        await w.insert(
            MAP_FACTS,
            {
                "id": new_id(), "entity_id": f.place_id, "layer": f.layer,
                **_row_value(f.layer, f.value), "words": "", "how": f.how, "part": "",
                "from_session_at": f.session_at, "to_session_at": None,
                "from_game_time": None, "to_game_time": None, "status": mf.READ,
                "source": mf.FROM_TEXT, "also_text": True,
                "table_knows": f.session_at is not None, "created_at": w.now,
            },
        )  # fmt: skip
        counts["written"] += 1
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
