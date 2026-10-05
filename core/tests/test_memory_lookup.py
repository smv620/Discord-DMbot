"""Sound codes and the in-memory name lookup, without a database."""

import asyncio
import unittest
from collections.abc import AsyncGenerator
from unittest import mock

from dmbot.devtools.stt_bakeoff.data import NAMES
from dmbot.memory import notify
from dmbot.memory.lookup import CampaignLookup, LookupCache, LookupData
from dmbot.memory.models import (
    CONFIRMED,
    FIX,
    KEEP,
    PROPOSED,
    Alias,
    Correction,
    Entity,
    Relation,
    name_key,
)
from dmbot.memory.sounds import sound_codes, word_runs

BELLEROS, CERRIC, KAEL = "a" * 32, "b" * 32, "c" * 32


class SoundCodes(unittest.TestCase):
    def test_every_bakeoff_spelling_meets_its_name(self) -> None:
        # The owner's own "sounds like" list for the speech-to-text bake-off (#128).
        for name in NAMES:
            base = set(sound_codes(name.canonical))
            for heard in (*name.variants, *name.sounds_like):
                with self.subTest(name=name.canonical, heard=heard):
                    self.assertTrue(base & set(sound_codes(heard)))

    def test_examples(self) -> None:
        self.assertEqual(sound_codes("Belleros"), ("PLRS",))
        self.assertEqual(sound_codes("bell or us"), ("PLRS",))
        self.assertEqual(sound_codes("Hrothgar"), sound_codes("Rothgar"))
        self.assertEqual(sound_codes("Knight"), ("NT",))
        self.assertEqual(sound_codes("Bob"), ("PP",))  # a vowel between keeps both
        self.assertEqual(sound_codes("Thomas"), ("0MS", "TMS"))  # two ways to say "th"
        self.assertEqual(sound_codes("Saelith"), ("SL0",))  # inside a name: only one
        self.assertEqual(sound_codes("Éowyn"), sound_codes("Eowyn"))
        self.assertEqual(sound_codes("?!"), ())

    def test_different_names_stay_apart(self) -> None:
        codes = {n.canonical: set(sound_codes(n.canonical)) for n in NAMES if not n.is_word}
        for a, ca in codes.items():
            for b, cb in codes.items():
                if a < b:
                    self.assertFalse(ca & cb, f"{a} and {b}")

    def test_word_runs_join_split_names(self) -> None:
        runs = word_runs(["bell", "or", "us", "now"])
        self.assertIn((0, 3, "bellorus"), runs)
        self.assertNotIn((0, 4, "bellorusnow"), runs)  # at most 3 words
        self.assertEqual(len(runs), 4 + 3 + 2)


def entity(eid: str, name: str, status: str = CONFIRMED) -> Entity:
    return Entity(eid, "npc", name, "", status, None, "dm", 0)


def alias(
    eid: str, text: str, *, status: str = CONFIRMED, secret: bool = False, aid: str = ""
) -> Alias:
    aid = aid or (eid[:16] + name_key(text).replace(" ", "").ljust(16, "0")[:16])
    return Alias(aid, eid, text, name_key(text), "full", None, secret, status, (), "dm", 0)


def data(version: int = 1, **kw: object) -> LookupData:
    base: dict[str, object] = {
        "entities": (entity(BELLEROS, "Belleros"), entity(CERRIC, "Cerric"),
                     entity(KAEL, "Kael", PROPOSED)),
        "aliases": (alias(BELLEROS, "Belleros"), alias(CERRIC, "Cerric"),
                    alias(KAEL, "Kael", status=PROPOSED),
                    alias(BELLEROS, "the hooded stranger", secret=True)),
        "corrections": (),
        "relations": (),
    }  # fmt: skip
    base.update(kw)
    return LookupData(version, **base)  # type: ignore[arg-type]


