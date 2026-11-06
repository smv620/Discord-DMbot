"""Removing a person the story never named, from the website's Names page (owner request): the
queue door lets `remove` find an unnamed entry (waiting or kept), the bot turns it down in one
change the name card's Undo takes back, and a normal removal is unchanged. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import web_actions as wa
from dmbot.memory.models import PROPOSED, REJECTED
from tests.test_recap_web import BOB
from tests.test_web_api import THURSDAY
from tests.test_web_name_actions import Acting


class RemovingUnnamed(Acting):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        added = await self.memory.add_unnamed(
            self.g, self.one.id, what="sister", anchor_id=self.mira, role="sibling"
        )
        self.sis = added.entity.id

    async def status_of(self, entity_id: str) -> str:
        found = await self.memory.entity(self.g, self.one.id, entity_id)
        assert found is not None
        return str(found.status)

    async def raw_remove(self, entity: str, **kw: Any) -> Any:
        async with self.web.user(self.session.user_id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(self.g),))
            cur = await conn.execute(
                "SELECT dmbot_web_name_action_add(%s, %s, %s, 'remove', '', '', '', %s) AS r",
                (self.g, self.one.id, entity, kw.get("flag", True)),
            )
            got = await cur.fetchone()
        assert got is not None
        return got["r"]


class TheRemoval(RemovingUnnamed):
    async def test_a_dm_removes_an_unnamed_person_and_it_is_one_undoable_change(self) -> None:
        self.assertEqual(await self.status_of(self.sis), PROPOSED)
        before = await self.memory.version(self.g, self.one.id)
        answer = await self.queue(self.sis, action="remove", confirm=True)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual((await self.poll(answer))["state"], "done")
        self.assertEqual(await self.status_of(self.sis), REJECTED)
        changes = await self.log_since(before)
        self.assertEqual({c["batch"] for c in changes}, {changes[0]["batch"]})  # one Undo

    async def test_it_needs_the_confirmation(self) -> None:
        refused = await self.refused_action(self.sis, action="remove")
        self.assertEqual(refused, "needs_confirm")
        self.assertEqual((await self.raw_remove(self.sis, flag=False))["status"], "needs_confirm")
        self.assertEqual(await self.status_of(self.sis), PROPOSED)

    async def test_a_kept_unnamed_person_is_removed_too(self) -> None:
        await self.sql(
            "UPDATE memory_entities SET status = 'confirmed' WHERE campaign_id = %s AND id = %s",
            self.one.id,
            self.sis,
        )
        await self.queue(self.sis, action="remove", confirm=True)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual(await self.status_of(self.sis), REJECTED)

    async def test_a_second_press_is_turned_away_at_the_door(self) -> None:
        await self.queue(self.sis, action="remove", confirm=True)
        await self.apply_all()
        self.assertEqual(
            await self.refused_action(self.sis, action="remove", confirm=True), "not_found"
        )

    async def test_only_a_dm_of_the_campaign_may_remove_it(self) -> None:
        bob = await self.signed(BOB, THURSDAY)
        refused = await self.refused_action(self.sis, who=bob, action="remove", confirm=True)
        self.assertEqual(refused, "not_allowed")
        self.assertEqual(await self.status_of(self.sis), PROPOSED)

    async def test_the_bot_refuses_a_sender_who_is_not_a_dm_any_more(self) -> None:
        await self.queue(self.sis, action="remove", confirm=True)
        self.assertEqual([o.error for o in await self.apply_all(dms={999})], ["not_allowed"])
        self.assertEqual(await self.status_of(self.sis), PROPOSED)

    async def test_a_named_entry_still_removes_as_before(self) -> None:
        await self.queue(self.keep, action="remove", confirm=True)
        self.assertEqual([o.error for o in await self.apply_all()], [""])
        self.assertEqual(await self.status_of(self.keep), REJECTED)

    async def test_the_bot_will_not_remove_an_unnamed_entry_without_the_confirmation(self) -> None:
        act = wa.Action(
            id="a" * 32,
            campaign_id=self.one.id,
            entity_id=self.sis,
            user_id=self.session.user_id,
            action="remove",
            flag=False,
        )
        out = await wa.apply(self.memory, self.g, {self.session.user_id}, act)
        self.assertNotEqual(out.error, "")
        self.assertEqual(await self.status_of(self.sis), PROPOSED)
