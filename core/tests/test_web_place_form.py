"""Land, water or a road, on the website (#4173 slice 6a, level with Discord's 📏 Size and kind
screen): a DM sets and clears a place's form from the All names page, the list sends it with each
name, the door and the API take a form word or a blank and nothing else, and the bot refuses an
entry that is not a place. Invented names throughout."""

from __future__ import annotations

from typing import Any

from tests.test_web_name_actions import Acting


class PlaceForm(Acting):
    async def form_of(self, entity: str) -> tuple[str | None, str | None]:
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT form, form_from FROM memory_entities WHERE campaign_id = %s AND id = %s",
                (self.one.id, entity),
            )
            row = await cur.fetchone()
        assert row is not None
        return row["form"], row["form_from"]

    async def listed(self, entity: str) -> Any:
        got = await self.page()
        return next(n for n in got["names"] if n["id"] == entity)

    async def test_a_dm_sets_it_the_list_sends_it_and_a_blank_clears_it(self) -> None:
        self.assertIsNone((await self.listed(self.keep))["form"])
        await self.queue(self.keep, action="place-form", form="water")
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual(await self.form_of(self.keep), ("water", "dm"))
        listed = await self.listed(self.keep)
        self.assertEqual((listed["form"], listed["formFrom"]), ("water", "dm"))
        await self.queue(self.keep, action="place-form", form=None)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual(await self.form_of(self.keep), (None, None))

    async def test_a_word_that_is_not_a_form_is_refused(self) -> None:
        for bad in ("lake", "Water", 7, ["route"], ""):
            refused = await self.refused_action(self.keep, action="place-form", form=bad)
            self.assertEqual(refused, "bad_name", bad)
        self.assertEqual(await self.form_of(self.keep), (None, None))

    async def test_the_bot_refuses_an_entry_that_is_not_a_place(self) -> None:
        await self.queue(self.sword, action="place-form", form="route")
        self.assertEqual([o.error for o in await self.apply_all()], ["bad_target"])

    async def test_the_bot_refuses_a_non_dm_sender(self) -> None:
        await self.queue(self.keep, action="place-form", form="water")
        self.assertEqual([o.error for o in await self.apply_all(dms={999})], ["not_allowed"])
        self.assertEqual(await self.form_of(self.keep), (None, None))
