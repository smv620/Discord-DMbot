"""A World read reveals what it states plainly (#4524 slice 1b, Supervisor's ruling C).

The owner's two-stage sample (#4523) reads four secret backstories, then a public lore that "reveals
some of the entities and connections to all PCs". Before this, a later public read never opened
what an earlier backstory had kept: names the campaign already has are skipped by the reader, and
a connection it already has is not offered again.

Now a **🌍 Common knowledge** (World) read reveals the hidden items it names or states itself:
- an entry it names word for word (`lore_read.LoreRead.known_named`) that the players are not shown,
  where showing it is allowed at all (never a clue only some know, the party, a player's own
  character or a person at the table: the store's `reveal_said`, whose rule it is);
- a connection it states itself, never DMbot's inference and never through a secret name
  (`queue_links`'s `revealable`), and only where its kept words are the text's own or there are
  none, so the reveal shows no more than the text says;
- a secret other name only where the text uses that very name for that same person: the line
  about the person's own name lists it (`known_spellings`). A matching name the text gives on its
  own may be another person, so it stays hidden.

Each is made 🌍 Common knowledge exactly as the DM's own answer on "Who knows this?" does
(`link_edits.with_common`): shown to the players first, then known to every character, now or later.
A smart or spoiler read reveals nothing. Every change is one of the upload's own, so its
↩️ Undo takes them back; with no notice to hold them (no store, or it failed), they are taken back
at once. The notice says how many, never which.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from dmbot.memory import link_edits, name_edits
from dmbot.memory.models import (
    DM,
    Alias,
    MemoryRuleError,
    Relation,
)
from dmbot.memory.secret_questions import Question
from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)


@dataclass
class Revealed:
    """What a World read revealed: the changes (for the upload's Undo) and the counts."""

    batches: list[int] = field(default_factory=list)
    # what it revealed, for the names the DMs are told privately (#4590 B; never logged)
    entry_ids: list[str] = field(default_factory=list)
    link_ids: list[str] = field(default_factory=list)
    alias_ids: list[str] = field(default_factory=list)
    entries: int = 0
    connections: int = 0
    names: int = 0
    answered: int = 0  # 🌍 questions it answered (`answer`)
    refused: int = 0  # 🌍 questions given back: their item could not be shown yet

    @property
    def total(self) -> int:
        return self.entries + self.connections + self.names


async def reveal(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    *,
    entries: Sequence[str] = (),
    links: Sequence[Relation] = (),
    aliases: Sequence[Alias] = (),
    by_itself: bool = True,
) -> Revealed:
    """Make these entries, connections and other names 🌍 Common knowledge, entries first (a
    connection is shown only once both its names are). One that can't be shown (an end still kept
    from the players) or that changed meanwhile is left as it is: the read goes on. Never raises
    for one item; each change's batch is kept for the upload's Undo.

    `by_itself` (a read revealing without the DM's press): an item whose 🌍 question the DM
    answered "Keep it secret" is left as it is (#4590, Supervisor's ruling 2 on #4605: a reveal by
    itself never overrides the DM's own answer). The DM's own answer (`answer`) passes False."""
    out = Revealed()
    if by_itself:
        kept = await memory.kept_secret_by_the_dm(
            guild_id, campaign_id,
            [*(("memory_entities", e) for e in entries),
             *(("memory_relations", r.id) for r in links),
             *(("memory_aliases", a.id) for a in aliases)],
        )  # fmt: skip
        if kept:
            log.info("A World read left %d item(s) the DM kept secret", len(kept))
            entries = [e for e in entries if ("memory_entities", e) not in kept]
            links = [r for r in links if ("memory_relations", r.id) not in kept]
            aliases = [a for a in aliases if ("memory_aliases", a.id) not in kept]
    # (the store's own rule for showing what was kept back, the one used when the table says a
    # name: never a clue only some know, the party, a player's character or a person at the table)
    shown = await memory.reveal_said(guild_id, campaign_id, list(dict.fromkeys(entries)))
    if shown.batch is not None:
        out.batches.append(shown.batch)
    shown_now = list(shown.value)
    out.entries = len(shown_now)
    out.entry_ids = list(shown_now)
    if shown_now:
        common = await memory.add_common_knowledge(
            guild_id, campaign_id, "entry", shown_now, source=DM
        )
        if common.batch is not None:
            out.batches.append(common.batch)
    # (read again just before: a DM may have chosen who knows one meanwhile, and a reveal never
    # overrides a newer choice)
    now = {
        r.id: r
        for r in await memory.relations(guild_id, campaign_id, include_secret=True)
        if r.id in {x.id for x in links}
    }
    for old in {r.id: r for r in links}.values():
        link = now.get(old.id)
        if (
            link is None
            or link.to_session_at is not None
            or not (link.secret or link.unrevealed)
            or (link.secret, link.unrevealed, link.detail)
            != (old.secret, old.unrevealed, old.detail)
        ):
            continue
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
            out.link_ids.append(link.id)
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
            out.alias_ids.append(alias.id)
    if by_itself and out.batches:
        # (a 🌍 question still waiting about something this read showed is out of date: it is
        # answered by the read's first change, so an Undo of the whole read asks it again;
        # Supervisor's ruling (b) on #4606. A failure here leaves a stale question, never a
        # reveal without its Undo, so it is logged and the read goes on)
        try:
            await memory.answer_waiting_world_questions(
                guild_id, campaign_id,
                [*(("memory_entities", e) for e in out.entry_ids),
                 *(("memory_relations", r) for r in out.link_ids),
                 *(("memory_aliases", a) for a in out.alias_ids)],
                out.batches[0],
            )  # fmt: skip
        except Exception as exc:
            log.warning(
                "A World read's waiting questions weren't answered (%s)", type(exc).__name__
            )
    return out


async def answer(
    memory: MemoryStore, guild_id: int, campaign_id: str, question_ids: Sequence[str]
) -> Revealed | None:
    """The DM's "Let them know" on 🌍 World read questions (#4590): the reveals the read would
    have made by itself, made now as the DM's answer, exactly as `reveal` makes them. A lone
    secret other name (A) is "the same person", so its person is shown with it.

    Every question is taken first, so none is answered twice. One whose item is still not shown
    afterwards (refused: an end still kept from the players, a person who can't be shown) is
    given back to wait, counted in `refused`, never used up. The others are answered by the
    reveal's first change, which an Undo of the whole reveal (newest first) reaches last, so they
    wait again only once all of it is taken back. None when none was still waiting."""
    taken = await memory.claim_secret_questions(guild_id, campaign_id, question_ids)
    if not taken:
        return None
    ids = [q.id for q in taken]
    try:
        entries = [q.item_id for q in taken if q.item_table == "memory_entities"]
        links = [
            r
            for q in taken
            if q.item_table == "memory_relations"
            and (r := await memory.relation(guild_id, campaign_id, q.item_id)) is not None
        ]
        alias_ids = {q.item_id for q in taken if q.item_table == "memory_aliases"}
        aliases = (
            [a for a in await memory.aliases(guild_id, campaign_id, include_secret=True)
             if a.id in alias_ids]
            if alias_ids else []
        )  # fmt: skip
        lone = {q.item_id for q in taken if q.change.get("lone")}
        entries += [a.entity_id for a in aliases if a.id in lone]
        out = await reveal(
            memory, guild_id, campaign_id, entries=entries, links=links, aliases=aliases,
            by_itself=False,
        )  # fmt: skip
        still = await _still_hidden(memory, guild_id, campaign_id, taken)
    except Exception:
        await memory.unclaim_secret_questions(guild_id, campaign_id, ids)
        raise
    if still:
        await memory.unclaim_secret_questions(guild_id, campaign_id, sorted(still))
        out.refused = len(still)
    done = [i for i in ids if i not in still]
    out.answered = len(done)
    if out.batches and done:
        try:
            await memory.secret_questions_answered(guild_id, campaign_id, done, out.batches[0])
        except Exception as exc:  # (the reveal stands; its Undo still takes it back)
            log.warning("A World reveal's questions weren't linked (%s)", type(exc).__name__)
    return out


async def _still_hidden(
    memory: MemoryStore, guild_id: int, campaign_id: str, taken: Sequence[Question]
) -> set[str]:
    """The questions whose item is still not shown to the players after their reveal."""
    entries = [q.item_id for q in taken if q.item_table == "memory_entities"]
    shown = await memory.entries_shown(guild_id, campaign_id, entries) if entries else set()
    alias_ids = {q.item_id for q in taken if q.item_table == "memory_aliases"}
    secret = (
        {a.id for a in await memory.aliases(guild_id, campaign_id, include_secret=True)
         if a.id in alias_ids and a.secret}
        if alias_ids else set()
    )  # fmt: skip
    out: set[str] = set()
    for q in taken:
        if q.item_table == "memory_entities":
            hidden = q.item_id not in shown
        elif q.item_table == "memory_aliases":
            hidden = q.item_id in secret
        else:
            r = await memory.relation(guild_id, campaign_id, q.item_id)
            hidden = r is not None and (r.secret or r.unrevealed)
        if hidden:
            out.add(q.id)
    return out


async def take_back(
    memory: MemoryStore, guild_id: int, campaign_id: str, batches: Sequence[int]
) -> None:
    """Undo these reveals, newest first, when the upload's notice (and so its ↩️ Undo) could not be
    kept: a reveal is only ever made as one of the upload's own changes. One that something since
    depends on is left, and logged."""
    for batch in reversed(batches):
        try:
            await memory.undo(guild_id, campaign_id, batch)
        except Exception as exc:
            log.warning("A World read's reveal could not be taken back (%s)", type(exc).__name__)
