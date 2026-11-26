"""World notes (#4285, slice 1; Supervisor's ruling on #4285, 2026-11-14): what DMbot guessed about
the world and the DM hasn't checked yet, gathered in one list for the DM. Today the guesses wait on
each place's card ("DMbot read this") and each character's languages; the list is the same rows,
never a copy, so a ✅ here is the card's ✅ too.

- **Places:** a place's land, climate and who rules it, as DMbot read them (their rows still
  `read`, open), through `map_fact_store`, the table's one reader.
- **Languages:** a language DMbot read for a character (`memory_story_languages`, `given_by =
  'read'`), for a character with no sheet (a sheet's languages are the sheet's, #4229). A language
  that is a question in ❓ Questions for you (waiting, or answered there) stays there and is never a
  note (Supervisor's ruling on #4285), and DMbot's own read never hides the question
  (`trait_offers._STILL_OPEN`), so nothing is asked twice.

- **Roads** (#4670 4c, Supervisor's ruling; docs/design/mapbot_layers.md §9): a road's length
  DMbot read, from lore or a session, that is far shorter than a kept map's straight line. Worked
  out when the list is read (`maps.store.roads_off`), never stored; only while the map answers
  are on (`with_roads`). ✅ makes the length the DM's own, as the road's ✅ Length is right does,
  and the note goes: a length the DM kept is never flagged again (ruling 3). Nothing else changes
  it. Only a road the DM kept: a waiting one is asked about on its own already, never twice.

Each line's id says which table it is (`place:`, `language:` or `road:`), so one action word takes
them all.
The DM's own list: never a player's, never on the DM screen (the caller checks who asks). Ids and
words of the list only, never a line of lore or transcript; logs get counts."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from dmbot.memory import biases, gate, language_rows, map_fact_store, place_levels
from dmbot.memory import map_facts as mf
from dmbot.memory._changes import CULTURES, ENTITIES, RELATIONS, STORY_LANGUAGES, Changes, Scope
from dmbot.memory.models import DM, MemoryRuleError

PLACE, LANGUAGE, ROAD, BIAS = "place", "language", "road", "bias"
KINDS = (PLACE, LANGUAGE, ROAD, BIAS)
GONE = "That note is gone already: it was checked or changed meanwhile."


@dataclass(frozen=True, slots=True)
class Note:
    """One line of the list. `layer`/`value`/`how` for a place (the value is the group's id for
    who rules it); `value` is the language's name for a language. `session_at`: the session it was
    read from (None for an upload or the lore); `made_at`: when DMbot read it."""

    id: str
    kind: str
    entity_id: str
    name: str
    value: str
    layer: str = ""
    how: str = ""
    session_at: int | None = None
    made_at: int = 0
    fact: mf.Fact | None = None  # a place's reading itself (its words, "partly", dry or wet)
    ruler: str = ""  # who rules it, by name, for a ruler line
    relation_id: str = ""  # a road's condition (#4258): `entity_id` is then the road's first place
    map_title: str = ""  # a road's length (#4670 4c): the map that disagrees, and its figure
    measured: str = ""  # ("35 miles", or "a mile or less")


def note_id(kind: str, row_id: str) -> str:
    return f"{kind}:{row_id}"


def split_id(line_id: str) -> tuple[str, str]:
    """(kind, row id), or MemoryRuleError for an id that is neither (a hand-made request)."""
    kind, _, row_id = line_id.partition(":")
    if kind not in KINDS or not row_id:
        raise MemoryRuleError(GONE)
    return kind, row_id


# A character's languages from the story that DMbot read and that are no question already: none for
# a character with a sheet (its languages are the sheet's, #4229), none that ❓ Questions for you
# asks or the DM answered there (any offer row, waiting or never: Supervisor's ruling on #4285,
# "language questions stay in Questions for you", so nothing is asked twice).
_LANGUAGE_NOTES = (
    " AND given_by = 'read'"
    " AND NOT EXISTS (SELECT 1 FROM character_sheets h WHERE h.guild_id = memory_story_languages"
    ".guild_id AND h.campaign_id = memory_story_languages.campaign_id"
    " AND h.entity_id = memory_story_languages.entity_id)"
    " AND NOT EXISTS (SELECT 1 FROM memory_trait_offers o WHERE o.guild_id ="
    " memory_story_languages.guild_id AND o.campaign_id = memory_story_languages.campaign_id"
    " AND o.entity_id = memory_story_languages.entity_id AND o.kind = 'language'"
    " AND o.name_key = memory_story_languages.name_key)"
    " ORDER BY created_at, id"
)


async def read(scope: Scope, *, with_roads: bool = False) -> list[Note]:
    """Every open note of the campaign, places, languages, feelings then roads, each oldest
    first. Only lines on an entry the DM keeps (confirmed or waiting, never one turned down or
    merged away), and a ruler only when the group is kept too (so ✅ never makes a group the DM
    turned down rule).
    `with_roads`: with the road lengths a map disagrees with (only while the map answers are on)."""
    names = {
        str(r["id"]): str(r["name"])
        for r in await scope.select(
            ENTITIES, " AND status IN ('confirmed', 'proposed') AND merged_into IS NULL", []
        )
    }
    out: list[Note] = []
    read = await map_fact_store.dm_read_notes(scope, gate.Viewer.the_dm())
    # a road's condition (#4258): a known road the DM hasn't taken away, between two kept places
    on_roads = sorted({f.relation_id for f, _, _ in read if f.relation_id})
    roads = {
        str(r["id"]): (str(r["subject_id"]), str(r["object_id"]))
        for r in (
            await scope.select(
                RELATIONS,
                " AND id = ANY(%s) AND status <> 'rejected' AND to_session_at IS NULL",
                [on_roads],
            )
            if on_roads
            else []
        )
        if r["subject_id"] in names and r["object_id"] in names
    }
    for fact, session_at, made_at in read:
        if fact.relation_id:
            if fact.relation_id in roads:
                a, b = roads[fact.relation_id]
                out.append(
                    Note(
                        note_id(PLACE, fact.id), PLACE, a,
                        f"the road between {names[a]} and {names[b]}", fact.value, fact.layer,
                        fact.how, session_at, made_at, fact, relation_id=fact.relation_id,
                    )
                )  # fmt: skip
            continue
        if fact.place_id in names and (fact.layer != mf.CONTROL or fact.value in names):
            out.append(
                Note(
                    note_id(PLACE, fact.id), PLACE, fact.place_id, names[fact.place_id],
                    fact.value, fact.layer, fact.how, session_at, made_at, fact,
                    names.get(fact.value, "") if fact.layer == mf.CONTROL else "",
                )
            )  # fmt: skip
    for r in await scope.select(STORY_LANGUAGES, _LANGUAGE_NOTES, []):
        who = str(r["entity_id"])
        if who in names:
            out.append(
                Note(
                    note_id(LANGUAGE, str(r["id"])), LANGUAGE, who, names[who], str(r["name"]),
                    made_at=int(r["created_at"]),
                )
            )  # fmt: skip
    out.extend(await _biases(scope, names))
    if with_roads:
        out.extend(await _roads(scope, names))
    return out


async def _biases(scope: Scope, names: dict[str, str]) -> list[Note]:
    """The biases DMbot read and the DM hasn't answered (#4229 slice 4): `value` names the target
    ("duergar", or a culture's name) and `how` is the step. Only a holder the DM keeps."""
    culture_names = {
        str(r["id"]): str(r["name"])
        for r in await scope.select(CULTURES, " AND status <> 'rejected'", [])
    }
    out: list[Note] = []
    for b in await biases.waiting(scope):
        target = biases.target_words(b, culture_names)
        if b.holder_id in names and target:
            out.append(
                Note(note_id(BIAS, b.id), BIAS, b.holder_id, names[b.holder_id], target,
                     how=b.step)
            )  # fmt: skip
    return out


async def _roads(scope: Scope, names: dict[str, str]) -> list[Note]:
    """The road lengths DMbot read that a kept map's straight line disagrees with (§9)."""
    from dmbot.maps import disagree
    from dmbot.maps.store import roads_off  # (maps builds on this package)

    guild_id, campaign_id = scope.ids
    out: list[Note] = []
    for r in await roads_off(scope.conn, guild_id, campaign_id, read_only=True, relation_ids=None):
        if r.a_id in names and r.b_id in names:
            out.append(
                Note(
                    note_id(ROAD, r.relation_id), ROAD, r.a_id,
                    f"{names[r.a_id]} to {names[r.b_id]}", r.stated, session_at=r.session_at,
                    made_at=r.created_at,  # (the road's: no column holds when its length came)
                    relation_id=r.relation_id, map_title=r.off.map.title,
                    measured=disagree.amount(r.off),
                )
            )  # fmt: skip
    return out


async def _still_open(w: Changes, line_ids: Collection[str]) -> dict[str, set[str]]:
    """The lines of these that are open notes now, by the list's own rule (an entry turned down
    since, a hand-made id, a language that is a question meanwhile, a road the DM kept or that the
    map agrees with now: none of them), by kind. MemoryRuleError(GONE) for an id of no kind."""
    asked = _split_all(line_ids)
    # (a road id only comes from a list drawn while the map answers were on: the callers check the
    # switch; keeping a length the DM was shown is harmless either way)
    now = {n.id for n in await read(w, with_roads=bool(asked[ROAD]))}
    return _split_all([i for i in line_ids if i in now])


async def keep(w: Changes, line_ids: Collection[str]) -> int:
    """The DM's ✅ on these lines, in the caller's one write (one Undo): a place's reading, a
    language and a road's length each become the DM's own word. A line checked or gone meanwhile is
    left; when none of them is open any more, MemoryRuleError(GONE), as 🚫 says. How many kept."""
    open_now = await _still_open(w, line_ids)
    if line_ids and not any(open_now.values()):
        raise MemoryRuleError(GONE)
    kept = await map_fact_store.keep_rows(w, open_now[PLACE])
    for row_id in sorted(open_now[LANGUAGE]):
        await w.update(STORY_LANGUAGES, row_id, {"given_by": DM})
        kept += 1
    for row_id in sorted(open_now[ROAD]):  # (as the road's ✅ Length is right, #4255)
        await w.update(RELATIONS, row_id, {"length_from": place_levels.BY_DM})
        kept += 1
    kept += await biases.keep(w, sorted(open_now[BIAS]))  # (the DM's from now on, #4229)
    return kept


async def turn_down(w: Changes, line_id: str) -> bool:
    """The DM's 🚫 Not right on one line (one Undo). A place's reading is turned down and a
    language is marked as the DM's no, as the character's own ✖ does (#4362): neither is ever read
    in again. True when a named culture went with it, at every place (#4257 slice 3b). A road's
    length has no 🚫: the length or the map may be the one that's off, so the DM changes the road
    or the map themselves (§9: nothing changes because the two differ)."""
    open_now = await _still_open(w, [line_id])
    if open_now[PLACE]:
        done = await map_fact_store.reject_row(w, next(iter(open_now[PLACE])))
        if done == map_fact_store.GONE_ALREADY:
            raise MemoryRuleError(GONE)
        return done == map_fact_store.NAME_TOO
    if open_now[BIAS]:  # (closed as history: never read in again, #4229)
        if not await biases.turn_down(w, next(iter(open_now[BIAS]))):
            raise MemoryRuleError(GONE)
        return False
    if not open_now[LANGUAGE]:
        raise MemoryRuleError(GONE)
    await language_rows.say_not(w, next(iter(open_now[LANGUAGE])))
    return False


def of_kind(notes: Sequence[Note], kind: str) -> list[str]:
    """The ids of one group's lines, for ✅ Keep all."""
    if kind not in KINDS:
        raise MemoryRuleError(GONE)
    return [n.id for n in notes if n.kind == kind]


def _split_all(line_ids: Collection[str]) -> dict[str, set[str]]:
    by_kind: dict[str, set[str]] = {kind: set() for kind in KINDS}
    for line_id in line_ids:
        kind, row_id = split_id(line_id)
        by_kind[kind].add(row_id)
    return by_kind


__all__ = [
    "BIAS", "GONE", "KINDS", "LANGUAGE", "PLACE", "ROAD", "Note", "keep", "note_id", "of_kind",
    "read", "split_id", "turn_down",
]  # fmt: skip
