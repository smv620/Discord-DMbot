"""Sound codes and the in-memory name lookup, without a database."""

import asyncio
import contextlib
import unittest
from collections.abc import AsyncGenerator, Callable

from dmbot.devtools.stt_bakeoff.data import NAMES
from dmbot.memory import notify
from dmbot.memory.lookup import CampaignLookup, LookupCache, LookupData, NamesKeepChanging
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
        self.assertEqual(sound_codes("Tiamat"), sound_codes("tea a mat"))
        for a, b in (
            ("Hal", "Al"),
            ("Sapphire", "Safire"),
            ("Matthew", "Mathew"),
            ("Bacchus", "Backus"),
            ("McCall", "Macall"),
            ("Þorin", "Thorin"),
            ("Æthelred", "Aethelred"),
            ("Łukasz", "Lukasz"),
            ("Accent", "aksent"),
        ):
            with self.subTest(a=a, b=b):
                self.assertTrue(set(sound_codes(a)) & set(sound_codes(b)))
        self.assertTrue(set(sound_codes("Gemaya")) & set(sound_codes("Jemaia")))

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

    def test_a_secret_name_is_never_rewritten(self) -> None:
        fix = Correction(
            "1" * 32, "the hooded stranger", "the hooded stranger", BELLEROS, FIX, "dm", 0
        )
        lookup = CampaignLookup.build(data(corrections=(fix,)))
        self.assertTrue(lookup.keep_as_heard("The Hooded Stranger"))
        self.assertEqual(lookup.fixes_for("the hooded stranger"), ())

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
        for bad in ("", "x:y:1", "a:1", "a:1:2", "f" * 32 + ":²:1", "camp:1:1"):
            self.assertIsNone(notify.parse(bad))


