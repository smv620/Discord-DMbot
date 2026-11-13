"""A place's land, climate and ruler on the website (#4287, slice 1, the DM's half): a database
door that answers a DM of the campaign and nobody else, four queue actions that take only the
fixed words (a value and its shade, or a group and how it holds the place, or nothing for "Not
sure"), and the bot's applier, which runs them through the store's own rules. What players see is
#4311. Invented names throughout."""

from __future__ import annotations

from typing import Any

from dmbot.web import place_world
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB
from tests.test_web_all_names import MEMBER
from tests.test_web_api import THURSDAY
from tests.test_web_name_actions import Acting


class PlaceWorld(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.order = await self.add("Order of the Keep", "faction")

    async def world(self, entity: str | None = None, who: Any = None) -> dict[str, Any]:
        return await place_world.read(self.web, who or self.session, self.cref, entity or self.keep)

    async def refusal(self, call: Any) -> str:
        with self.assertRaises(RecapError) as caught:
            await call
        return str(caught.exception.code)

    async def run_one(self, entity: str, **body: Any) -> list[str]:
        await self.queue(entity, **body)
        return [o.error for o in await self.apply_all()]


class TheDoor(PlaceWorld):
    async def test_a_dm_reads_a_place_with_nothing_set_and_the_groups_for_the_menu(self) -> None:
        got = await self.world()
        self.assertEqual(got["rows"], [])
        self.assertIn(
            {"id": self.order, "name": "Order of the Keep"},
            got["groups"],
        )

    async def test_what_the_dm_set_is_there_with_who_gave_it(self) -> None:
        self.assertEqual(
            await self.run_one(self.keep, action="map-terrain", value="hill", how="partly"), [""]
        )
        self.assertEqual(
            await self.run_one(self.keep, action="map-control", group=self.order, how="claims"),
            [""],
        )
        rows = {r["layer"]: r for r in (await self.world())["rows"]}
        self.assertEqual(
            (rows["terrain"]["value"], rows["terrain"]["how"], rows["terrain"]["source"]),
            ("hill", "partly", "dm"),
        )
        self.assertFalse(rows["terrain"]["read"])  # (the DM's own word, not "DMbot read this")
        self.assertEqual(
            (rows["control"]["group"], rows["control"]["groupName"], rows["control"]["how"]),
            (self.order, "Order of the Keep", "claims"),
        )

    async def test_a_value_the_bot_only_read_is_marked_read(self) -> None:
        async with self.db.guild(self.g) as conn:
            await conn.execute(
                "INSERT INTO memory_map_facts (guild_id, campaign_id, id, entity_id, layer, value,"
                " status, source, created_at) VALUES (%s, %s, %s, %s, 'climate', 'cold',"
                " 'read', 'text', 1)",
                (self.g, self.one.id, "a" * 32, self.keep),
            )
        (row,) = (await self.world())["rows"]
        self.assertEqual((row["value"], row["read"], row["source"]), ("cold", True, "text"))

    async def test_a_player_and_a_stranger_get_nothing(self) -> None:
        self.assertEqual(
            await self.refusal(self.world(who=await self.signed(BOB, THURSDAY))), "not_allowed"
        )
        elsewhere = await self.signed(BOB, MEMBER.__class__(id=999, name="Elsewhere", manage=True))
        self.assertEqual(
            await self.refusal(self.world(who=elsewhere)), "not_found"
        )  # (not in the server)

    async def test_a_name_that_is_not_a_place_and_a_bad_id_are_not_found(self) -> None:
        self.assertEqual(await self.refusal(self.world(self.sword)), "not_found")
        self.assertEqual(await self.refusal(self.world("not-an-id")), "not_found")
        self.assertEqual(await self.refusal(self.world("f" * 32)), "not_found")


class TheActions(PlaceWorld):
    async def test_not_sure_clears_a_value(self) -> None:
        await self.run_one(self.keep, action="map-climate", value="cold", how="dry")
        self.assertEqual(await self.run_one(self.keep, action="map-climate", value=None), [""])
        self.assertEqual((await self.world())["rows"], [])

    async def test_only_the_fixed_words_are_taken(self) -> None:
        for body in (
            {"action": "map-terrain", "value": "lava"},
            {"action": "map-terrain", "value": "hill", "how": "dry"},  # (a climate shade)
            {"action": "map-climate", "value": "cold", "how": "mostly"},
            {"action": "map-climate", "value": None, "how": "dry"},  # (a shade with no value)
            {"action": "map-control", "group": "xyz", "how": "rules"},
            {"action": "map-control", "group": self.order},  # (no "how")
            {"action": "map-control", "group": self.order, "how": "owns"},
            {"action": "map-show"},
            {"action": "map-show", "shown": "yes"},
        ):
            self.assertIn(
                await self.refused_action(self.keep, **body),
                ("bad_name", "bad_action", "bad_text"),
                body,
            )
        self.assertEqual((await self.world())["rows"], [])

    async def test_a_ruler_must_be_a_group_of_this_campaign(self) -> None:
        refused = await self.refused_action(
            self.keep, action="map-control", group=self.sword, how="rules"
        )
        self.assertEqual(refused, "bad_target")
        refused = await self.refused_action(
            self.keep, action="map-control", group="e" * 32, how="rules"
        )
        self.assertEqual(refused, "bad_target")

    async def test_the_show_switch_runs_and_a_place_that_is_not_a_place_is_refused_by_the_bot(
        self,
    ) -> None:
        await self.run_one(self.keep, action="map-terrain", value="hill")
        self.assertEqual(await self.run_one(self.keep, action="map-show", shown=True), [""])
        (row,) = (await self.world())["rows"]
        self.assertTrue(row["shown"])
        self.assertEqual(
            await self.run_one(self.sword, action="map-terrain", value="hill"), ["bad_target"]
        )

    async def test_a_climate_on_a_small_place_is_refused_by_the_bot(self) -> None:
        await self.run_one(self.keep, action="place-size", size="building")
        self.assertEqual(
            await self.run_one(self.keep, action="map-climate", value="cold"), ["bad_target"]
        )

    async def test_a_non_dm_sender_is_refused_by_the_bot(self) -> None:
        await self.queue(self.keep, action="map-terrain", value="hill")
        self.assertEqual([o.error for o in await self.apply_all(dms={999})], ["not_allowed"])
        self.assertEqual((await self.world())["rows"], [])
