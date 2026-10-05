"""The in-memory copy of a campaign's names, for the Transcript Cleaner (docs/PLAN.md,
"Campaign memory storage", Speed).

During a session, checking a line must not touch the database. `CampaignLookup` is a
read-only snapshot of one campaign: every name and alias (with sound codes), the
"don't change" and "change to" rules, and who is related to whom. `LookupCache` keeps
one per (server, campaign) and reloads it when the campaign's memory changes (a Postgres
NOTIFY), or reloads everything after the listening connection drops. Postgres stays the
single source of truth.

Secret aliases ("the hooded stranger" is Belleros) are included and marked, so the
Cleaner can recognise those words as known and leave them alone. A transcript must never
reveal a secret identity, so a secret alias is never offered as a fix.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from dmbot.memory import notify
from dmbot.memory.models import CONFIRMED, FIX, KEEP, Alias, Correction, Entity, Relation, name_key
from dmbot.memory.sounds import sound_codes

log = logging.getLogger(__name__)

RECONNECT_DELAY_S = (1.0, 2.0, 5.0, 10.0, 30.0)


@dataclass(frozen=True, slots=True)
class LookupData:
    """What a lookup is built from, read in one consistent snapshot."""

    version: int
    entities: tuple[Entity, ...]  # live ones (proposed or confirmed)
    aliases: tuple[Alias, ...]  # not rejected, of live entities, secret ones included
    corrections: tuple[Correction, ...]
    relations: tuple[Relation, ...]  # live, not secret, between live entities


@dataclass(frozen=True, slots=True)
class NameEntry:
    """One way a name is said or written, ready for matching."""

    alias_id: str
    entity_id: str
    text: str
    key: str
    kind: str
    secret: bool
    # Only confirmed names (alias and entity) may make a silent fix (PLAN, Transcript
    # Cleaner): a proposed one reaches at most a fix with Undo.
    confirmed: bool
    codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CampaignLookup:
    version: int
    entities: dict[str, Entity]
    names: tuple[NameEntry, ...]
    by_key: dict[str, tuple[NameEntry, ...]]
    by_sound: dict[str, tuple[NameEntry, ...]]
    keep_keys: frozenset[str]  # words that must stay as heard
    fixes: dict[str, tuple[str, ...]]  # heard key → entity IDs the DM said it means
    neighbours: dict[str, frozenset[str]]  # entity → entities it's related to

    @classmethod
    def build(cls, data: LookupData) -> CampaignLookup:
        entities = {e.id: e for e in data.entities}
        names = tuple(
            NameEntry(
                a.id,
                a.entity_id,
                a.text,
                a.key,
                a.kind,
                a.secret,
                a.status == CONFIRMED and entities[a.entity_id].status == CONFIRMED,
                sound_codes(a.text),  # the same function as for heard words
            )
            for a in data.aliases
            if a.entity_id in entities
        )
        by_key: dict[str, list[NameEntry]] = defaultdict(list)
        by_sound: dict[str, list[NameEntry]] = defaultdict(list)
        for entry in names:
            by_key[entry.key].append(entry)
            for code in entry.codes:
                by_sound[code].append(entry)
        fixes: dict[str, list[str]] = defaultdict(list)
        keep: set[str] = set()
        for c in data.corrections:
            if c.action == KEEP:
                keep.add(c.heard_key)
            elif c.action == FIX and c.entity_id in entities:
                fixes[c.heard_key].append(c.entity_id)
        neighbours: dict[str, set[str]] = defaultdict(set)
        for r in data.relations:
            if r.subject_id in entities and r.object_id in entities:
                neighbours[r.subject_id].add(r.object_id)
                neighbours[r.object_id].add(r.subject_id)
        return cls(
            data.version,
            entities,
            names,
            {k: tuple(v) for k, v in by_key.items()},
            {k: tuple(v) for k, v in by_sound.items()},
            frozenset(keep),
            {k: tuple(v) for k, v in fixes.items() if k not in keep},  # keep wins
            {k: frozenset(v) for k, v in neighbours.items()},
        )

    def exact(self, heard: str) -> tuple[NameEntry, ...]:
        """Names spelled like `heard` (case, accents, apostrophes and hyphens ignored).
        Secret aliases are included: they're known words, never to be changed."""
        return self.by_key.get(name_key(heard), ())

    def sounds_like(self, heard: str) -> tuple[NameEntry, ...]:
        """Names that sound like `heard`, for fixing: secret aliases left out, so a fix
        can never write a secret identity into a transcript."""
        seen: dict[str, NameEntry] = {}
        for code in sound_codes(heard):
            for entry in self.by_sound.get(code, ()):
                if not entry.secret:
                    seen.setdefault(entry.alias_id, entry)
        return tuple(seen.values())

    def keep_as_heard(self, heard: str) -> bool:
        """The DM said these words stay as heard (an Undo or "Keep as heard")."""
        return name_key(heard) in self.keep_keys

    def fixes_for(self, heard: str) -> tuple[str, ...]:
        """Entities the DM said these words mean, unless they're to stay as heard."""
        return self.fixes.get(name_key(heard), ())

    def related(self, entity_id: str) -> frozenset[str]:
        return self.neighbours.get(entity_id, frozenset())


