"""Finding a name in a campaign (docs/PLAN.md, "Names at scale": 🔍 Find a name and the
`/dmbot names find:` type-ahead). Pure: answers from the in-memory copy of the names,
never a database query per keystroke.

A name is found by any of its names: the name itself, another name, a secret name (only
for the campaign's DMs), or how it sounds. Best matches first: the whole name, then
names starting with what was typed, then a word starting with it, then anywhere inside,
then sounding alike.
"""

from __future__ import annotations

from dataclasses import dataclass

from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, name_key
from dmbot.memory.sounds import sound_codes

MAX_RESULTS = 25  # Discord's limit for a menu and for type-ahead choices

WHOLE, START, WORD, INSIDE, SOUND = range(5)


@dataclass(frozen=True, slots=True)
class Match:
    entity_id: str
    matched: str  # the name that matched, as written
    secret: bool  # it matched a secret name (only ever for the campaign's DMs)
    how: int  # WHOLE … SOUND, best first


def find(
    lookup: CampaignLookup, typed: str, *, secrets: bool, limit: int = MAX_RESULTS
) -> list[Match]:
    """Confirmed names matching what was typed, best first, one per entry. `secrets`:
    whether secret names may be searched and shown (the campaign's DMs only)."""
    key = name_key(typed)
    if not key:
        return []
    codes = set(sound_codes(typed))
    best: dict[str, Match] = {}
    for entry in lookup.names:
        entity = lookup.entities.get(entry.entity_id)
        if entity is None or entity.status != CONFIRMED or not entry.confirmed:
            continue
        if entry.secret and not secrets:
            continue
        if entry.key == key:
            how = WHOLE
        elif entry.key.startswith(key):
            how = START
        elif any(word.startswith(key) for word in entry.key.split()):
            how = WORD
        elif key in entry.key:
            how = INSIDE
        elif codes and codes & set(entry.codes):
            how = SOUND
        else:
            continue
        found = Match(entry.entity_id, entry.text, entry.secret, how)
        old = best.get(entry.entity_id)
        # Prefer the better match; for the same, the entry's own (non-secret) name.
        if old is None or (how, found.secret, found.matched != entity.name) < (
            old.how,
            old.secret,
            old.matched != entity.name,
        ):
            best[entry.entity_id] = found
    ordered = sorted(
        best.values(), key=lambda m: (m.how, name_key(lookup.entities[m.entity_id].name))
    )
    return ordered[:limit]


def said_lately(lookup: CampaignLookup, limit: int = MAX_RESULTS) -> list[str]:
    """Confirmed entries said most recently (then most often), for the type-ahead with
    nothing typed yet."""
    heard = [
        h
        for h in lookup.heard.values()
        if (e := lookup.entities.get(h.entity_id)) is not None and e.status == CONFIRMED
    ]
    heard.sort(key=lambda h: (-(h.last_session_at or 0), -h.times, h.entity_id))
    return [h.entity_id for h in heard[:limit]]
