"""A real person at the table on the Story graph is a "player" or a "dm" (#2670, for #2618): one
word, read from the campaign's DM list each time, on the graph's nodes and on the overview's
names. Run as the website's own database role; invented names and numbers throughout."""

from __future__ import annotations

import unittest
from typing import Any

from dmbot.web import relationships
from tests.test_recap_web import BOB
from tests.test_web_relationships import Web

CAROL_ID = 4242  # a second DM, invented


class AtTable(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # Two people at the table linked to Rook: Bob (a player) and Carol (a DM as well as Alice).
        await self.sql(
            "INSERT INTO campaign_dms (guild_id, campaign_id, user_id) VALUES (%s, %s, %s)",
            self.g, self.one.id, CAROL_ID,
        )  # fmt: skip
        self.bob = await self.person("Bob at the table", BOB.id)
        self.carol = await self.person("Carol at the table", CAROL_ID)
        await self.link("knows", self.bob, self.rook, "bob-rook")
        await self.link("knows", self.carol, self.rook, "carol-rook")

    async def person(self, name: str, user_id: int) -> str:
        # (DMbot makes these itself; a DM's list of names is how a test makes one, then it is
        # named so the checks can find it)
        await self.memory.add_person_names(self.g, self.one.id, user_id, [name])
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "UPDATE memory_entities SET name = %s WHERE guild_id = %s AND campaign_id = %s"
                " AND type = 'person' AND played_by = %s RETURNING id",
                (name, self.g, self.one.id, user_id),
            )
            row = await cur.fetchone()
        assert row is not None
        return str(row["id"])

    async def nodes(self) -> dict[str, dict[str, Any]]:
        got = await self.graph(center=self.rook, hops=1)
        return {n["name"]: n for n in got["nodes"]}

    async def tray(self) -> dict[str, dict[str, Any]]:
        got = await relationships.read_overview(self.web, self.session, self.cref)
        return {n["name"]: n for n in got["tray"]}


class TheWord(AtTable):
    async def test_a_dm_and_a_player_in_one_campaign_each_get_the_right_word(self) -> None:
        nodes = await self.nodes()
        self.assertEqual(nodes["Bob at the table"]["atTable"], "player")
        self.assertEqual(nodes["Carol at the table"]["atTable"], "dm")

    async def test_the_overview_says_the_same(self) -> None:
        lone_player = await self.person("Dana at the table", 5151)
        lone_dm = await self.person("Eli at the table", CAROL_ID)
        tray = await self.tray()
        self.assertEqual(tray["Dana at the table"]["atTable"], "player")
        self.assertEqual(tray["Eli at the table"]["atTable"], "dm")
        self.assertNotIn(self.bob, tray)  # (Bob and Carol are linked: in a group, not the tray)
        self.assertTrue({lone_player, lone_dm} <= {n["id"] for n in tray.values()})
        full = await relationships.read_overview(self.web, self.session, self.cref)
        self.assertEqual(full["counts"]["names"], len(tray) + full["counts"]["connectedNames"])

    async def test_a_name_that_is_not_a_person_has_no_such_field(self) -> None:
        nodes = await self.nodes()
        for name in ("Rook", "Greywater Keep", "Mira Voss"):
            if name in nodes:
                self.assertNotIn("atTable", nodes[name])
        await self.add("Lone Hall", "place")
        for n in (await self.tray()).values():
            if n["kind"] != "person":
                self.assertNotIn("atTable", n)

    async def test_a_person_who_stops_being_a_dm_reads_player_on_the_next_read(self) -> None:
        self.assertEqual((await self.nodes())["Carol at the table"]["atTable"], "dm")
        await self.sql(
            "DELETE FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
            self.g, self.one.id, CAROL_ID,
        )  # fmt: skip
        self.assertEqual((await self.nodes())["Carol at the table"]["atTable"], "player")

    async def test_nothing_else_about_the_person_is_sent(self) -> None:
        node = (await self.nodes())["Bob at the table"]
        self.assertEqual(
            set(node), {"id", "name", "kind", "atTable"} | ({"secret"} & set(node)), node
        )
        self.assertNotIn(str(BOB.id), repr(node))  # no Discord number anywhere in the row

    async def test_a_player_sees_the_same_word(self) -> None:
        got = await self.graph(self.player, center=self.rook, hops=1)
        by_name = {n["name"]: n for n in got["nodes"]}
        self.assertEqual(by_name["Carol at the table"]["atTable"], "dm")
        self.assertEqual(by_name["Bob at the table"]["atTable"], "player")


class TheSourceStaysCurrent(unittest.TestCase):
    def test_the_migration_adds_the_word_to_both_doors_and_nothing_else(self) -> None:
        from dmbot import schema

        sql = dict(schema.MIGRATIONS)["0235_at_table"]
        self.assertEqual(sql.count("'atTable'"), 2)  # the graph's nodes and the overview's names
        self.assertNotIn("played_by,", sql.replace("e.played_by)", ""))  # never selected on its own


if __name__ == "__main__":
    unittest.main()
