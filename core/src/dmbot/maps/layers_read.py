"""What a map shows outright, as layer rows for the DM to check (#4259, MapBot layers slice 8a;
docs/design/mapbot_layers.md §5.2, §7.1 to 7.4; Supervisor's ruling on #4259). Pure: no database,
no Discord, no AI. The close read (`images._shows`) gives each thing a map shows from a legend, a
labelled border, a colour key or a terrain symbol; this turns it into `map_fact_store.Read` rows for
the store, which writes them as `read` with `source = 'map'` and checks every value again.

Rules kept here:
- **Kept places only**, by an exact name or kept other name (as pins are, ruling 2 on #4670); a
  name that is two places, or none, writes nothing (DMbot never picks between two).
- **A ruler** is a kept group or place named exactly as the map writes it; anything else writes
  nothing (never a guess from a banner, §5.2).
- **Words** are the layers' own lists (`memory/map_facts.py`); a culture or a people may be written
  as the lineage, its adjective or its plural ("elf", "elven", "elves"). Anything else writes
  nothing: not sure means nothing.
- **A culture a map shows is "strong"**: a legend or colour key marks where a culture is the
  place's own, never traces of one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from dmbot.images import Shown
from dmbot.memory import map_facts as mf
from dmbot.memory import place_peoples
from dmbot.memory.map_fact_store import Read
from dmbot.memory.models import name_key

MAP_CULTURE_HOW = "strong"  # (a legend's culture is the place's own: see the module's rules)

# A lineage as a map may write it: the key, its adjective or its plural.
_LINEAGE: dict[str, str] = {}
for _key, (_adjective, _plural) in place_peoples.LINEAGES.items():
    _LINEAGE.update({_key: _key, _adjective: _key, _plural: _key})


def value_of(layer: str, written: str) -> str | None:
    """The stored value for a word the map wrote, or None when it is none of the layer's own."""
    word = " ".join(written.casefold().split())
    if layer in (mf.TERRAIN, mf.CLIMATE):
        return word if word in mf.VALUES[layer] else None
    if layer in (mf.CULTURE, mf.POPULATION):
        return _LINEAGE.get(word)
    return None


def reads(
    shows: Iterable[Shown],
    places: Mapping[str, str | None],
    rulers: Mapping[str, str | None],
) -> list[Read]:
    """The rows to write: `places` and `rulers` are name key → the one kept entry (None for a name
    that is more than one). Each (place, layer, value) once; control's value is the ruler's id."""
    out: list[Read] = []
    seen: set[tuple[str, str, str]] = set()
    for s in shows:
        place = places.get(name_key(s.place))
        if place is None:
            continue
        if s.layer == mf.CONTROL:
            ruler = rulers.get(name_key(s.value))
            if ruler is None or ruler == place:
                continue
            row = Read(place, mf.CONTROL, ruler, how="rules")
        else:
            value = value_of(s.layer, s.value)
            if value is None:
                continue
            how = MAP_CULTURE_HOW if s.layer == mf.CULTURE else ""
            row = Read(place, s.layer, value, how=how)
        key = (row.place_id, row.layer, row.value)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out
