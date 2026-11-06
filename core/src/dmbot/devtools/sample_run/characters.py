"""The sample's four player characters (#3554): after the lore read, the run says who plays each
(Cerric to `ist`, Belleros to `jac`, Okthandom to `eri`, Claude to `owe`) itself, so no one has to
press anything, but first counts how many the read had already found as player characters, so the
reader's own result (#3553) stays visible in the report.

It goes through the store's own DM calls, the ones the name card's "who plays" button uses
(`confirm_entity` and `party_on_assignment`), so every rule applies, and it only ever runs in the
sample campaign. Names are only ever counted, never printed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from dmbot.db import Database
from dmbot.devtools.sample_run.plan import PLAYER_CHARACTERS, SampleError
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, DM, MemoryRuleError, name_key
from dmbot.memory.store import MemoryStore

PC = "player_character"


@dataclass(frozen=True, slots=True)
class Set:
    """What the step did, as counts: how many of the four the read found as player characters, and
    how many were set (an entry the read had made) and made (one the read had not)."""

    found_by_the_read: int
    set: int
    made: int


async def check_sample(db: Database, guild: int, campaign: str) -> None:
    """Refuses any campaign that is not the sample, before anything is read or written."""
    from dmbot.devtools import accept_all

    try:
        await accept_all.check_campaign(db, guild, campaign)
    except accept_all.WrongCampaign as exc:
        raise SampleError(str(exc)) from exc


def _entry_for(lookup: CampaignLookup, name: str) -> str | None:
    """The live character the read made for this player character: by the full name, else by the
    first word of it ("Cerric" for "Cerric Vanya'elen") when that names exactly one character.
    None when the read made none."""
    for key in (name_key(name), name_key(name.split()[0])):
        found = {
            e.entity_id
            for e in lookup.by_key.get(key, ())
            if e.entity_id in lookup.entities and lookup.entities[e.entity_id].type == "character"
        }
        if len(found) == 1:
            return next(iter(found))
    return None


async def set_player_characters(
    db: Database,
    memory: MemoryStore,
    guild: int,
    campaign: str,
    players: Mapping[str, int],
) -> Set:
    """Count what the read found, then make each of the four a confirmed player character played by
    its test account (an entry the read made is confirmed as one; a missing one is made)."""
    await check_sample(db, guild, campaign)
    lookup = CampaignLookup.build(await memory.lookup_data(guild, campaign))
    entries = {name: _entry_for(lookup, name) for name, _label in PLAYER_CHARACTERS}
    found = sum(
        1 for entry in entries.values() if entry is not None and lookup.entities[entry].role == PC
    )
    set_, made = 0, 0
    for name, label in PLAYER_CHARACTERS:
        who, entry = players[label], entries[name]
        try:
            if entry is None:
                await memory.add_entity(
                    guild, campaign, type=PC, name=name, source=DM, status=CONFIRMED, played_by=who
                )
                made += 1
            else:
                await memory.confirm_entity(guild, campaign, entry, PC, source=DM, played_by=who)
                set_ += 1
        except MemoryRuleError as exc:
            raise SampleError(f"a player character could not be set ({exc})") from exc
    await memory.party_on_assignment(guild, campaign)  # as the name card does (a lore-only start)
    return Set(found, set_, made)
