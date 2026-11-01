"""A player character who joins is given what the whole table was shown (#2995, Supervisor
2026-11-01): the gate's party view agrees with the old flags for every kind of entry, however many
player characters join and in what order. Invented names throughout."""

from __future__ import annotations

from dmbot.memory import gate, knowers
from dmbot.memory.gate import ENTRY, Viewer
from dmbot.memory.models import CONFIRMED
from tests.test_memory_store import GUILD_A, MemoryTest


class TableRows(MemoryTest):
    async def pc(self, name: str, player: int) -> str:
        return await self.add(
            name, type="character", role="player_character", played_by=player, status=CONFIRMED
        )

    async def party_sees(self) -> set[str]:
        async with self.memory._read(GUILD_A, self.c, snapshot=True) as scope:
            return set((await gate.visible_to(scope, Viewer.the_party())).entries)

    async def shown_by_flags(self) -> set[str]:
        rows = await self.memory.entities(GUILD_A, self.c)
        return {e.id for e in rows if e.status == CONFIRMED and not e.unseen_by_players}

    async def entries(self) -> dict[str, str]:
        return {
            "pub": await self.add("The Pub", status=CONFIRMED),
            "place": await self.add("Greywater", type="place", status=CONFIRMED),
            "faction": await self.add("The Lantern Council", type="faction", status=CONFIRMED),
            "item": await self.add("Heartseeker", type="item", status=CONFIRMED),
            "hidden": await self.add("Rook", status=CONFIRMED, hidden_from_players=True),
            "proposed": await self.add("Maybe", status="proposed"),
        }

    async def test_the_party_view_agrees_with_the_flags_as_player_characters_join(self) -> None:
        first = await self.pc("Tavin", 1001)
        ids = await self.entries()
        for step, (name, player) in enumerate((("Mara", 1002), ("Kesh", 1003), ("Orin", 1004))):
            with self.subTest(step=step):
                await self.pc(name, player)
                ids[f"made after {step}"] = await self.add(f"Late {step}", status=CONFIRMED)
                self.assertEqual(await self.party_sees(), await self.shown_by_flags())
        self.assertIn(first, await self.party_sees())

    async def test_the_first_player_character_gets_what_the_flags_show(self) -> None:
        ids = await self.entries()  # (no player character yet: nobody holds any row)
        await self.pc("Tavin", 1001)
        seen = await self.party_sees()
        for key in ("pub", "place", "faction", "item"):
            self.assertIn(ids[key], seen)
        for key in ("hidden", "proposed"):
            self.assertNotIn(ids[key], seen)

    async def test_a_new_player_character_never_inherits_a_some_characters_list(self) -> None:
        first = await self.pc("Tavin", 1001)
        second = await self.pc("Mara", 1002)
        secret = await self.add("Rook", status=CONFIRMED, hidden_from_players=True)
        npc = await self.add("Brisa", status=CONFIRMED, hidden_from_players=True)
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, ENTRY, secret, [first], source="dm"
        )
        await self.memory.set_statement_knowers(GUILD_A, self.c, ENTRY, npc, [npc], source="dm")
        await self.pc("Kesh", 1003)
        async with self.memory._read(GUILD_A, self.c, snapshot=True) as scope:
            kesh_sees = (await gate.visible_to(scope, Viewer.player([second]))).entries
        self.assertNotIn(secret, kesh_sees)
        everyone = await self.memory.statement_knowers(GUILD_A, self.c, ENTRY, [secret, npc])
        self.assertEqual(everyone[secret], [first])
        self.assertEqual(everyone[npc], [npc])

    async def test_what_a_joining_character_was_given_is_in_the_joining_change(self) -> None:
        # (one change, so Undo reverses them with the character; a batch that also makes the
        # party's own entry is refused by Undo with or without this, which is not tested here)
        await self.pc("Tavin", 1001)
        pub = await self.add("The Pub", status=CONFIRMED)
        joined = await self.memory.add_entity(
            GUILD_A, self.c, name="Mara", type="character", role="player_character",
            played_by=1002, status=CONFIRMED, source="dm",
        )  # fmt: skip
        got = await self.memory.statement_knowers(GUILD_A, self.c, ENTRY, [pub])
        self.assertEqual(len(got[pub]), 2)  # both player characters know the pub now
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_changes WHERE campaign_id = %s AND batch = %s"
                " AND table_name = 'memory_knowers' AND op = 'insert'",
                (self.c, joined.batch),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertGreaterEqual(row["n"], 1)

    async def test_a_player_character_knows_its_own_entry_in_the_party_view(self) -> None:
        first = await self.pc("Tavin", 1001)
        second = await self.pc("Mara", 1002)
        seen = await self.party_sees()
        self.assertIn(first, seen)
        self.assertIn(second, seen)
        self.assertTrue(
            knowers.entry_shown(
                {
                    "status": CONFIRMED,
                    "hidden_from_players": False,
                    "audience_limited": False,
                    "unnamed": False,
                }
            )
        )

    async def test_a_hidden_entry_named_by_a_known_connection_stays_unseen(self) -> None:
        await self.pc("Tavin", 1001)
        await self.pc("Mara", 1002)
        pub = await self.add("The Pub", status=CONFIRMED)
        rook = await self.add("Rook", status=CONFIRMED, hidden_from_players=True)
        made = await self.memory.add_relation(
            GUILD_A, self.c, pub, "knows", rook, source="dm", confidence=1.0, status=CONFIRMED
        )
        link = str(made.value[0].id)
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "connection", link, [], source="dm"
        )
        data = await self.memory.lookup_data(GUILD_A, self.c)
        by_name = {e.name: e for e in data.entities}
        self.assertTrue(by_name["Rook"].unseen_by_players)
        self.assertFalse(by_name["The Pub"].unseen_by_players)

    async def test_a_who_knows_edit_reloads_the_lookup(self) -> None:
        from dmbot.memory import notify

        self.assertIn("memory_knowers", notify.LOOKUP_TABLES)
        self.assertIn("memory_perspectives", notify.LOOKUP_TABLES)
