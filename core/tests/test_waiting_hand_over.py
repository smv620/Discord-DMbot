"""The waiting list says when a connection means an item changed hands (#4122, migration 0436):
`handOver`, `handOverFrom` and `handOverName` ride beside each waiting backstory row; a plain
connection, or one whose previous holder is gone, says nothing of a hand-over. Real Postgres;
invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.backstory_links import BackstoryLinks, NewLink
from dmbot.web import relationships
from dmbot.web.recaps import RecapError
from tests.test_web_relationships import Web


class HandOverWaiting(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.backstory = BackstoryLinks(self.db)
        self.belleros = await self.add("Belleros", "character", role="npc")
        self.claude = await self.add("Claude", "character", role="npc")
        self.brand = await self.add("Frost Brand", "item")

    async def rows(self) -> list[dict[str, Any]]:
        got = await relationships.read_waiting(
            self.web, self.session, self.cref, outside_engine=None
        )
        return list(got["backstory"])

    async def hand(self, **over: str) -> None:
        await self.backstory.add(
            self.g,
            self.one.id,
            [
                NewLink(
                    self.belleros,
                    "owns",
                    self.brand,
                    "Belleros bought the Frost Brand",
                    hand_over_from=over.get("frm", self.claude),
                    hand_over=over.get("how", "sold"),
                )
            ],
        )

    async def test_a_hand_over_carries_how_from_whom_and_the_name(self) -> None:
        await self.hand()
        (row,) = await self.rows()
        self.assertEqual(row["handOver"], "sold")
        self.assertEqual(row["handOverFrom"], self.claude)
        self.assertEqual(row["handOverName"], "Claude")

    async def test_a_plain_connection_says_nothing_of_one(self) -> None:
        await self.backstory.add(
            self.g, self.one.id,
            [NewLink(self.belleros, "owns", self.brand, "Belleros has the Frost Brand")],
        )  # fmt: skip
        (row,) = await self.rows()
        self.assertEqual((row["handOver"], row["handOverFrom"]), ("", ""))
        self.assertIsNone(row["handOverName"])

    async def test_a_previous_holder_who_is_gone_leaves_a_plain_connection(self) -> None:
        await self.hand(frm="0" * 32)
        (row,) = await self.rows()
        self.assertEqual((row["handOver"], row["handOverFrom"]), ("", ""))
        self.assertIsNone(row["handOverName"])

    async def test_a_merged_previous_holder_is_followed_to_the_one_kept(self) -> None:
        other = await self.add("Claude of the Reach", "character", role="npc")
        await self.hand()
        await self.memory.merge(
            self.g, self.one.id, other, self.claude, source="dm", dm_said_same=True
        )
        (row,) = await self.rows()
        self.assertEqual(row["handOverName"], "Claude of the Reach")

    async def test_a_chain_of_merges_is_followed_to_the_end_as_the_bot_does(self) -> None:
        b = await self.add("Claude the Elder", "character", role="npc")
        c = await self.add("Claude of the Reach", "character", role="npc")
        await self.hand()
        await self.memory.merge(self.g, self.one.id, b, self.claude, source="dm", dm_said_same=True)
        await self.memory.merge(self.g, self.one.id, c, b, source="dm", dm_said_same=True)
        (row,) = await self.rows()
        self.assertEqual(row["handOverName"], "Claude of the Reach")
        self.assertEqual(row["handOverFrom"], c)

    async def test_a_previous_holder_turned_down_leaves_a_plain_connection(self) -> None:
        await self.hand()
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'rejected' WHERE guild_id = %s AND id = %s",
                (self.g, self.claude),
            )
        (row,) = await self.rows()
        self.assertEqual((row["handOver"], row["handOverFrom"]), ("", ""))

    async def test_a_previous_holder_who_is_the_new_holder_leaves_a_plain_connection(self) -> None:
        await self.hand(frm=self.belleros)
        (row,) = await self.rows()
        self.assertEqual((row["handOver"], row["handOverFrom"]), ("", ""))

    async def test_the_door_is_still_a_definer_with_its_search_path(self) -> None:
        async with self.db.unscoped() as conn:
            cur = await conn.execute(
                "SELECT prosecdef, proconfig FROM pg_proc WHERE proname = 'dmbot_web_waiting'"
                " AND pronamespace = current_schema()::regnamespace"
            )
            (found,) = await cur.fetchall()
        self.assertTrue(found["prosecdef"])
        self.assertTrue(any(str(x).startswith("search_path=") for x in found["proconfig"]))

    async def test_a_player_gets_no_list(self) -> None:
        await self.hand()
        with self.assertRaises(RecapError):
            await relationships.read_waiting(self.web, self.player, self.cref, outside_engine=None)