class LookupSource(Protocol):
    async def lookup_data(self, guild_id: int, campaign_id: str) -> LookupData: ...


Listen = Callable[[str], AsyncGenerator[str, None]]


@dataclass(slots=True)
class _Slot:
    lookup: CampaignLookup | None = None
    stale: bool = True
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LookupCache:
    """One `CampaignLookup` per (server, campaign), reloaded when its memory changes."""

    def __init__(self, source: LookupSource) -> None:
        self._source = source
        self._slots: dict[tuple[int, str], _Slot] = {}
        self.loads = 0

    async def get(self, guild_id: int, campaign_id: str) -> CampaignLookup:
        """The campaign's lookup, loading it first if it's missing or out of date."""
        slot = self._slots.setdefault((guild_id, campaign_id), _Slot())
        if slot.lookup is not None and not slot.stale:
            return slot.lookup
        async with slot.lock:  # one load at a time per campaign
            if slot.lookup is None or slot.stale:
                slot.stale = False  # a change arriving during the load marks it again
                data = await self._source.lookup_data(guild_id, campaign_id)
                slot.lookup = await asyncio.to_thread(CampaignLookup.build, data)
                self.loads += 1
            return slot.lookup

    def changed(self, event: notify.MemoryChanged) -> None:
        """A campaign's memory changed: reload its copy before next use, if names did."""
        if not event.names_changed:
            return
        for (_, campaign_id), slot in self._slots.items():
            if campaign_id == event.campaign_id and (
                slot.lookup is None or event.version > slot.lookup.version
            ):
                slot.stale = True

    def forget(self, campaign_ids: Iterable[str] | None = None) -> None:
        """Drop copies (all of them by default): a session ended, or notifications may
        have been missed."""
        wanted = None if campaign_ids is None else set(campaign_ids)
        for (_, campaign_id), slot in self._slots.items():
            if wanted is None or campaign_id in wanted:
                slot.stale = True

    async def follow(self, listen: Listen) -> None:
        """Apply change notifications until cancelled. `listen(channel)` yields payloads;
        when it fails, every copy is reloaded (changes may have been missed) and it's
        opened again, waiting a little longer each time."""
        failures = 0
        while True:
            try:
                # Closed straight away when cancelled, so its connection goes back to
                # the pool.
                async with contextlib.aclosing(listen(notify.CHANNEL)) as stream:
                    async for raw in stream:
                        failures = 0
                        event = notify.parse(raw)
                        if event is not None:
                            self.changed(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the connection dropped; carry on without crashing
                log.warning("Lost campaign memory updates (%s); reloading names", exc)
            self.forget()
            delay = RECONNECT_DELAY_S[min(failures, len(RECONNECT_DELAY_S) - 1)]
            failures += 1
            await asyncio.sleep(delay)
