"""One place written two ways ("Northhaven", "North Haven"). Pure: no database, no AI (#3037).

A written text spells a town one way in one paragraph and another way in the next, and the read kept
two places, so a link to the one never met a link to the other. Two place names are the same place
when their letters are the same once case, accents, apostrophes, hyphens and spaces are ignored
(`place_key`). Nothing fuzzier is ever a match: no typos, no similar sounds, no shared words
("North Heaven" is another place). Places only, and written sources only: in spoken lines a space is
a guess, and a split name is a different thing heard (`models.name_key`), so people keep that rule.

The bot never decides. A name that spells an existing place another way is offered to the DM as a
proposed other name of it (the existing "another name of" question, #3034), never joined silently;
two spellings in the same read become one waiting place with the second as its other name.
Anything a player may not see stays unseen: a proposed name is the DM's alone until they say yes.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import replace

from dmbot.memory.background import Found
from dmbot.memory.models import name_key

PLACE = "place"


def place_key(text: str) -> str:
    """`name_key` with the spaces gone too: "North Haven", "Northhaven", "North-Haven" are one."""
    return name_key(text).replace(" ", "")


def fold_in_read(found: Sequence[Found]) -> tuple[list[Found], int]:
    """Places of one read that spell the same place two ways become one place: the first written
    keeps its name and the other spelling goes among its other names. Returns the list and how many
    were folded (a count, for the log). Every other name is left as it is."""
    out: list[Found] = []
    first: dict[str, int] = {}  # place_key → index in `out`
    folded = 0
    for f in found:
        key = place_key(f.name)
        if f.kind != PLACE or not key:
            out.append(f)
            continue
        at = first.get(key)
        if at is None:
            first[key] = len(out)
            out.append(f)
            continue
        keep = out[at]  # (the folded spelling's own quote is dropped: the first place's stands)
        if name_key(f.name) != name_key(keep.name):
            also = tuple(dict.fromkeys((*keep.also, f.name, *f.also)))
            out[at] = replace(
                keep, also=also, secret=(*keep.secret, *f.secret), about=keep.about or f.about
            )
            folded += 1
        # (the same name twice is the merge's business, not a second spelling)
    return out, folded


def near_places(
    found: Iterable[Found],
    places: Mapping[str, Collection[str]],
    refused: Collection[tuple[str, str]] = (),
) -> tuple[dict[str, tuple[str, ...]], list[Found]]:
    """The new places that are an existing place written another way. `places`: entity id → every
    spelling that place has (main name and other names); `refused`: (entity id, `name_key`) of a
    spelling the DM already turned down, which is a new place from then on. Returns the new
    spellings by the existing place's id and the rest of `found`. A spelling that fits two existing
    places is left in `found` (DMbot does not guess which one is meant)."""
    by_key: dict[str, set[str]] = {}
    for entity_id, spellings in places.items():
        for spelling in spellings:
            by_key.setdefault(place_key(spelling), set()).add(entity_id)
    known = {name_key(s) for spellings in places.values() for s in spellings}
    joined: dict[str, list[str]] = {}
    rest: list[Found] = []
    for f in found:
        owners = by_key.get(place_key(f.name), set()) if f.kind == PLACE else set()
        if len(owners) != 1 or name_key(f.name) in known:
            rest.append(f)
            continue
        entity_id = next(iter(owners))
        if (entity_id, name_key(f.name)) in set(refused):
            rest.append(f)
            continue
        joined.setdefault(entity_id, []).extend([f.name, *f.also])
    return {k: tuple(v) for k, v in joined.items()}, rest
