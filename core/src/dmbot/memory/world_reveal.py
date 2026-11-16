"""A World read reveals what it states plainly (#4524 slice 1b, Supervisor's ruling C).

The owner's two-stage sample (#4523) reads four secret backstories, then a public lore that "reveals
some of the entities and connections to all PCs". Before this, a later public read never opened
what an earlier backstory had kept: names the campaign already has are skipped by the reader, and
a connection it already has is not offered again.

Now a **🌍 Common knowledge** (World) read reveals the hidden items it names or states itself:
- an entry it names word for word (`lore_read.LoreRead.known_named`) that the players are not shown,
  where showing it is allowed at all (never a clue only some know, the party, a player's own
  character or a person at the table: `reveal_said`'s rule);
- a connection it states itself, never DMbot's inference and never through a secret name
  (`queue_links`'s `revealable`), with its words as they are kept;
- a secret other name only where the text uses that very name for that same person: the line
  about the person's own name lists it (`known_spellings`). A matching name the text gives on its
  own may be another person, so it stays hidden.

Each is made 🌍 Common knowledge exactly as the DM's own answer on "Who knows this?" does
(`link_edits.with_common`): shown to the players first, then known to every character, now or later.
A smart or spoiler read reveals nothing. Every change is one of the upload's own, so its
↩️ Undo takes them back. The notice says how many, never which.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from dmbot.memory import link_edits, name_edits
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    PLAYER_CHARACTER,
    Alias,
    MemoryRuleError,
    Relation,
)
from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)
PERSON = "person"  # (a person at the table: `store.PERSON`, never a story entry to show)


@dataclass
class Revealed:
    """What a World read revealed: the changes (for the upload's Undo) and the counts."""

    batches: list[int] = field(default_factory=list)
    entries: int = 0
    connections: int = 0
    names: int = 0

    @property
    def total(self) -> int:
        return self.entries + self.connections + self.names


def may_show(entity: object) -> bool:
    """An entry a public read may show: confirmed and kept from the players, but never one the DM
    made a clue only some know, the party, a player's own character or a person at the table (the
    same rule as when the table says a name, `MemoryStore.reveal_said`)."""
    return bool(
        getattr(entity, "status", None) == CONFIRMED
        and getattr(entity, "hidden_from_players", False)
        and not getattr(entity, "audience_limited", False)
        and not getattr(entity, "party", False)
        and getattr(entity, "type", "") not in (PERSON, "clue")
        and getattr(entity, "role", None) != PLAYER_CHARACTER
        and getattr(entity, "played_by", None) is None
    )


async def reveal(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    *,
    entries: Sequence[str] = (),
    links: Sequence[Relation] = (),
    aliases: Sequence[Alias] = (),
) -> Revealed:
    """Make these entries, connections and other names 🌍 Common knowledge, entries first (a
    connection is shown only once both its names are). One that can't be shown (an end still kept
    from the players) or that changed meanwhile is left as it is: the read goes on. Never raises
    for one item; each change's batch is kept for the upload's Undo."""
    out = Revealed()
    shown_now: list[str] = []
    for entity_id in dict.fromkeys(entries):
        entity = await memory.entity(guild_id, campaign_id, entity_id)
        if entity is None or not may_show(entity):
            continue
        try:
            written = await memory.set_hidden_from_players(
                guild_id, campaign_id, entity_id, False, source=DM, dm_chose_secret=False
            )
        except MemoryRuleError as exc:
            log.info("A World read could not show an entry (%s)", type(exc).__name__)
            continue
        if written.batch is not None:
            out.batches.append(written.batch)
        shown_now.append(entity_id)
        out.entries += 1
    if shown_now:
        common = await memory.add_common_knowledge(
            guild_id, campaign_id, "entry", shown_now, source=DM
        )
        if common.batch is not None:
            out.batches.append(common.batch)
    for link in {r.id: r for r in links}.values():
        made: list[int] = []
        try:
            await link_edits.set_who_knows(
                memory, guild_id, campaign_id, link, link_edits.COMMON, made=made
            )
        except MemoryRuleError as exc:  # (an end kept from the players: it stays as it is)
            log.info("A World read could not reveal a connection (%s)", type(exc).__name__)
            continue
        if made:
            out.batches.extend(made)
            out.connections += 1
    for alias in {a.id: a for a in aliases}.values():
        if not alias.secret:
            continue
        made = []
        try:
            await name_edits.set_who_knows(
                memory, guild_id, campaign_id, alias, link_edits.COMMON, made=made
            )
        except MemoryRuleError as exc:
            log.info("A World read could not reveal an other name (%s)", type(exc).__name__)
            continue
        if made:
            out.batches.extend(made)
            out.names += 1
    return out