class FakeSource:
    def __init__(self) -> None:
        self.version = 1
        self.calls = 0
        self.fail = False
        self.gate: asyncio.Event | None = None

    async def lookup_data(self, guild_id: int, campaign_id: str) -> LookupData:
        self.calls += 1
        if self.fail:
            raise ConnectionError("database away")
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

    async def test_each_build_is_logged_with_its_time_once(self) -> None:
        with self.assertLogs("dmbot.memory.lookup", "INFO") as logs:
            await self.cache.get(1, "camp")
            await self.cache.get(1, "camp")  # from the copy: not built again
        (line,) = logs.output
        self.assertRegex(
            line,
            r"Built names for campaign camp in \d+ ms "
            r"\(waited \d+, database \d+, index \d+; \d+ names\)",
        )

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

    async def test_nobody_gets_the_old_copy_while_it_reloads(self) -> None:
        # The old copy may hold a name the DM just made secret: wait for the new one.
        await self.cache.get(1, "camp")
        self.source.version = 2
        self.cache.changed(notify.MemoryChanged("camp", 2, names_changed=True))
        self.source.gate = asyncio.Event()
        loading = asyncio.create_task(self.cache.get(1, "camp"))
        await asyncio.sleep(0)
        reader = asyncio.create_task(self.cache.get(1, "camp"))
        await asyncio.sleep(0)
        self.assertFalse(reader.done())
        self.source.gate.set()
        self.assertEqual([(await t).version for t in (loading, reader)], [2, 2])
        self.assertEqual(self.source.calls, 2)

    async def test_a_failed_reload_is_tried_again(self) -> None:
        await self.cache.get(1, "camp")
        self.cache.changed(notify.MemoryChanged("camp", 2, names_changed=True))
        self.source.fail = True
        with self.assertRaises(ConnectionError):
            await self.cache.get(1, "camp")
        self.source.fail = False
        self.source.version = 2
        self.assertEqual((await self.cache.get(1, "camp")).version, 2)  # not the old copy

    async def loads_done(self) -> None:
        """Let loads nobody waits for any more finish."""
        await asyncio.gather(*self.cache._loading.values(), return_exceptions=True)

    async def test_type_ahead_waits_a_little_and_the_load_carries_on(self) -> None:
        # #581: a copy ready in time is used; one that isn't gives None at once, and the
        # load goes on so the next keystroke finds it ready.
        first = await self.cache.get_within(1, "camp", 1.0)
        self.assertEqual(first and first.version, 1)
        self.source.version = 2
        self.cache.changed(notify.MemoryChanged("camp", 2, names_changed=True))
        self.source.gate = asyncio.Event()
        self.assertIsNone(await self.cache.get_within(1, "camp", 0.01))  # never the old one
        self.assertIsNone(await self.cache.get_within(1, "camp", 0.01))  # same load
        self.source.gate.set()
        await self.loads_done()
        ready = await self.cache.get_within(1, "camp", 0)
        self.assertEqual((ready and ready.version, self.source.calls), (2, 2))

    async def test_a_change_during_a_load_is_loaded_too(self) -> None:
        # The load read the names, then the DM made one secret: whoever gets the copy,
        # by get or by joining the type-ahead's load, gets one from after the change.
        read: list[int] = []

        async def lookup_data(guild_id: int, campaign_id: str) -> LookupData:
            read.append(self.source.version)  # what the database said, before the wait
            if self.source.gate is not None:
                await self.source.gate.wait()
            return data(read[-1])

        self.source.lookup_data = lookup_data  # type: ignore[method-assign]
        self.source.gate = asyncio.Event()
        self.assertIsNone(await self.cache.get_within(1, "camp", 0))
        await asyncio.sleep(0)
        self.source.version = 2
        self.cache.mark_stale(1, "camp")
        joined = asyncio.create_task(self.cache.get_within(1, "camp", 5.0))
        plain = asyncio.create_task(self.cache.get(1, "camp"))
        self.source.gate.set()
        got = [await joined, await plain]
        self.assertEqual([g and g.version for g in got], [2, 2])
        self.assertEqual(read, [1, 2])

    async def test_a_failed_load_is_logged_once_and_tried_again(self) -> None:
        self.source.fail = True
        with self.assertLogs("dmbot.memory.lookup", "WARNING") as logs:
            self.assertIsNone(await self.cache.get_within(1, "camp", 1.0))  # waited
            self.assertIsNone(await self.cache.get_within(1, "camp", 0))  # didn't
            await self.loads_done()
        self.assertEqual(len(logs.output), 1)  # once, waited for or not (and once a minute)
        self.assertIn("Couldn't load names for campaign camp", logs.output[0])
        self.source.fail = False
        again = await self.cache.get_within(1, "camp", 1.0)
        self.assertEqual(again and again.version, 1)

    async def test_names_that_keep_changing_never_give_an_old_copy(self) -> None:
        # A change during every load: no copy at all, rather than one from before the last
        # change (it may hold a name just made secret).
        async def lookup_data(guild_id: int, campaign_id: str) -> LookupData:
            self.source.calls += 1
            version = self.source.calls
            self.cache.mark_stale(1, "camp")  # the DM changed a name meanwhile
            return data(version)

        self.source.lookup_data = lookup_data  # type: ignore[method-assign]
        with self.assertRaises(NamesKeepChanging):
            await self.cache.get(1, "camp")
        self.assertEqual(self.source.calls, 3)
        with self.assertLogs("dmbot.memory.lookup", "WARNING"):
            self.assertIsNone(await self.cache.get_within(1, "camp", 1.0))

    async def test_a_type_ahead_waiting_on_a_dropped_campaign_gets_nothing(self) -> None:
        self.source.gate = asyncio.Event()
        waiting = asyncio.create_task(self.cache.get_within(1, "camp", 5.0))
        await asyncio.sleep(0)
        self.cache.drop(["camp"])  # the session ended, or the campaign was deleted
        self.assertIsNone(await waiting)  # never a CancelledError for the type-ahead

    async def test_a_load_that_keeps_failing_is_logged_once_a_minute(self) -> None:
        self.source.fail = True
        with self.assertLogs("dmbot.memory.lookup", "WARNING") as logs:
            for _ in range(5):  # every clip of a session asks
                self.assertIsNone(await self.cache.get_within(1, "camp", 1.0))
        self.assertEqual(len(logs.output), 1)

    async def test_dropping_a_campaign_stops_its_load(self) -> None:
        self.source.gate = asyncio.Event()
        self.assertIsNone(await self.cache.get_within(1, "camp", 0))
        (task,) = self.cache._loading.values()
        self.cache.drop(["camp"])
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(task.cancelled())
        self.assertEqual(self.cache._loading, {})

    async def test_drop_frees_copies(self) -> None:
        await self.cache.get(1, "camp")
        await self.cache.get(1, "other")
        self.cache.drop(["camp"])
        await self.cache.get(1, "camp")
        await self.cache.get(1, "other")
        self.assertEqual(self.source.calls, 3)

    async def test_follow_reloads_on_every_connect_and_applies_changes(self) -> None:
        camp = "d" * 32
        await self.cache.get(1, camp)
        connected = [asyncio.Event(), asyncio.Event()]
        changed = asyncio.Event()
        opened = 0
        sleeps: list[float] = []

        async def sleep(delay: float) -> None:
            sleeps.append(delay)

        async def listen(
            channel: str, on_listening: Callable[[], None]
        ) -> AsyncGenerator[str, None]:
            nonlocal opened
            opened += 1
            self.assertEqual(channel, notify.CHANNEL)
            on_listening()
            connected[opened - 1].set()
            if opened == 1:
                yield "junk"
                raise ConnectionError("server went away")
            await changed.wait()
            yield notify.payload(camp, 99, True)
            await asyncio.Event().wait()  # then quiet

        cache = LookupCache(self.source, sleep=sleep)
        await cache.get(1, camp)
        with self.assertLogs("dmbot.memory.lookup", "WARNING"):
            task = asyncio.create_task(cache.follow(listen))
            await asyncio.wait_for(connected[1].wait(), 5)
        await cache.get(1, camp)  # reloaded: changes may have been missed while away
        calls = self.source.calls
        await cache.get(1, camp)
        self.assertEqual(self.source.calls, calls)
        changed.set()
        for _ in range(100):
            await asyncio.sleep(0)
        await cache.get(1, camp)
        self.assertEqual(self.source.calls, calls + 1)  # the change was applied
        self.assertEqual(sleeps, [1.0])  # reconnect waited, then reset once connected
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
