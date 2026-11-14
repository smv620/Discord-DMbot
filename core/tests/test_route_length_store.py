"""A route's stated length in the store (#4255; Supervisor's ruling): on a `route_to` connection and
on a route place, the DM's word and DMbot's read, one Undo per change, never DMbot's read over the
DM's, kept by ✅, carried by a turn, a merge and a backup, and gone with a place that stops being a
route. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import route_length as rl
from dmbot.memory.models import MemoryRuleError, Relation
from dmbot.memory.store import LENGTH_CHANGED, ROUTE_GONE
from tests.test_memory_store import DM, GUILD_A, GUILD_B, MemoryTest

DM_SOURCE = "dm"


class RouteLength(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.targos = await self.add("Targos", type="place", status="confirmed")
        self.bryn = await self.add("Bryn Shander", type="place", status="confirmed")
        made = await self.relate(self.targos, "route_to", self.bryn, detail="road",
                                 status="confirmed")  # fmt: skip
        self.road: Relation = made.value[0]

    async def row(self, rid: str | None = None) -> Relation:
        got = await self.memory.relation(GUILD_A, self.c, rid or self.road.id)
        assert got is not None
        return got

    async def set(self, words: str, source: str = DM_SOURCE, **kw: Any) -> Any:
        return await self.memory.set_route_length(
            GUILD_A, self.c, kw.pop("target", self.road.id), words,
            on=kw.pop("on", "connection"), source=source,
        )  # fmt: skip

    async def test_the_dms_length_with_its_amount_and_one_undo(self) -> None:
        done = await self.set("two leagues by cart")
        r = await self.row()
        self.assertEqual(
            (r.length_words, r.length_amount, r.length_unit, r.length_approx, r.length_from),
            ("two leagues by cart", 6.0, rl.MILES, False, "dm"),
        )
        await self.memory.undo(GUILD_A, self.c, done.batch)
        self.assertIsNone((await self.row()).length_words)

    async def test_about_and_a_range_say_about(self) -> None:
        await self.set("two or three days by sled")
        r = await self.row()
        self.assertEqual((r.length_amount, r.length_unit, r.length_approx), (2.0, rl.DAYS, True))

    async def test_a_few_days_keeps_its_words_only(self) -> None:
        await self.set("a few days")
        r = await self.row()
        self.assertEqual(
            (r.length_words, r.length_amount, r.length_unit), ("a few days", None, None)
        )

    async def test_dmbots_read_never_overwrites_the_dms_and_reads_twice_write_once(self) -> None:
        read = await self.set("a day by cart", source="background")
        self.assertEqual((await self.row()).length_from, "read")
        again = await self.set("a day by cart", source="background")
        self.assertIsNone(again.batch)  # (the same words read again: nothing new)
        await self.set("twenty miles")
        self.assertIsNone((await self.set("a day by cart", source="background")).batch)
        self.assertEqual((await self.row()).length_words, "twenty miles")
        assert read.batch is not None

    async def test_only_the_dm_takes_it_off(self) -> None:
        await self.set("twenty miles")
        with self.assertRaises(MemoryRuleError):
            await self.set("", source="background")
        done = await self.set("")
        self.assertIsNone((await self.row()).length_words)
        await self.memory.undo(GUILD_A, self.c, done.batch)
        self.assertEqual((await self.row()).length_words, "twenty miles")

    async def test_the_same_words_write_nothing(self) -> None:
        await self.set("twenty miles")
        self.assertIsNone((await self.set("twenty miles")).batch)

    async def test_keep_makes_dmbots_read_the_dms_with_one_undo(self) -> None:
        await self.set("a day by cart", source="background")
        kept = await self.memory.keep_route_length(
            GUILD_A, self.c, self.road.id, on="connection", source=DM_SOURCE
        )
        self.assertEqual((await self.row()).length_from, "dm")
        with self.assertRaisesRegex(MemoryRuleError, LENGTH_CHANGED):  # (kept already)
            await self.memory.keep_route_length(
                GUILD_A, self.c, self.road.id, on="connection", source=DM_SOURCE
            )
        await self.memory.undo(GUILD_A, self.c, kept.batch)
        self.assertEqual((await self.row()).length_from, "read")

    async def test_only_on_a_live_route(self) -> None:
        other = await self.relate(self.targos, "next_to", self.bryn,
                                  status="confirmed")  # fmt: skip
        with self.assertRaisesRegex(MemoryRuleError, ROUTE_GONE):
            await self.set("two days", target=other.value[0].id)
        with self.assertRaisesRegex(MemoryRuleError, ROUTE_GONE):
            await self.set("two days", target=self.targos, on="place")  # (an area, not a route)

    async def test_a_turned_route_keeps_its_length(self) -> None:
        await self.set("two days by cart")
        turned = (
            await self.memory.set_direction(
                GUILD_A, self.c, self.road.id, self.road.object_id, source=DM_SOURCE
            )
        ).value
        self.assertEqual((turned.length_words, turned.length_from), ("two days by cart", "dm"))

    async def test_a_route_place_and_its_form(self) -> None:
        east = await self.add("The Eastway", type="place", status="confirmed")
        await self.memory.set_place_form(GUILD_A, self.c, east, "route", source=DM_SOURCE)
        await self.set("about three days' march", target=east, on="place")
        place = await self.memory.entity(GUILD_A, self.c, east)
        assert place is not None
        self.assertEqual((place.length_amount, place.length_approx), (3.0, True))
        water = await self.memory.set_place_form(GUILD_A, self.c, east, "water", source=DM_SOURCE)
        self.assertIsNone(water.value.length_words)  # (only a route has a length)
        await self.memory.undo(GUILD_A, self.c, water.batch)
        back = await self.memory.entity(GUILD_A, self.c, east)
        assert back is not None
        self.assertEqual(back.length_words, "about three days' march")

    async def test_a_merge_keeps_the_dms_length(self) -> None:
        east = await self.add("The Eastway", type="place", status="confirmed")
        twin = await self.add("East Way", type="place", status="confirmed")
        for p in (east, twin):
            await self.memory.set_place_form(GUILD_A, self.c, p, "route", source=DM_SOURCE)
        await self.set("a day by cart", source="background", target=east, on="place")
        await self.set("two days by cart", target=twin, on="place")
        await self.memory.merge(GUILD_A, self.c, east, twin, source=DM_SOURCE, dm_said_same=True)
        kept = await self.memory.entity(GUILD_A, self.c, east)
        assert kept is not None
        self.assertEqual((kept.length_words, kept.length_from), ("two days by cart", "dm"))

    async def test_a_backup_carries_it(self) -> None:
        await self.set("about 20 miles")
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM)
        (road,) = [r for r in await self.memory.relations(GUILD_B, restored.id)
                   if r.predicate == "route_to"]  # fmt: skip
        self.assertEqual(
            (road.length_words, road.length_amount, road.length_approx),
            ("about 20 miles", 20, True),
        )

    async def test_a_kept_route_takes_its_length_from_its_quote_as_dmbots_read(self) -> None:
        # (#4255: the lore reader's route, kept by the sure step or the DM's press)
        from dmbot.memory import backstory_keep
        from dmbot.memory.backstory_links import BackstoryLinks, NewLink

        easthaven = await self.add("Easthaven", type="place", status="confirmed")
        links = BackstoryLinks(self.db)
        await links.add(
            GUILD_A, self.c,
            [NewLink(self.bryn, "route_to", easthaven,
                     "Easthaven is two days by dogsled from Bryn Shander.", detail="road",
                     upload="Notes")],
        )  # fmt: skip
        waiting = await links.waiting(GUILD_A, self.c, symmetric={"route_to"})
        done = await backstory_keep.keep_all(self.memory, links, GUILD_A, self.c, waiting)
        every = await self.memory.relations(GUILD_A, self.c, include_secret=True)
        (route,) = [
            r
            for r in every
            if r.predicate == "route_to" and easthaven in (r.subject_id, r.object_id)
        ]
        self.assertEqual(
            (route.length_words, route.length_amount, route.length_from),
            ("two days by dogsled", 2.0, "read"),
        )
        for batches in done.ended:  # (the keep's own Undo takes the length back too)
            self.assertTrue(all(b is not None for b in batches))

    async def card_lines(self, *, dm: bool) -> str:
        from dmbot.memory.lookup import CampaignLookup
        from dmbot.ui import name_card

        lookup = CampaignLookup.build(await self.memory.lookup_data(GUILD_A, self.c))
        rows = name_card._rows(lookup, self.targos, [await self.row()], dm)
        return " | ".join(name_card.connection_line(row) for row in rows)

    async def test_a_player_never_sees_an_unkept_read_and_the_dm_sees_it_marked(self) -> None:
        await self.set("a day by cart", source="background")
        self.assertIn("· a day by cart (DMbot read this)", await self.card_lines(dm=True))
        self.assertNotIn("a day by cart", await self.card_lines(dm=False))
        await self.memory.keep_route_length(
            GUILD_A, self.c, self.road.id, on="connection", source=DM_SOURCE
        )
        player = await self.card_lines(dm=False)
        self.assertIn("· a day by cart", player)
        self.assertNotIn("DMbot read this", player)
