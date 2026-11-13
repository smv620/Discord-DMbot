"""What the table may be told of a place's land, climate and ruler, on the website (#4311, the
players' half of #4287): a door any member of the server may call that gives only the rows the DM
confirmed and showed, then the gate and `map_facts.for_players` (the one players' rule) cut them
for this viewer. Real Postgres; invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.web import place_world
from dmbot.web.discord import DiscordGuild
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_relationships import Web


class TheTableSees(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.order = await self.add("Order of the Keep", "faction")
        await self.tell_the_table(self.hero or "")  # (Bob's hero now knows the Order too)

    async def dm_sets(
        self, layer: str, value: str | None, how: str = "", place: Any = None
    ) -> None:
        await self.memory.set_map_fact(
            self.g, self.one.id, place or self.keep, layer, value, how=how, source=DM_SOURCE
        )

    async def dm_shows(self, shown: bool = True, place: Any = None) -> None:
        await self.memory.show_map_facts(
            self.g, self.one.id, place or self.keep, shown, source=DM_SOURCE
        )

    async def bob_reads(self, place: Any = None) -> list[dict[str, Any]]:
        got = await place_world.read_table(self.web, self.player, self.cref, place or self.keep)
        return list(got["rows"])

    async def refusal(self, call: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await call
        return str(caught.exception.code)

    async def test_a_player_sees_a_row_the_dm_confirmed_and_showed_for_a_place_they_know(
        self,
    ) -> None:
        await self.dm_sets("terrain", "hill", "partly")
        await self.dm_sets("control", self.order, "claims")
        await self.dm_shows()
        rows = {r["layer"]: r for r in await self.bob_reads()}
        self.assertEqual((rows["terrain"]["value"], rows["terrain"]["how"]), ("hill", "partly"))
        self.assertEqual(
            (rows["control"]["groupName"], rows["control"]["how"]), ("Order of the Keep", "claims")
        )
        self.assertEqual(
            set(rows["terrain"]), {"layer", "value", "words", "groupName", "how", "part"}
        )

    async def test_nothing_until_the_dm_shows_it(self) -> None:
        await self.dm_sets("terrain", "hill")
        self.assertEqual(await self.bob_reads(), [])  # (set, but never shown)
        await self.dm_shows()
        self.assertEqual(len(await self.bob_reads()), 1)
        await self.dm_shows(False)  # 🙈
        self.assertEqual(await self.bob_reads(), [])

    async def test_a_value_dmbot_only_read_is_never_given(self) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO memory_map_facts (guild_id, campaign_id, id, entity_id, layer, value,"
                " status, source, table_knows, created_at) VALUES (%s, %s, %s, %s, 'climate',"
                " 'cold', 'read', 'text', true, 1)",
                (self.g, self.one.id, "a" * 32, self.keep),
            )
        self.assertEqual(await self.bob_reads(), [])

    async def test_a_place_the_player_does_not_know_gives_nothing(self) -> None:
        await self.dm_sets("terrain", "hill")
        await self.dm_shows()
        await self.keep_from_bob(self.keep)
        # the same answer as for an id that is not there: the route cannot be used to find places
        self.assertEqual(await self.refusal(self.bob_reads()), "not_found")
        self.assertEqual(await self.refusal(self.bob_reads("f" * 32)), "not_found")

    async def test_a_ruler_the_player_does_not_know_is_left_out_and_never_named(self) -> None:
        secret = await self.add("The Hidden Hand", "faction")
        await self.keep_from_bob(secret)  # (the DM keeps this group from Bob's hero)
        await self.dm_sets("terrain", "forest")
        await self.dm_sets("control", secret, "rules")
        await self.dm_shows()
        rows = await self.bob_reads()
        self.assertEqual([r["layer"] for r in rows], ["terrain"])
        self.assertNotIn("Hidden Hand", str(rows))

    async def test_a_dm_asking_here_sees_the_players_view_not_their_own(self) -> None:
        await self.dm_sets("terrain", "hill")  # (never shown)
        got = await place_world.read_table(self.web, self.session, self.cref, self.keep)
        self.assertEqual(got["rows"], [])
        await self.dm_shows()
        got = await place_world.read_table(self.web, self.session, self.cref, self.keep)
        self.assertEqual(
            [r["value"] for r in got["rows"]], ["hill"]
        )  # (shown, and the party knows the place)

    async def test_every_way_of_holding_a_place_comes_through(self) -> None:
        for how in ("rules", "claims", "occupies", "protects"):
            await self.dm_sets("control", self.order, how)
            await self.dm_shows()
            (row,) = [r for r in await self.bob_reads() if r["layer"] == "control"]
            self.assertEqual(row["how"], how)

    async def test_a_row_that_has_ended_is_not_given(self) -> None:
        await self.dm_sets("terrain", "hill")
        await self.dm_shows()
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "UPDATE memory_map_facts SET from_session_at = 1, to_session_at = 2"
                " WHERE entity_id = %s",
                (self.keep,),
            )
        self.assertEqual(await self.bob_reads(), [])

    async def test_a_ruler_group_merged_into_another_is_named_as_the_one_that_survives(
        self,
    ) -> None:
        await self.dm_sets("control", self.order, "rules")
        await self.dm_shows()
        other = await self.add("The Merged Order", "faction")
        await self.memory.merge(
            self.g, self.one.id, other, self.order, source=DM_SOURCE, dm_said_same=True
        )
        (row,) = [r for r in await self.bob_reads() if r["layer"] == "control"]
        self.assertEqual(row["groupName"], "The Merged Order")  # (the merge moved the row)

    async def test_a_stranger_a_bad_id_and_a_name_that_is_not_a_place(self) -> None:
        elsewhere = await self.signed(BOB, DiscordGuild(id=999, name="Elsewhere", manage=True))
        self.assertEqual(
            await self.refusal(place_world.read_table(self.web, elsewhere, self.cref, self.keep)),
            "not_found",
        )
        self.assertEqual(
            await self.refusal(place_world.read_table(self.web, self.player, self.cref, "nope")),
            "not_found",
        )
        self.assertEqual(
            await self.refusal(place_world.read_table(self.web, self.player, self.cref, self.mira)),
            "not_found",
        )
