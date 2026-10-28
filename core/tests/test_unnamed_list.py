"""People the story never named, in the names list (#2790, slice 4b): the "Not named yet" group
for DMs only, Give them a name, and the question when the name is one DMbot knows."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.campaigns import Campaign
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, MERGED, PROPOSED
from dmbot.ui import name_lists, name_unnamed
from dmbot.ui import names as names_ui
from tests.test_memory_store import GUILD_A, MemoryTest

DM = 7


class TheGroup(MemoryTest):
    async def setup_people(self) -> tuple[str, str]:
        ken = await self.add("Ken", status=CONFIRMED)
        sis = (
            await self.memory.add_unnamed(
                GUILD_A, self.c, what="sister", anchor_id=ken, role="sibling"
            )
        ).entity.id
        return ken, sis

    async def view(self, secrets: bool) -> CampaignLookup:
        data = await self.memory.lookup_data(GUILD_A, self.c)
        return CampaignLookup.build(data).for_viewer(secrets)

    async def test_a_dm_sees_the_group_last_with_a_count_and_nobody_else_does(self) -> None:
        _ken, sis = await self.setup_people()
        dm, other = await self.view(True), await self.view(False)
        self.assertEqual(name_lists.kinds_in(dm)[-1], (name_unnamed.NOT_NAMED, 1))
        self.assertNotIn(sis, other.entities)
        self.assertNotIn(name_unnamed.NOT_NAMED, [k for k, _ in name_lists.kinds_in(other)])

    async def test_no_other_list_shows_them_even_when_confirmed(self) -> None:
        _ken, sis = await self.setup_people()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_entities SET status = 'confirmed' WHERE id = %s", (sis,)
            )
        dm = await self.view(True)
        for kind, _n in name_lists.kinds_in(dm):
            if kind != name_unnamed.NOT_NAMED:
                self.assertNotIn(sis, name_lists.browse_order(dm, kind, True))
        self.assertEqual(name_lists.browse_order(dm, name_unnamed.NOT_NAMED, True), [sis])
        campaign = await self.campaigns.get(GUILD_A, self.c)
        assert campaign is not None
        text, shown = names_ui.home_text(await self.view(False), campaign, 0)
        self.assertNotIn("sister", text + " ".join(n for _, n, _ in shown))

    async def test_the_group_page_says_who_and_offers_to_name_them(self) -> None:
        _ken, sis = await self.setup_people()
        dm = await self.view(True)
        campaign = await self.campaigns.get(GUILD_A, self.c)
        assert campaign is not None
        page = name_lists.Browse(self.c, dm, name_unnamed.NOT_NAMED)
        text = page.text(dm, campaign)
        self.assertIn("Not named yet", text)
        self.assertIn("Ken's sister (name unknown)", text)
        self.assertIn("not named yet", text)
        assert page.open is not None
        self.assertIn("Give them a name", page.open.placeholder or "")
        self.assertEqual([o.value for o in page.open.options], [sis])

    async def test_the_label_follows_the_anchors_name_in_the_list(self) -> None:
        ken, sis = await self.setup_people()
        await self.memory.rename_entity(GUILD_A, self.c, ken, "Kenneth", source="dm")
        dm = await self.view(True)
        self.assertEqual(name_unnamed.shown(dm, sis), "Kenneth's sister (name unknown)")

    async def test_one_hanging_off_another_unnamed_person_reads_through(self) -> None:
        _ken, sis = await self.setup_people()
        kid = (await self.memory.add_unnamed(GUILD_A, self.c, what="son", anchor_id=sis)).entity.id
        dm = await self.view(True)
        self.assertEqual(
            name_unnamed.shown(dm, kid), "Ken's sister (name unknown)'s son (name unknown)"
        )


class GivingTheName(MemoryTest):
    async def setUp2(self) -> tuple[str, str, Campaign]:
        ken = await self.add("Ken", status=CONFIRMED)
        sis = (
            await self.memory.add_unnamed(GUILD_A, self.c, what="sister", anchor_id=ken)
        ).entity.id
        campaign = await self.campaigns.get(GUILD_A, self.c)
        assert campaign is not None
        return ken, sis, campaign

    def interaction(self) -> Any:
        it = MagicMock()
        it.user.id = DM
        it.followup.send = AsyncMock()
        it.client.lookup.get = AsyncMock(side_effect=lambda g, c: self.lookup())
        return it

    def lookup(self) -> Any:
        raise NotImplementedError

    async def run_give(self, campaign: Campaign, entity_id: str, name: str) -> tuple[Any, Any]:
        data = await self.memory.lookup_data(GUILD_A, self.c)
        built = CampaignLookup.build(data)
        it = self.interaction()
        it.client.lookup.get = AsyncMock(return_value=built)
        replied = AsyncMock()
        with (
            patch.object(name_unnamed, "_answer_first", AsyncMock()),
            patch.object(name_unnamed, "_campaign_for", AsyncMock(return_value=campaign)),
            patch.object(name_unnamed, "_memory", lambda _it: self.memory),
            patch.object(name_unnamed, "sees_secrets", lambda c, u: True),
            patch.object(name_unnamed, "changed", lambda *a: None),
            patch.object(name_unnamed, "_replace", replied),
            patch.object(name_unnamed, "_tell", replied),
        ):
            await name_unnamed.give_name(it, self.c, entity_id, name)
        return it, replied

    async def test_a_new_name_names_them_and_offers_undo(self) -> None:
        _ken, sis, campaign = await self.setUp2()
        it, replied = await self.run_give(campaign, sis, "Mira")
        self.assertIn("is now **Mira**", (replied.await_args or MagicMock()).args[1])
        entity = await self.memory.entity(GUILD_A, self.c, sis)
        assert entity is not None
        self.assertEqual((entity.name, entity.unnamed), ("Mira", False))
        view = it.followup.send.await_args.kwargs["view"]
        [button] = view.children
        self.assertIsInstance(button, name_unnamed.UndoNamingButton)
        self.assertEqual(await self.memory.unnamed(GUILD_A, self.c), [])

    async def test_undo_makes_them_not_named_yet_again(self) -> None:
        _ken, sis, campaign = await self.setUp2()
        it, _ = await self.run_give(campaign, sis, "Mira")
        [button] = it.followup.send.await_args.kwargs["view"].children
        pressed = self.interaction()
        replied = AsyncMock()
        with (
            patch.object(name_unnamed, "_answer_first", AsyncMock()),
            patch.object(name_unnamed, "_campaign_for", AsyncMock(return_value=campaign)),
            patch.object(name_unnamed, "_memory", lambda _it: self.memory),
            patch.object(name_unnamed, "changed", lambda *a: None),
            patch.object(name_unnamed, "_replace", replied),
            patch.object(name_unnamed, "_tell", replied),
        ):
            await button.callback(pressed)
        [(entity, shown)] = await self.memory.unnamed(GUILD_A, self.c)
        self.assertEqual((entity.id, shown), (sis, "Ken's sister (name unknown)"))

    async def test_a_name_dmbot_knows_asks_first_and_changes_nothing(self) -> None:
        _ken, sis, campaign = await self.setUp2()
        mira = await self.add("Mira", status=CONFIRMED)
        _it, replied = await self.run_give(campaign, sis, "mira")
        ask = (replied.await_args or MagicMock()).args[2]
        self.assertIsInstance(ask, name_unnamed.JoinConfirm)
        self.assertIn("already known as **Mira**", (replied.await_args or MagicMock()).args[1])
        self.assertEqual(ask.other_id, mira)
        entity = await self.memory.entity(GUILD_A, self.c, sis)
        assert entity is not None
        self.assertTrue(entity.unnamed)  # nothing joined on its own

    async def test_saying_yes_joins_them_and_the_named_one_is_kept(self) -> None:
        _ken, sis, campaign = await self.setUp2()
        mira = await self.add("Mira", status=CONFIRMED)
        _it, replied = await self.run_give(campaign, sis, "Mira")
        ask = (replied.await_args or MagicMock()).args[2]
        pressed = self.interaction()
        shown = AsyncMock()
        with (
            patch.object(name_unnamed, "_answer_first", AsyncMock()),
            patch.object(name_unnamed, "_campaign_for", AsyncMock(return_value=campaign)),
            patch.object(name_unnamed, "_memory", lambda _it: self.memory),
            patch.object(name_unnamed, "changed", lambda *a: None),
            patch.object(name_unnamed, "_replace", AsyncMock()),
            patch("dmbot.ui.name_card.show_card", shown),
        ):
            await ask._yes(pressed)
        merged = {e.id: e for e in await self.memory.entities(GUILD_A, self.c, statuses=(MERGED,))}
        self.assertEqual(merged[sis].merged_into, mira)
        self.assertEqual((shown.await_args or MagicMock()).args[2], mira)
        self.assertEqual(await self.memory.unnamed(GUILD_A, self.c), [])
        pressed.followup.send.assert_awaited_once()

    async def test_two_entries_with_that_name_are_not_guessed(self) -> None:
        _ken, sis, campaign = await self.setUp2()
        await self.add("Mira", status=CONFIRMED)
        await self.add("Mira Voss", status=CONFIRMED)
        async with self.db.guild(GUILD_A) as conn:  # a second entry that is also called Mira
            await conn.execute(
                "INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key, kind,"
                " used_by, secret, status, sound_codes, source, created_at)"
                " SELECT guild_id, campaign_id, replace(gen_random_uuid()::text, '-', ''), id,"
                " 'Mira', 'mira', 'short', NULL, false, 'confirmed', '{}', 'dm', 1"
                " FROM memory_entities WHERE name = 'Mira Voss'"
            )
        _it, replied = await self.run_give(campaign, sis, "Mira")
        self.assertIn("More than one entry", (replied.await_args or MagicMock()).args[1])

    async def test_a_gone_or_already_named_person_and_an_empty_name_change_nothing(self) -> None:
        _ken, sis, campaign = await self.setUp2()
        await self.run_give(campaign, sis, "Mira")
        _it, replied = await self.run_give(campaign, sis, "Lia")  # already named
        self.assertEqual((replied.await_args or MagicMock()).args[1], name_unnamed.ALREADY_DONE)
        entity = await self.memory.entity(GUILD_A, self.c, sis)
        assert entity is not None
        self.assertEqual(entity.name, "Mira")
        _it, replied = await self.run_give(campaign, sis, "")
        self.assertEqual(entity.status, PROPOSED)

    async def test_only_a_dm_can_name_them(self) -> None:
        _ken, sis, campaign = await self.setUp2()
        it = self.interaction()
        replied = AsyncMock()
        with (
            patch.object(name_unnamed, "_answer_first", AsyncMock()),
            patch.object(name_unnamed, "_campaign_for", AsyncMock(return_value=campaign)),
            patch.object(name_unnamed, "_memory", lambda _it: self.memory),
            patch.object(name_unnamed, "sees_secrets", lambda c, u: False),
            patch.object(name_unnamed, "_tell", replied),
        ):
            await name_unnamed.give_name(it, self.c, sis, "Mira")
        self.assertIn("Only a DM", (replied.await_args or MagicMock()).args[1])
        [(entity, _)] = await self.memory.unnamed(GUILD_A, self.c)
        self.assertEqual(entity.id, sis)