class Lookup(unittest.TestCase):
    def test_exact_and_sounds_like(self) -> None:
        lookup = CampaignLookup.build(data())
        self.assertEqual([e.entity_id for e in lookup.exact("BELLEROS")], [BELLEROS])
        self.assertEqual([e.text for e in lookup.sounds_like("bell or us")], ["Belleros"])
        self.assertEqual(lookup.sounds_like("nothing like it"), ())

    def test_only_confirmed_names_are_marked_confirmed(self) -> None:
        lookup = CampaignLookup.build(data())
        self.assertTrue(lookup.exact("Cerric")[0].confirmed)
        self.assertFalse(lookup.exact("Kael")[0].confirmed)
        # A confirmed alias of a proposed entity isn't enough either.
        mixed = data(entities=(entity(BELLEROS, "Belleros", PROPOSED),),
                     aliases=(alias(BELLEROS, "Belleros"),))  # fmt: skip
        self.assertFalse(CampaignLookup.build(mixed).exact("Belleros")[0].confirmed)

    def test_secret_names_are_known_but_never_offered_as_fixes(self) -> None:
        lookup = CampaignLookup.build(data())
        self.assertTrue(lookup.exact("the hooded stranger")[0].secret)
        self.assertEqual(lookup.sounds_like("the hooded stranger"), ())

    def test_keep_as_heard_beats_a_fix(self) -> None:
        corrections = (
            Correction("1" * 32, "Bell or us", "bell or us", BELLEROS, FIX, "dm", 0),
            Correction("2" * 32, "kale", "kale", KAEL, FIX, "dm", 0),
            Correction("3" * 32, "Kale", "kale", None, KEEP, "dm", 0),
        )
        lookup = CampaignLookup.build(data(corrections=corrections))
        self.assertEqual(lookup.fixes_for("bell or us"), (BELLEROS,))
        self.assertTrue(lookup.keep_as_heard("KALE"))
        self.assertEqual(lookup.fixes_for("kale"), ())

    def test_related_entries(self) -> None:
        rel = Relation("9" * 32, BELLEROS, "ally_of", CERRIC, "", 1.0, CONFIRMED, "dm", (),
                       None, None, None, None, False, 0)  # fmt: skip
        lookup = CampaignLookup.build(data(relations=(rel,)))
        self.assertEqual(lookup.related(CERRIC), frozenset({BELLEROS}))
        self.assertEqual(lookup.related(KAEL), frozenset())


class Notifications(unittest.TestCase):
    def test_payload_round_trip_has_no_names(self) -> None:
        raw = notify.payload("f" * 32, 12, True)
        self.assertEqual(raw, "f" * 32 + ":12:1")
        self.assertEqual(notify.parse(raw), notify.MemoryChanged("f" * 32, 12, True))
        for bad in ("", "x:y:1", "a:1", "a:1:2"):
            self.assertIsNone(notify.parse(bad))


class FakeSource:
    def __init__(self) -> None:
        self.version = 1
        self.calls = 0
        self.gate: asyncio.Event | None = None

    async def lookup_data(self, guild_id: int, campaign_id: str) -> LookupData:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return data(self.version)


class Cache(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.source = FakeSource()
        self.cache = LookupCache(self.source)

    async def test_loads_once_until_names_change(self) -> None:
        first = await self.cache.get(1, "camp")
        self.assertIs(await self.cache.get(1, "camp"), first)
        self.cache.changed(notify.MemoryChanged("camp", 2, names_changed=False))  # mentions
        self.cache.changed(notify.MemoryChanged("other", 9, names_changed=True))
        self.cache.changed(notify.MemoryChanged("camp", 1, names_changed=True))  # not newer
        self.assertIs(await self.cache.get(1, "camp"), first)
        self.source.version = 2
        self.cache.changed(notify.MemoryChanged("camp", 2, names_changed=True))
        second = await self.cache.get(1, "camp")
        self.assertEqual((second.version, self.source.calls), (2, 2))

    async def test_servers_and_campaigns_have_their_own_copies(self) -> None:
        await self.cache.get(1, "camp")
        await self.cache.get(2, "camp")
        await self.cache.get(1, "other")
        self.assertEqual(self.source.calls, 3)

    async def test_many_readers_share_one_load(self) -> None:
        self.source.gate = asyncio.Event()
        readers = [asyncio.create_task(self.cache.get(1, "camp")) for _ in range(5)]
        await asyncio.sleep(0)
        self.source.gate.set()
        await asyncio.gather(*readers)
        self.assertEqual(self.source.calls, 1)

    async def test_a_change_during_a_load_is_not_lost(self) -> None:
        self.source.gate = asyncio.Event()
        reader = asyncio.create_task(self.cache.get(1, "camp"))
        await asyncio.sleep(0)
        self.cache.changed(notify.MemoryChanged("camp", 2, names_changed=True))
        self.source.gate.set()
        await reader
        self.source.gate = None
        await self.cache.get(1, "camp")
        self.assertEqual(self.source.calls, 2)

    async def test_follow_applies_changes_and_reloads_after_a_dropped_connection(self) -> None:
        await self.cache.get(1, "camp")
        opened = 0
        reconnected = asyncio.Event()

        async def listen(channel: str) -> AsyncGenerator[str, None]:
            nonlocal opened
            opened += 1
            self.assertEqual(channel, notify.CHANNEL)
            if opened == 1:
                yield notify.payload("camp", 1, True)  # not newer: ignored
                yield "junk"
                raise ConnectionError("server went away")
            reconnected.set()
            await asyncio.Event().wait()  # then quiet
            yield ""  # never reached

        with (
            mock.patch("dmbot.memory.lookup.asyncio.sleep", new=mock.AsyncMock()),
            self.assertLogs("dmbot.memory.lookup", "WARNING"),
        ):
            task = asyncio.create_task(self.cache.follow(listen))
            await asyncio.wait_for(reconnected.wait(), 5)
            task.cancel()
        await self.cache.get(1, "camp")
        self.assertEqual(self.source.calls, 2)  # reloaded: changes may have been missed
