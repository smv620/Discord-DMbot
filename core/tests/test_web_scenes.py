"""The scene of a question on the website (#3644): the DM's list of unnamed people and the names a
recap asks to check carry the scene DMbot kept, and nobody else's does. Real Postgres; invented
names."""

from __future__ import annotations

from typing import Any

from dmbot.web import all_names, scenes
from dmbot.web.recaps import _scoped
from tests.test_scenes import ALICE, TEXT, Base


class OnTheList(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{self.g}.{self.one.id}"

    async def rows(self, who: Any) -> dict[str, dict[str, Any]]:
        got = await all_names.read_page(self.web, who, self.cref, unnamed=True)
        return {n["id"]: n for n in got["names"]}

    async def test_a_dm_sees_the_scene_on_an_unnamed_person(self) -> None:
        await self.put(self.scene())
        row = (await self.rows(self.session))[self.someone]
        self.assertEqual(row["scene"], TEXT)

    async def test_an_unnamed_person_with_no_scene_has_no_key(self) -> None:
        row = (await self.rows(self.session))[self.someone]
        self.assertNotIn("scene", row)

    async def test_a_named_entry_never_carries_one(self) -> None:
        await self.put(self.scene(self.mira))  # (an entry whose question is not open is never read)
        self.assertNotIn("scene", (await self.rows(self.session))[self.mira])

    async def test_a_member_who_is_not_a_dm_is_given_no_scene(self) -> None:
        await self.put(self.scene())
        async with self.web.user(self.player.user_id, session=self.player.id_hash) as conn:
            await _scoped(conn, self.g)
            got = await scenes.read(conn, self.g, self.one.id, [self.someone])
        self.assertEqual(got, {})

    async def test_another_campaign_is_never_read(self) -> None:
        await self.put(self.scene())
        other = (await self.campaigns.create(self.g, "Elsewhere", 7)).id
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            self.assertEqual(await scenes.read(conn, self.g, other, [self.someone]), {})

    async def test_no_ids_or_too_many_are_handled(self) -> None:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await _scoped(conn, self.g)
            self.assertEqual(await scenes.read(conn, self.g, self.one.id, []), {})
            many = [f"{i:032x}" for i in range(scenes.MAX_IDS + 50)]
            self.assertEqual(await scenes.read(conn, self.g, self.one.id, many), {})
        self.assertEqual(ALICE, 8)
