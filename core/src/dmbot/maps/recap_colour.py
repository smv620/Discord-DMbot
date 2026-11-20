"""Recap colour (#4260, MapBot layers slice 10b; design §9 "After the session"; Supervisor's ruling
on #4260): the land, climate, ruler, culture and who lives in the places a session visited, as one
plain line each, for the recap writer to use as background where the session touched the place.

**The players' view only** (Supervisor's ruling): a recap is read by the table, so a line holds only
rows the whole party may see, through THE reader (`map_fact_store.facts_for` with the party's
viewer, `map_facts.for_players`): confirmed by the DM, marked as known to the table, of a place the
party knows, and a ruler only when the party knows the group too. So DMbot's unkept reads never
reach it, and a row read from a DM-only map reaches it only once the DM has kept it and marked it
known to the table (the place's 👁️ in World notes), as on the place card.

Names are the caller's, and must be the names the players know (never a secret name): a place or a
ruler with no name there is left out, never looked up here. Culture and people are said in the
shared words (`memory.layer_words`), which may look up a people's name in the campaign's list.

Pure of AI: reads only. Wiring it into the recap writer is CloudDev-lead's, behind a switch, scored
before and after (`docs/RECAP_STANDARD.md`)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from dmbot.memory import gate, map_fact_store
from dmbot.memory import map_facts as mf
from dmbot.memory.layer_words import (
    RULED,
    climate_words,
    culture_words,
    land_words,
    people_words,
)

if TYPE_CHECKING:
    from dmbot.memory._changes import Scope

MAX_PLACES = 12  # (background for a recap, not a gazetteer: the first places the session named)


def line(name: str, facts: Sequence[mf.Fact], names: Mapping[str, str]) -> str | None:
    """'Bryn Shander: land ice and snow; climate freezing; ruled by Ten-Towns; culture strongly
    dwarven; who lives there mostly humans.' None when nothing is left to say."""
    by_layer: dict[str, list[mf.Fact]] = {}
    for f in facts:
        by_layer.setdefault(f.layer, []).append(f)
    parts: list[str] = []
    if land := by_layer.get(mf.TERRAIN):
        parts.append("land " + ", ".join(dict.fromkeys(land_words(f) for f in land)))
    if climate := by_layer.get(mf.CLIMATE):
        parts.append("climate " + ", ".join(dict.fromkeys(climate_words(f) for f in climate)))
    for f in by_layer.get(mf.CONTROL, []):
        ruler = names.get(f.value)
        if ruler:
            parts.append(f"{RULED.get(f.how or 'rules', 'Ruled by').lower()} {ruler}")
    if culture := by_layer.get(mf.CULTURE):
        parts.append("culture " + ", ".join(dict.fromkeys(culture_words(f) for f in culture)))
    if people := by_layer.get(mf.POPULATION):
        parts.append("who lives there " + ", ".join(dict.fromkeys(people_words(f) for f in people)))
    return f"{name}: {'; '.join(parts)}." if parts else None


async def lines(
    scope: Scope,
    place_ids: Sequence[str],
    names: Mapping[str, str],
    viewer: gate.Viewer | None = None,
) -> list[str]:
    """One line for each place the session visited (in the order given, at most `MAX_PLACES`)
    that the party may know something of. `names`: entry id to the name the PLAYERS know it by
    (the caller reads it through the party's view, never the DM's), for the places and for any
    ruler; one with no name here is left out."""
    wanted = [p for p in dict.fromkeys(place_ids) if p in names][:MAX_PLACES]
    if not wanted:
        return []
    # (the party at that session when the caller gives it, #4537 slice 3b; else today's party)
    found = await map_fact_store.facts_for(scope, wanted, viewer or gate.Viewer.the_party())
    out: list[str] = []
    for place in wanted:
        said = line(names[place], found.get(place, []), names)
        if said is not None:
            out.append(said)
    return out
