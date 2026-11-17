"""A region's listed towns (#4636 part 3, Supervisor's ruling, the keys' one exception to
"inferred = offered, never extracted"): a worked-out `in` from a place to a region, rated sure by
the reader, from a list naming at least three of the region's places, for a place with no other
parent, is kept by an upload's sure step. "Lives in", roads, a guess, a list of two, a place with
another parent and a near-miss stay questions. Invented names."""

from __future__ import annotations

import json
import unittest
from typing import Any

from dmbot.campaigns import CampaignStore
from dmbot.devtools import graph_key_check as gkc
from dmbot.memory import auto_accept
from dmbot.memory import background as bg
from dmbot.memory.backstory_links import BackstoryLinks, NewLink
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.store import MemoryStore
from tests.pg import DatabaseTest
from tests.test_graph_key_check import backup, rel

GUILD, DM = 9_100, 9_101
TEXT = "The Nine Burgs are Ashford, Bramlow, Coldmere and Dunhollow, each with its own speaker."
LIST = "The Nine Burgs are Ashford, Bramlow, Coldmere and Dunhollow"
WHY = "listed as one of the Nine Burgs"
TOWNS = ("Ashford", "Bramlow", "Coldmere")


def reply(*links: dict[str, Any]) -> str:
    names = [
        {"name": n, "kind": "place", "also": [], "secret": [], "quote": n}
        for n in ("Nine Burgs", "Ashford", "Bramlow", "Coldmere", "Dunhollow")
    ]
    return json.dumps({"names": names, "connections": list(links)})


def line(how: str, inferred: str, sure: bool, a: str = "Ashford") -> dict[str, Any]:
    return {"a": a, "how": how, "b": "Nine Burgs", "quote": LIST, "words": "",
            "inferred": inferred, "sure": sure, "from": "", "change": ""}  # fmt: skip


class TheRead(unittest.TestCase):
    def test_a_sure_worked_out_in_keeps_its_sure(self) -> None:
        (got,) = bg.read_connections(reply(line("in", WHY, True)), TEXT)
        self.assertEqual((got.how, got.inferred, got.inferred_sure), ("located_in", WHY, True))

    def test_sure_is_dropped_on_anything_but_a_worked_out_in(self) -> None:
        (stated,) = bg.read_connections(reply(line("in", "", True)), TEXT)
        self.assertFalse(stated.inferred_sure)  # (stated: nothing to be sure of)
        (unsure,) = bg.read_connections(reply(line("in", WHY, False)), TEXT)
        self.assertFalse(unsure.inferred_sure)
        got = bg.read_connections(reply(line("knows", WHY, True)), TEXT)
        self.assertFalse(any(c.inferred_sure for c in got))

    def test_the_answer_shape_asks_for_sure(self) -> None:
        item = bg.SCHEMA["properties"]["connections"]["items"]
        self.assertIn("sure", item["required"])


