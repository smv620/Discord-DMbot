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
class Corrected:
    """What the correction did, as counts: how many of the four it changed (an entry the read made
    is confirmed as a player character, or one the read had wrong is set right) and how many of
    those it had to make because the read had not."""

    corrected: int
    made: int


async def check_sample(db: Database, guild: int, campaign: str) -> None:
    """Refuses any campaign that is not the sample, before anything is read or written."""
    from dmbot.devtools import accept_all

    try:
        await accept_all.check_campaign(db, guild, campaign)
    except accept_all.WrongCampaign as exc:
        raise SampleError(str(exc)) from exc


def _entry_for(lookup: CampaignLookup, name: str) -> str | None:
    """The live character the read made for this player character, by the character's own name
    (never an alias, so a secret name of someone else cannot be taken): the full name; else the
    first word of it ("Cerric" for "Cerric Vanya'elen") when exactly one entry of that name is a
    player character already. None when the read made none.

    Refuses (rather than guess) when two characters carry the full name, or when something that
    is not a character does: a second entry with the same name would make every lookup of it
    ambiguous."""
    live = list(lookup.entities.values())
    key = name_key(name)
    same = [e for e in live if name_key(e.name) == key]
    characters = [e for e in same if e.type == "character"]
    if len(characters) > 1:
        raise SampleError("two characters in the sample campaign carry one player character's name")
    if characters:
        return characters[0].id
    if same:
        raise SampleError("an entry that is not a character carries a player character's name")
    first = name_key(name.split()[0])
    short = [
        e for e in live if e.type == "character" and e.role == PC and name_key(e.name) == first
    ]
    return short[0].id if len(short) == 1 else None


async def _entries(
    memory: MemoryStore, guild: int, campaign: str
) -> tuple[CampaignLookup, dict[str, str | None]]:
    lookup = CampaignLookup.build(await memory.lookup_data(guild, campaign))
    return lookup, {name: _entry_for(lookup, name) for name, _label in PLAYER_CHARACTERS}


async def found_by_the_read(db: Database, memory: MemoryStore, guild: int, campaign: str) -> int:
    """How many of the four the read proposed as player characters by itself. Read only: it is
    taken before anything is accepted or corrected, so the reader's own result stays visible."""
    await check_sample(db, guild, campaign)
    lookup, entries = await _entries(memory, guild, campaign)
    return sum(
        1 for entry in entries.values() if entry is not None and lookup.entities[entry].role == PC
    )


async def correct_player_characters(
    db: Database,
    memory: MemoryStore,
    guild: int,
    campaign: str,
    players: Mapping[str, int],
) -> Corrected:
    """Make each of the four a confirmed player character played by its test account: an entry the
    read made is confirmed as one, a missing one is made, one already right is left alone. Not
    atomic: a rule that stops it part way leaves the earlier ones done, and the next run, which
    starts from a new campaign, is unaffected."""
    await check_sample(db, guild, campaign)
    lookup, entries = await _entries(memory, guild, campaign)
    corrected, made = 0, 0
    for name, label in PLAYER_CHARACTERS:
        who, entry = players[label], entries[name]
        try:
            if entry is None:
                await memory.add_entity(
                    guild, campaign, type=PC, name=name, source=DM, status=CONFIRMED, played_by=who
                )
                corrected += 1
                made += 1
                continue
            e = lookup.entities[entry]
            if e.status == CONFIRMED and e.role == PC and e.played_by == who:
                continue  # already right
            await memory.confirm_entity(guild, campaign, entry, PC, source=DM, played_by=who)
            corrected += 1
        except MemoryRuleError as exc:
            raise SampleError(f"a player character could not be set ({exc})") from exc
    await memory.party_on_assignment(guild, campaign)  # as the name card does (a lore-only start)
    return Corrected(corrected, made)
