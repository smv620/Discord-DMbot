"""What DMbot is sure of, added for the DM (#3878; docs/PLAN.md, "Uploads and imports apply what
DMbot is sure of").

After a lore upload or an import, the names, other names and connections the reader found are added
through the store's own DM calls, as `source=DM`, so every rule is checked again and each change can
be undone like the DM's own. The same three steps are what `devtools/accept_all` runs on the public
sample for dev1: the tool calls these functions with `narrow=False`, which keeps its old counts.

`narrow=True` (the upload and import flow) leaves these as questions for the DM, never guessed
(Supervisor's ruling on #3878, from GraphEngineer's review of what the keys score):
- a name that needs a person to say who plays it, and one with no kind to confirm it as;
- a described person ("the woman with the scar"; an unnamed entry): it belongs in the unnamed and
  same-person questions, never confirmed as a name (#3431);
- a name that is a rules word or a kind of creature ("Owlbear", "Goodberries"): the readers refuse
  those, and one that slips through is a question, not a fact.
"Which one?" and same-person questions are not answered here at all, and nor are the family links
DMbot worked out (they stay in their own question). Counts only; never a name or a word of a text.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Collection
from dataclasses import dataclass, field

from dmbot.memory import backstory_keep
from dmbot.memory.backstory_links import BackstoryLinks
from dmbot.memory.claims import ClaimStore
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    HOW_DM,
    PLAYER_CHARACTER,
    Entity,
    MemoryRuleError,
)
from dmbot.memory.ontology import rules_word_for
from dmbot.memory.store import MemoryStore
from dmbot.names.not_names import CREATURE_KINDS

log = logging.getLogger(__name__)

NAMES, OTHER_NAMES, CONNECTIONS = "names", "other names", "connections"


def _words(text: str) -> str:
    return " ".join(
        "".join(c if c.isalnum() else " " for c in text.casefold()).split()
    ).removesuffix("s")


def not_a_name(name: str) -> bool:
    """A rules word or a kind of creature, taken as the whole name."""
    if rules_word_for(name) is not None:
        return True
    return _words(name) in CREATURE_KINDS or name.casefold().strip() in CREATURE_KINDS


@dataclass
class Tally:
    """What was done, by kind of thing: counts only."""

    accepted: Counter[str] = field(default_factory=Counter)
    waiting: Counter[str] = field(default_factory=Counter)  # left for a person to answer
    refused: Counter[str] = field(default_factory=Counter)  # a rule said no (it stays waiting)
    batches: list[int] = field(default_factory=list)  # the changes made, to undo them together
    kinds: Counter[str] = field(default_factory=Counter)  # names added, by their kind
    shown: int = 0  # held-back connections the table stated, shown to the players (#3963 c)
    held: int = 0  # kept hidden beside a connection the story hides (#3975, #3979)
    # Said while the party was split (#2996): the group there knows it, or which group can't be told
    split_group: int = 0
    split_unknown: int = 0
    # A new name said only while the party was split (#4037): the group knows it / nobody does
    names_split_group: int = 0
    names_split_unknown: int = 0

    def add_batch(self, batch: int | None) -> None:
        if batch is not None:
            self.batches.append(batch)


def refused(tally: Tally, what: str, exc: Exception) -> None:
    tally.refused[what] += 1
    if not isinstance(exc, MemoryRuleError):
        log.warning("auto_accept: a %s could not be added (%s)", what, type(exc).__name__)


async def accept_names(
    memory: MemoryStore,
    guild: int,
    campaign: str,
    tally: Tally,
    *,
    dry_run: bool = False,
    narrow: bool = True,
    only: Collection[str] | None = None,
    kind_of: Callable[[Entity], str],
) -> None:
    """Confirm each waiting name that was read with its kind. `only`: just these entries (an
    upload's own, so an earlier session's names are not touched); None for every waiting one.
    `kind_of`: the reader's kind for an entry (`ui.names.background_kind`; passed in, as the memory
    package does not import the screens)."""
    for entity in await memory.entities(guild, campaign, statuses=("proposed",)):
        if only is not None and entity.id not in only:
            continue
        kind = kind_of(entity)
        if not kind or entity.role == PLAYER_CHARACTER:
            tally.waiting[NAMES] += 1
            continue
        if narrow and (getattr(entity, "unnamed", False) or not_a_name(entity.name)):
            tally.waiting[NAMES] += 1
            continue
        if dry_run:
            tally.accepted[NAMES] += 1
            tally.kinds[kind] += 1
            continue
        try:
            written = await memory.confirm_entity(guild, campaign, entity.id, kind, source=DM)
            tally.names_split_group += written.split == "group"
            tally.names_split_unknown += written.split == "unknown"
            tally.accepted[NAMES] += 1
            tally.kinds[kind] += 1
            tally.add_batch(written.batch)
        except Exception as exc:  # a rule, or anything else: counted, the run goes on
            refused(tally, NAMES, exc)


async def accept_other_names(
    memory: MemoryStore,
    guild: int,
    campaign: str,
    tally: Tally,
    *,
    dry_run: bool = False,
    only_entries: Collection[str] | None = None,
) -> None:
    """Confirm the other names and spellings suggested for entries. `only_entries`: just those of
    these entries (None for all)."""
    for alias, _entry in await memory.suggested_names(guild, campaign):
        if only_entries is not None and alias.entity_id not in only_entries:
            continue
        if dry_run:
            tally.accepted[OTHER_NAMES] += 1
            continue
        try:
            written = await memory.update_alias(
                guild, campaign, alias.id, source=DM, status=CONFIRMED
            )
            tally.accepted[OTHER_NAMES] += 1
            tally.add_batch(written.batch)
        except Exception as exc:
            refused(tally, OTHER_NAMES, exc)


async def accept_connections(
    memory: MemoryStore,
    links: BackstoryLinks,
    guild: int,
    campaign: str,
    tally: Tally,
    *,
    dry_run: bool = False,
    upload: str | None = None,
) -> None:
    """Keep the connections found in a text whose two names are answered, as the DM's keep-all
    does; the rest wait for a name or an answer. `upload`: just those read from this upload (its
    name and date as the DM saw them); None for all."""
    onto = await memory.ontology(guild, campaign)
    symmetric = {p.key for p in onto.predicates.values() if p.symmetric}
    every = [
        w
        for w in await links.waiting(guild, campaign, symmetric=symmetric)
        if upload is None or w.upload == upload
    ]
    # (one DMbot inferred is never kept unasked: it waits for the DM's own press, #2959 item 5;
    # nor a hand-over, which ends what someone else holds, #4115)
    ready = [w for w in every if w.ready and not w.inferred and not w.hand_over]
    tally.waiting[CONNECTIONS] += len(every) - len(ready)
    if dry_run:
        tally.accepted[CONNECTIONS] += len(ready)
        return
    try:
        done = await backstory_keep.keep_all(memory, links, guild, campaign, ready)
    except Exception as exc:  # the connections kept before it stay kept
        tally.refused[CONNECTIONS] += len(ready)
        log.warning("auto_accept: keeping connections stopped (%s)", type(exc).__name__)
        return
    tally.accepted[CONNECTIONS] += len(done.kept)
    tally.refused[CONNECTIONS] += done.refused
    for _link, batch in done.kept:
        tally.add_batch(batch)


async def accept_dm_claims(
    memory: MemoryStore,
    guild: int,
    campaign: str,
    tally: Tally,
    *,
    dm_ids: Collection[int],
    opted_in: Collection[int],
    sessions: Collection[int],
    dry_run: bool = False,
) -> None:
    """Keep the connections an import's sessions state in the DM's own lines (#3897), through the
    claims review's own yes (`ClaimStore.confirm`: every rule, the speakers' consent read again,
    the DM's own batch for Undo). A claim is sure only when all hold: it is stated, not a reading;
    it is `dm_said`; every cited line was spoken by one of the campaign's DMs (checked here, never
    trusting the AI's label); it is still waiting with both its names answered (what `waiting`
    returns); and it came from `sessions`, the starts of this import's sessions. Players' claims,
    plans, readings and lore's worked-out ties stay questions. Secrecy is decided as a plain ✅
    in the review decides it (`secret=False`, with the store's own rules for names the DM hid);
    this adds no "secret" choice, which only the DM makes."""
    dms = set(dm_ids)
    starts = set(sessions)
    claims = ClaimStore(memory)
    for claim in await claims.waiting(guild, campaign, opted_in=opted_in):
        if claim.session_started_at not in starts:
            continue  # (another session's: not this import's to answer)
        if (
            claim.reading is not None
            or claim.how != HOW_DM
            or not claim.speaker_ids
            or not set(claim.speaker_ids) <= dms
        ):
            tally.waiting[CONNECTIONS] += 1
            continue
        if dry_run:
            tally.accepted[CONNECTIONS] += 1
            continue
        try:
            done = await claims.confirm(guild, campaign, claim.id, opted_in=opted_in, secret=False)
            tally.accepted[CONNECTIONS] += 1
            tally.shown += done.shown
            tally.held += done.held
            tally.split_group += done.split and done.scene > 0
            tally.split_unknown += done.split and done.scene == 0
            tally.add_batch(done.batch)
        except Exception as exc:  # a rule, or anything else: counted, the run goes on
            refused(tally, CONNECTIONS, exc)
