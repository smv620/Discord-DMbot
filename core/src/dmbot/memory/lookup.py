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
import dataclasses
import logging
import time
from collections import defaultdict
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from dmbot.memory import notify
from dmbot.memory.models import (
    CONFIRMED,
    DM_ONLY_TYPES,
    FIX,
    KEEP,
    Alias,
    Correction,
    Entity,
    HeardCount,
    Relation,
    RuleLink,
    name_key,
)
from dmbot.memory.ontology import is_dm_word
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
    # How often and when names were said in past sessions (stored mentions), and when
    # the last two sessions with any started: for ranking hints only.
    heard: tuple[HeardCount, ...] = ()
    recent_sessions: tuple[int, ...] = ()
    links: tuple[RuleLink, ...] = ()  # characters' links to the rules (#1034)
    # The table's people (#1208): the names the DM confirmed for each person (never their
    # placeholder). Kept apart from `entities`, so no list, card or search ever shows a
    # person as a story name; only the Cleaner reads them.
    people: tuple[Alias, ...] = ()
    # The DM's secret connections, kept apart from `relations` (which no one but a DM may see):
    # only the name dropdown's "closest first" order reads them, and only for a DM (#2321).
    secret_relations: tuple[Relation, ...] = ()


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
    person: bool = False  # a name of someone at the table, not of a story character
    said_as: str | None = None  # the DM's spelling for the recap's narrator (#1419)
    # `secret` only because its entry is "Players don't know this one yet" (#2321); the name itself
    # is not a secret one. A name a DM adds starts so (#2404), and a new spelling joined to it stays
    # hidden with it, so a DM's list may still match it and the card calls it an ordinary name.
    entry_hidden: bool = False


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
    # Only links the DM confirmed: what scene hints follow (docs/PLAN.md).
    confirmed_neighbours: dict[str, frozenset[str]]
    heard: dict[str, HeardCount] = field(default_factory=dict)
    recent_sessions: tuple[int, ...] = ()
    links: dict[str, tuple[RuleLink, ...]] = field(default_factory=dict)  # entity → its links
    # Every secret name's word count: all of one is left alone, and the Cleaner checks
    # only runs of about those lengths.
    secret_lengths: frozenset[int] = frozenset()
    # Secret names with no sound codes (not in Latin letters): compared by spelling.
    codeless_secrets: tuple[str, ...] = ()
    # The names of people at the table, by spelling and by sound (#1208). The Cleaner may
    # fix the spelling of one; it never maps a word between a person and a character.
    people_by_key: dict[str, tuple[NameEntry, ...]] = field(default_factory=dict)
    people_by_sound: dict[str, tuple[NameEntry, ...]] = field(default_factory=dict)
    # Who is joined to whom by a confirmed secret connection: a DM's view only (`for_viewer`
    # drops it for anyone else), so an order built from it can never hint at a secret.
    secret_neighbours: dict[str, frozenset[str]] = field(default_factory=dict)

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
                # A clue only some people know (#1649), or an entry the DM hasn't told the players
                # of (#2321), is as secret as a secret name: the Cleaner must never write it into
                # the shared transcript, and it is the DM's alone.
                a.secret or entities[a.entity_id].unseen_by_players,
                a.status == CONFIRMED and entities[a.entity_id].status == CONFIRMED,
                sound_codes(a.text),  # the same function as for heard words
                False,
                a.said_as,
                not a.secret and entities[a.entity_id].hidden_from_players,
            )
            for a in data.aliases
            if a.entity_id in entities and not is_dm_word(a.text)
        )  # (the four DM words are refused as names, but an old row must never be a target, #1547)
        people_by_key: dict[str, list[NameEntry]] = defaultdict(list)
        people_by_sound: dict[str, list[NameEntry]] = defaultdict(list)
        for a in data.people:
            entry = NameEntry(
                a.id, a.entity_id, a.text, a.key, a.kind, False, True, sound_codes(a.text), True
            )
            people_by_key[entry.key].append(entry)
            for code in entry.codes:
                people_by_sound[code].append(entry)
        by_key: dict[str, list[NameEntry]] = defaultdict(list)
        by_sound: dict[str, list[NameEntry]] = defaultdict(list)
        for entry in names:
            by_key[entry.key].append(entry)
            for code in entry.codes:
                by_sound[code].append(entry)
        secret_lengths = {len(entry.key.split()) for entry in names if entry.secret}
        fixes: dict[str, list[str]] = defaultdict(list)
        # A secret alias is never rewritten, even if a "change to" rule says so: that
        # would put the real name over "the hooded stranger".
        keep: set[str] = {entry.key for entry in names if entry.secret}
        for c in data.corrections:
            if c.action == KEEP:
                keep.add(c.heard_key)
            elif c.action == FIX and c.entity_id in entities:
                fixes[c.heard_key].append(c.entity_id)
        neighbours: dict[str, set[str]] = defaultdict(set)
        confirmed_links: dict[str, set[str]] = defaultdict(set)
        for r in data.relations:
            if r.subject_id in entities and r.object_id in entities:
                neighbours[r.subject_id].add(r.object_id)
                neighbours[r.object_id].add(r.subject_id)
                if r.status == CONFIRMED and not r.secret:
                    confirmed_links[r.subject_id].add(r.object_id)
                    confirmed_links[r.object_id].add(r.subject_id)
        hidden_links: dict[str, set[str]] = defaultdict(set)
        for r in data.secret_relations:
            if r.subject_id in entities and r.object_id in entities and r.status == CONFIRMED:
                hidden_links[r.subject_id].add(r.object_id)
                hidden_links[r.object_id].add(r.subject_id)
        links_by: dict[str, list[RuleLink]] = defaultdict(list)
        for link in data.links:
            if link.entity_id in entities:
                links_by[link.entity_id].append(link)
        return cls(
            data.version,
            entities,
            names,
            {k: tuple(v) for k, v in by_key.items()},
            {k: tuple(v) for k, v in by_sound.items()},
            frozenset(keep),
            {k: tuple(v) for k, v in fixes.items() if k not in keep},  # keep wins
            {k: frozenset(v) for k, v in neighbours.items()},
            {k: frozenset(v) for k, v in confirmed_links.items()},
            {h.entity_id: h for h in data.heard if h.entity_id in entities},
            data.recent_sessions,
            {k: tuple(v) for k, v in links_by.items()},
            frozenset(secret_lengths),
            tuple(sorted({e.key for e in names if e.secret and not e.codes})),
            {k: tuple(v) for k, v in people_by_key.items()},
            {k: tuple(v) for k, v in people_by_sound.items()},
            {k: frozenset(v) for k, v in hidden_links.items()},
        )

    @property
    def hidden(self) -> frozenset[str]:
        """What the campaign's DMs alone may see: the clues only some people know (#1649), the
        entries the DM marked "Players don't know yet" (#2321), and the story threads (#1662)
        and deeds (#1732), which are the DM's plot notes."""
        return frozenset(
            e.id for e in self.entities.values() if e.unseen_by_players or e.type in DM_ONLY_TYPES
        )

    def for_viewer(self, secrets: bool) -> CampaignLookup:
        """What a person who may not see secrets (a server manager who is not a DM) gets to
        read: the same names without the DM-only entries (`hidden`), in every list, search,
        card and file. The campaign's DMs, and the Cleaner, get the whole thing."""
        if secrets:
            return self
        gone = self.hidden
        if not gone and not self.secret_neighbours:
            return self

        def keep(entries: tuple[NameEntry, ...]) -> tuple[NameEntry, ...]:
            return tuple(e for e in entries if e.entity_id not in gone)

        return dataclasses.replace(
            self,
            secret_neighbours={},  # a DM's view of secret connections: nobody else's
            entities={k: v for k, v in self.entities.items() if k not in gone},
            names=keep(self.names),
            by_key={k: v for k, v in ((k, keep(v)) for k, v in self.by_key.items()) if v},
            by_sound={k: v for k, v in ((k, keep(v)) for k, v in self.by_sound.items()) if v},
            neighbours={k: v - gone for k, v in self.neighbours.items() if k not in gone},
            confirmed_neighbours={
                k: v - gone for k, v in self.confirmed_neighbours.items() if k not in gone
            },
            heard={k: v for k, v in self.heard.items() if k not in gone},
            links={k: v for k, v in self.links.items() if k not in gone},
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

    def linked(self, entity_id: str) -> frozenset[str]:
        """Entries the DM confirmed are linked to this one."""
        return self.confirmed_neighbours.get(entity_id, frozenset())


class LookupSource(Protocol):
    async def lookup_data(self, guild_id: int, campaign_id: str) -> LookupData: ...


# listen(channel, on_listening) yields notification payloads; it calls on_listening
# once it's listening (see Database.listen).
Listen = Callable[[str, Callable[[], None]], AsyncGenerator[str, None]]
Sleep = Callable[[float], Awaitable[None]]
# A bound, so a DM editing names in a burst can't keep a reader loading forever.
_LOADS_PER_ASK = 3
_clock = time.monotonic  # tests move it on
FAIL_LOG_S = 60.0  # a load that keeps failing (database away) is logged once a minute
# A copy nobody has asked for in this long, for a campaign with no table running, is dropped
# (#1883). 30 minutes: well past a break in a session or a DM tidying names between sessions (a
# copy is rebuilt in well under a second for a normal campaign, so being dropped early costs a
# little time, not correctness), yet short enough that a bot serving searches for hundreds of
# campaigns keeps only the recently used ones.
IDLE_EVICT_S = 30 * 60.0
# How often the idle sweep may run. It walks every copy, so it is done at most once a minute.
SWEEP_EVERY_S = 60.0


class NamesKeepChanging(RuntimeError):
    """The names changed during every load: no copy known to be current."""


def _never_in_use(guild_id: int, campaign_id: str) -> bool:
    return False


@dataclass(slots=True)
class _Slot:
    lookup: CampaignLookup | None = None
    stale: bool = True
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # When this copy was last handed out (`_clock`), for the idle sweep (#1883).
    used_at: float = field(default_factory=lambda: _clock())

    def current(self) -> CampaignLookup | None:
        """The copy if it's current, with no reload under way: while one is, the old copy
        may hold a name the DM has just made secret, so readers wait for the new one."""
        return None if self.stale or self.lock.locked() else self.lookup


class LookupCache:
    """One `CampaignLookup` per (server, campaign), reloaded when its memory changes.

    Run `follow` for as long as the cache is used: without it, copies never notice
    changes.
    """

    def __init__(
        self,
        source: LookupSource,
        *,
        sleep: Sleep = asyncio.sleep,
        in_use: Callable[[int, str], bool] = _never_in_use,
    ) -> None:
        self._source = source
        self._sleep = sleep
        # True while the campaign has a table running (the bot passes it): such a copy is kept
        # however long it has sat unused, because the next clip asks for it at once.
        self._in_use = in_use
        self._swept_at = _clock()
        self._slots: dict[tuple[int, str], _Slot] = {}
        # Loads that carry on after whoever asked stopped waiting (`get_within`).
        self._loading: dict[tuple[int, str], asyncio.Task[CampaignLookup]] = {}
        self._failed_at: dict[tuple[int, str], float] = {}

    async def get(self, guild_id: int, campaign_id: str) -> CampaignLookup:
        """The campaign's lookup, loading it first if it's missing or out of date."""
        self._sweep()
        slot = self._slots.setdefault((guild_id, campaign_id), _Slot())
        slot.used_at = _clock()
        if (ready := slot.current()) is not None:
            return ready
        asked = time.perf_counter()
        async with slot.lock:  # one load at a time per campaign
            # A change during a load (the DM made a name secret) means load again: the
            # copy just built may be from before it. A few times at most; if changes
            # still keep coming, no copy is given at all (callers treat it as a failed
            # load) and the next reader loads again.
            for _ in range(_LOADS_PER_ASK):
                if slot.lookup is not None and not slot.stale:
                    break
                slot.stale = False  # a change arriving during the load marks it again
                started = time.perf_counter()
                try:
                    data = await self._source.lookup_data(guild_id, campaign_id)
                    loaded = time.perf_counter()
                    # Off the event loop's own turn, so voice keeps flowing while a big
                    # campaign is indexed.
                    slot.lookup = await asyncio.to_thread(CampaignLookup.build, data)
                except BaseException:
                    # Never keep serving the old copy as if it were current: a missed
                    # change could be a name the DM just made secret.
                    slot.stale = True
                    raise
                built = time.perf_counter()
                log.info(  # how long a menu waits on a cold copy, and on what (#537)
                    "Built names for campaign %s in %d ms (waited %d, database %d, "
                    "index %d; %d names)",
                    campaign_id,
                    (built - asked) * 1000,
                    (started - asked) * 1000,
                    (loaded - started) * 1000,
                    (built - loaded) * 1000,
                    len(slot.lookup.entities),
                )
            if slot.lookup is None or slot.stale:
                raise NamesKeepChanging(f"names changed during {_LOADS_PER_ASK} loads")
            slot.used_at = _clock()
            return slot.lookup

    async def get_within(
        self, guild_id: int, campaign_id: str, seconds: float
    ) -> CampaignLookup | None:
        """The campaign's lookup if it's ready within `seconds`, else None (also if the
        load failed: that's logged once, here). The load carries on either way, so the
        next ask finds it ready: for type-ahead, which must answer Discord within 3
        seconds and can't say "loading" (#581)."""
        self._sweep()
        key = (guild_id, campaign_id)
        slot = self._slots.get(key)
        if slot is not None and (ready := slot.current()) is not None:
            slot.used_at = _clock()
            return ready
        task = self._loading.get(key)
        if task is None:
            task = asyncio.create_task(self.get(guild_id, campaign_id), name="names copy")
            self._loading[key] = task
            task.add_done_callback(lambda done: self._loaded(key, done))
        try:
            await asyncio.wait_for(asyncio.shield(task), seconds)
        except TimeoutError:
            return None
        except Exception:  # logged by _loaded, for everyone who waited
            return None
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if not task.cancelled() or (current is not None and current.cancelling()):
                raise  # this caller was cancelled
            return None  # only the load was (drop(): the campaign isn't wanted any more)
        # From the slot, not the load's result: a change may have landed between the load
        # finishing and this waiter resuming (a name the DM just made secret).
        slot = self._slots.get(key)
        if slot is None:
            return None
        slot.used_at = _clock()
        return slot.current()

    def _sweep(self) -> None:
        """Drop copies nobody has used for `IDLE_EVICT_S` whose campaign has no table running
        (#1883). Run from `get` and `get_within`, at most once a `SWEEP_EVERY_S`, not from
        `follow`: a copy only ever appears through those two, so sweeping where they are called
        bounds the memory exactly as often as it can grow, needs no timer of its own, and works
        in a cache nobody runs `follow` for. A copy being loaded or reloaded (its lock is held,
        or a `get_within` load is still going) is never dropped. An evicted campaign is simply
        loaded again on its next `get`, as on first use."""
        now = _clock()
        if now - self._swept_at < SWEEP_EVERY_S:
            return
        self._swept_at = now
        for key, slot in list(self._slots.items()):
            if (
                now - slot.used_at >= IDLE_EVICT_S
                and not slot.lock.locked()
                and key not in self._loading
                and not self._is_in_use(key)
            ):
                del self._slots[key]
                self._failed_at.pop(key, None)  # as in `drop`: a later load starts afresh

    def _is_in_use(self, key: tuple[int, str]) -> bool:
        """Ask the bot whether the campaign has a table. If that raises, say it is in use: a
        sweep must never break a names lookup, and keeping a copy is the safe answer."""
        try:
            return self._in_use(*key)
        except Exception:
            log.exception("Couldn't tell whether campaign %s is in use; keeping its names", key[1])
            return True

    def _loaded(self, key: tuple[int, str], task: asyncio.Task[CampaignLookup]) -> None:
        if self._loading.get(key) is task:
            del self._loading[key]
        if not task.cancelled() and (error := task.exception()) is not None:
            # Said here, whoever was waiting (the next ask tries again); at most once a
            # minute per campaign, as every clip of a session asks.
            now = _clock()
            if now - self._failed_at.get(key, -FAIL_LOG_S) >= FAIL_LOG_S:
                self._failed_at[key] = now
                log.warning("Couldn't load names for campaign %s: %r", key[1], error)

    def changed(self, event: notify.MemoryChanged) -> None:
        """A campaign's memory changed: reload its copy before next use, if names did."""
        if not event.names_changed:
            return
        for (_, campaign_id), slot in self._slots.items():
            if campaign_id == event.campaign_id and (
                slot.lookup is None or event.version > slot.lookup.version
            ):
                slot.stale = True

    def mark_stale(self, guild_id: int, campaign_id: str) -> None:
        """DMbot itself just changed this campaign's names: reload before next use,
        without waiting for the change notification."""
        slot = self._slots.get((guild_id, campaign_id))
        if slot is not None:
            slot.stale = True

    def mark_all_stale(self) -> None:
        """Changes may have been missed: reload every copy before its next use."""
        for slot in self._slots.values():
            slot.stale = True

    def drop(self, campaign_ids: Iterable[str]) -> None:
        """Free copies that aren't needed any more (a session ended, a campaign was
        deleted)."""
        gone = set(campaign_ids)
        for key in [k for k in self._slots if k[1] in gone]:
            del self._slots[key]
            self._failed_at.pop(key, None)  # a campaign restored with this id starts afresh
        for key in [k for k in self._loading if k[1] in gone]:
            self._loading.pop(key).cancel()  # a deleted campaign's names: not wanted

    async def follow(self, listen: Listen) -> None:
        """Apply change notifications until cancelled. Every time the listener is
        (re)connected, every copy is reloaded, because changes committed while it wasn't
        listening were never announced. If it fails, it's opened again, waiting a little
        longer each time it fails without ever connecting."""
        failures = 0

        def on_listening() -> None:
            nonlocal failures
            failures = 0
            self.mark_all_stale()

        while True:
            try:
                # Closed straight away when cancelled, so its connection closes too.
                async with contextlib.aclosing(listen(notify.CHANNEL, on_listening)) as stream:
                    async for raw in stream:
                        event = notify.parse(raw)
                        if event is not None:
                            self.changed(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the connection dropped; carry on without crashing
                log.warning("Lost campaign memory updates (%s); will reload names", exc)
            self.mark_all_stale()
            delay = RECONNECT_DELAY_S[min(failures, len(RECONNECT_DELAY_S) - 1)]
            failures += 1
            await self._sleep(delay)