class Base(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(MemorySection())
        self.memory = MemoryStore(self.db)
        self.links = BackstoryLinks(self.db)
        self.c = (await self.campaigns.create(GUILD, "Nine Burgs", DM)).id
        self.region = await self.place("Nine Burgs")
        self.towns = [await self.place(n) for n in TOWNS]

    async def place(self, name: str) -> str:
        written = await self.memory.add_entity(
            GUILD, self.c, type="place", name=name, source=DM_SOURCE, status=CONFIRMED
        )
        return written.value.id

    async def queue(self, *towns: str, sure: bool = True, quote: str = LIST, **kw: Any) -> None:
        await self.links.add(
            GUILD, self.c,
            [NewLink(t, "located_in", self.region, quote, upload="Burgs, 1 Jan", inferred=WHY,
                     inferred_sure=sure, **kw) for t in towns],
        )  # fmt: skip

    async def keep(self) -> auto_accept.Tally:
        tally = auto_accept.Tally()
        await auto_accept.accept_connections(
            self.memory, self.links, GUILD, self.c, tally, upload="Burgs, 1 Jan"
        )
        return tally

    async def kept_in(self) -> set[str]:
        return {
            r.subject_id
            for r in await self.memory.relations(GUILD, self.c, include_secret=True)
            if r.predicate == "located_in" and r.status == "confirmed"
        }


class TheStore(Base):
    async def test_sure_waits_with_the_row(self) -> None:
        await self.queue(self.towns[0])
        (row,) = await self.links.waiting(GUILD, self.c)
        self.assertTrue(row.inferred_sure)

    async def test_a_stated_twin_makes_it_stated_and_not_sure(self) -> None:
        await self.queue(self.towns[0])
        await self.links.add(
            GUILD, self.c, [NewLink(self.towns[0], "located_in", self.region, LIST)]
        )
        (row,) = await self.links.waiting(GUILD, self.c)
        self.assertEqual((row.inferred, row.inferred_sure), ("", False))


class TheSureStep(Base):
    async def test_a_region_s_listed_towns_are_kept(self) -> None:
        await self.queue(*self.towns)
        tally = await self.keep()
        self.assertEqual(tally.accepted[auto_accept.CONNECTIONS], 3)
        self.assertEqual(await self.kept_in(), set(self.towns))

    async def test_a_list_of_two_is_no_list(self) -> None:
        await self.queue(*self.towns[:2])
        tally = await self.keep()
        self.assertEqual(await self.kept_in(), set())
        self.assertEqual(tally.waiting_why[auto_accept.WORKED_OUT], 2)

    async def test_one_the_reader_is_not_sure_of_waits(self) -> None:
        await self.queue(*self.towns[:2])
        await self.queue(self.towns[2], sure=False)
        await self.keep()
        self.assertEqual(await self.kept_in(), set())  # (two sure ones are no list)

    async def test_a_place_with_another_parent_waits(self) -> None:
        other = await self.place("Old Kingdom")
        await self.memory.add_relation(
            GUILD, self.c, self.towns[0], "located_in", other, source=DM_SOURCE, confidence=1.0,
            status=CONFIRMED,
        )  # fmt: skip
        extra = await self.place("Dunhollow")
        await self.queue(*self.towns, extra)
        await self.keep()
        self.assertEqual(await self.kept_in() - {self.towns[0]}, {*self.towns[1:], extra})
        mine = [
            r for r in await self.memory.relations(GUILD, self.c, include_secret=True)
            if r.subject_id == self.towns[0] and r.object_id == self.region
        ]  # fmt: skip
        self.assertEqual(mine, [])

    async def test_a_quote_that_is_no_list_waits(self) -> None:
        await self.queue(*self.towns, quote="Ashford sits in the Nine Burgs")
        await self.keep()
        self.assertEqual(await self.kept_in(), set())

    async def test_a_secret_one_waits(self) -> None:
        await self.queue(*self.towns, secret=True)
        await self.keep()
        self.assertEqual(await self.kept_in(), set())

    async def test_lives_in_from_the_same_list_waits(self) -> None:
        speaker = (
            await self.memory.add_entity(
                GUILD, self.c, type="character", role="npc", name="Speaker Orla",
                source=DM_SOURCE, status=CONFIRMED,
            )
        ).value.id  # fmt: skip
        await self.queue(*self.towns)
        await self.links.add(
            GUILD, self.c,
            [NewLink(speaker, "lives_in", self.towns[0], LIST, upload="Burgs, 1 Jan",
                     inferred=WHY, inferred_sure=True)],
        )  # fmt: skip
        tally = await self.keep()
        self.assertEqual(tally.accepted[auto_accept.CONNECTIONS], 3)
        self.assertEqual(tally.waiting_why[auto_accept.WORKED_OUT], 1)

    async def test_discord_s_keep_all_still_leaves_them(self) -> None:
        from dmbot.memory import backstory_keep

        await self.queue(*self.towns)
        rows = await self.links.waiting(GUILD, self.c)
        done = await backstory_keep.keep_all(self.memory, self.links, GUILD, self.c, rows)
        self.assertEqual(done.kept, [])


SCORE_KEY: dict[str, Any] = {
    "nodes": [
        {"name": "Nine Burgs", "kind": "place", "aliases": [], "note": ""},
        {"name": "Ashford", "kind": "place", "aliases": [], "note": ""},
        {"name": "Cairnpeak", "kind": "place", "aliases": [], "note": ""},
    ],
    "pairs": [
        {"id": "p1", "a": "Ashford", "relation": "in", "b": "Nine Burgs", "tier": "stated",
         "must_find": True},
    ],
    "must_not_ties": [
        {"id": "n1", "a": "Cairnpeak", "relation": "in", "b": "Nine Burgs", "note": "a peak"},
    ],
}  # fmt: skip
SCORE_ENTRIES = [
    ("r", "Nine Burgs", "place"),
    ("a", "Ashford", "place"),
    ("k", "Cairnpeak", "place"),
]


class TheScore(unittest.TestCase):
    def test_a_listed_town_kept_is_found_and_a_near_miss_kept_is_false(self) -> None:
        rows = [rel("x1", "a", "located_in", "r"), rel("x2", "k", "located_in", "r")]
        report = gkc.check(backup(SCORE_ENTRIES, rows), SCORE_KEY)
        self.assertEqual([r.status for r in report.ties], [gkc.FOUND])
        self.assertEqual(report.false_ties, ["n1"])
        self.assertIn("Must-not-make ties kept as facts: 1", gkc.render(report))

    def test_a_near_miss_only_offered_is_no_false_tie(self) -> None:
        rows = [rel("x2", "k", "located_in", "r", status="proposed")]
        report = gkc.check(backup(SCORE_ENTRIES, rows), SCORE_KEY)
        self.assertEqual(report.false_ties, [])

    def test_the_sample_keys_carry_the_near_misses(self) -> None:
        from tests.test_graph_key_check import load

        for name in ("lore_key.json", "master_key.json"):
            key = load(name)
            nodes = {n["name"] for n in key["nodes"]}
            self.assertGreaterEqual(len(key["must_not_ties"]), 2)
            for t in key["must_not_ties"]:
                self.assertIn(t["a"], nodes)
                self.assertIn(t["b"], nodes)
