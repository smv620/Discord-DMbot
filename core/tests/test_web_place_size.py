"""How big a place is, on the website (#4173 slice 1, level with Discord's 📏 Size menu): a DM sets,
changes and clears it from the All names page, the list sends it with each name, the door takes a
size word or a blank and nothing else, and the bot refuses an entry that is not a place. Invented
names throughout."""

from __future__ import annotations

from typing import Any

from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_name_actions import Acting


class PlaceSize(Acting):
    async def size_of(self, entity: str) -> str | None:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT place_level FROM memory_entities WHERE campaign_id = %s AND id = %s",
                (self.one.id, entity),
            )
            row = await cur.fetchone()
        assert row is not None
        level: str | None = row["place_level"]
        return level

    async def listed(self, entity: str, who: Any = None) -> Any:
        got = await self.page(who)
        return next(n for n in got["names"] if n["id"] == entity)

    async def door(self, entity: str, text: str, target: str = "", flag: bool = False) -> Any:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
                (self.g, self.one.id, entity, "place-size", "", target, text, flag),
            )
            got = await cur.fetchone()
        assert got is not None
        return got["r"]

    async def test_a_dm_sets_changes_and_clears_it(self) -> None:
        self.assertIsNone((await self.listed(self.keep))["placeLevel"])
        await self.queue(self.keep, action="place-size", size="building")
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual(await self.size_of(self.keep), "building")
        self.assertEqual((await self.listed(self.keep))["placeLevel"], "building")
        self.assertEqual((await self.listed(self.keep))["placeLevelFrom"], "dm")
        await self.queue(self.keep, action="place-size", size="settlement")
        await self.apply_all()
        self.assertEqual(await self.size_of(self.keep), "settlement")
        await self.queue(self.keep, action="place-size", size=None)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertIsNone(await self.size_of(self.keep))

    async def test_it_is_one_change_so_one_undo(self) -> None:
        before = await self.memory.version(self.g, self.one.id)
        await self.queue(self.keep, action="place-size", size="building")
        await self.apply_all()
        changes = await self.log_since(before)
        self.assertEqual(len({c["batch"] for c in changes}), 1)

    async def test_a_word_that_is_not_a_size_is_refused(self) -> None:
        for bad in ("huge", "Building", 7, ["room"], ""):
            refused = await self.refused_action(self.keep, action="place-size", size=bad)
            self.assertEqual(refused, "bad_name", bad)
        self.assertIsNone(await self.size_of(self.keep))

    async def test_the_bot_refuses_an_entry_that_is_not_a_place(self) -> None:
        await self.queue(self.sword, action="place-size", size="building")
        self.assertEqual([o.error for o in await self.apply_all()], ["bad_target"])

    async def test_the_bot_refuses_a_non_dm_sender(self) -> None:
        await self.queue(self.keep, action="place-size", size="building")
        self.assertEqual([o.error for o in await self.apply_all(dms={999})], ["not_allowed"])
        self.assertIsNone(await self.size_of(self.keep))

    async def test_a_dm_of_another_campaign_cannot_set_it(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        refused = await self.refused_action(self.keep, who=bob, action="place-size", size="room")
        self.assertEqual(refused, "not_allowed")

    async def test_the_door_takes_a_size_word_or_a_blank_and_nothing_else(self) -> None:
        self.assertEqual((await self.door(self.keep, "building"))["status"], "ok")
        self.assertEqual((await self.door(self.keep, ""))["status"], "ok")
        for bad in ("huge", "Building", "building room", "room1"):
            self.assertEqual((await self.door(self.keep, bad))["status"], "bad_text", bad)
        self.assertEqual(
            (await self.door(self.keep, "room", target=self.sword))["status"], "bad_text"
        )
        self.assertEqual((await self.door(self.keep, "room", flag=True))["status"], "bad_text")
