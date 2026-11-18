"""World notes on the website lists a road's length a kept map disagrees with (#4677, migration
0543): the door's rows run through the same pure check and words as Discord's "Roads" group; a
length the DM kept, a close one, a travel time and a map DMbot may not use give no line; only a DM
of the campaign reads them, and ✅ Keep takes a road's line id. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import DM
from dmbot.web import world_notes
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_api import THURSDAY
from tests.test_web_map_pins import Pins


class Roads(Pins):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.targos = await self.add("Targos", "place")
        self.bryn = await self.add("Bryn Shander", "place")
        # a map 1000 px wide, 0.05 miles to a pixel: pins at 0.1 and 0.9 across are 40 miles apart
        await self.sql(
            "UPDATE campaign_maps SET scale_value = 0.05, scale_unit = 'miles', scale_from = 'dm',"
            " width = 1000, height = 1000 WHERE guild_id = %s AND id = %s",
            self.g, self.dm_map,
        )  # fmt: skip
        await self.pin("Targos", 0.1, 0.5, place=self.targos, n=1)
        await self.pin("Bryn Shander", 0.9, 0.5, place=self.bryn, n=2)

    async def road(self, words: str = "10 miles", amount: float = 10.0, **kw: Any) -> str:
        made = await self.memory.add_relation(
            self.g, self.one.id, self.targos, "route_to", self.bryn, source="dm",
            confidence=1.0, status="confirmed",
        )  # fmt: skip
        rid = made.value[0].id
        await self.sql(
            "UPDATE memory_relations SET length_words = %s, length_amount = %s,"
            " length_unit = %s, length_from = %s WHERE guild_id = %s AND id = %s",
            words, amount, kw.get("unit", "miles"), kw.get("from_", "read"), self.g, rid,
        )  # fmt: skip
        return str(rid)

    async def lines(self, who: Any = None) -> list[dict[str, Any]]:
        got = await world_notes.read(self.web, who or self.session, self.cref)
        roads: list[dict[str, Any]] = got["roads"]
        return roads

    async def test_a_read_length_far_short_of_the_map_is_a_line_in_discords_words(self) -> None:
        rid = await self.road()
        (line,) = await self.lines()
        self.assertEqual(line["id"], f"road:{rid}")
        # named in the road's own order, subject to object, as Discord's `world_notes._roads` does
        # (a symmetric road may be stored either way round, by its ids)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT subject_id FROM memory_relations WHERE guild_id = %s AND id = %s",
                (self.g, rid),
            )
            first = (await cur.fetchone() or {})["subject_id"]
        first_name, second_name = (
            ("Targos", "Bryn Shander") if first == self.targos else ("Bryn Shander", "Targos")
        )
        self.assertEqual(line["name"], f"{first_name} to {second_name}")
        self.assertIn("10 miles", line["note"])
        self.assertIn("The Keep", line["note"])
        self.assertIn("about 40 miles in a straight line", line["note"])

    async def test_a_close_length_gives_no_line(self) -> None:
        await self.road("38 miles", 38.0)
        self.assertEqual(await self.lines(), [])

    async def test_a_length_the_dm_kept_gives_no_line(self) -> None:
        await self.road(from_="dm")
        self.assertEqual(await self.lines(), [])

    async def test_a_travel_time_is_never_compared(self) -> None:
        await self.road("two days by cart", 2.0, unit="days")
        self.assertEqual(await self.lines(), [])

    async def test_a_map_dmbot_may_not_use_gives_no_line(self) -> None:
        await self.road()
        await self.sql(
            "UPDATE campaign_maps SET use_ok = false WHERE guild_id = %s AND id = %s",
            self.g, self.dm_map,
        )  # fmt: skip
        self.assertEqual(await self.lines(), [])

    async def test_a_player_gets_no_line_and_a_stranger_none(self) -> None:
        await self.road()
        with self.assertRaises(RecapError) as refused:
            await self.lines(self.player)
        self.assertEqual(refused.exception.code, "not_allowed")

    async def test_keep_takes_a_road_line_and_nothing_else_changes_shape(self) -> None:
        rid = await self.road()
        answer = await world_notes.queue_keep(
            self.web, self.session, self.cref, {"ids": [f"road:{rid}"]}
        )
        self.assertTrue(answer)
        with self.assertRaises(RecapError):  # (no 🚫 for a road: the shape check refuses it)
            await world_notes.queue_not(self.web, self.session, self.cref, {"id": f"road:{rid}"})

    async def test_the_bots_keep_makes_the_length_the_dms_and_the_line_goes(self) -> None:
        rid = await self.road()
        self.assertEqual(len(await self.lines()), 1)
        await self.memory.keep_world_notes(self.g, self.one.id, [f"road:{rid}"], source=DM)
        self.assertEqual(await self.lines(), [])

    async def test_a_length_from_a_session_says_so(self) -> None:
        rid = await self.road()
        await self.sql(
            "UPDATE memory_relations SET from_session_at = 500 WHERE guild_id = %s AND id = %s",
            self.g, rid,
        )  # fmt: skip
        (line,) = await self.lines()
        self.assertTrue(line["note"].startswith("said in a session"))
        self.assertEqual(line["fromSession"], 500)

    async def test_another_campaigns_dm_gets_nothing(self) -> None:
        await self.road()
        bob = await self.signed(BOB, THURSDAY)  # DMs Two, not One
        with self.assertRaises(RecapError) as refused:
            await self.lines(bob)
        self.assertEqual(refused.exception.code, "not_allowed")
