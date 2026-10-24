"""Delete this campaign (#1608): only the game master whose plan it uses; a typed name; not
while it is being played; checked again when the name arrives; the channels are tidied and the
other game masters told once. The store tests need Postgres."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot import retention_channels as rc
from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.dm_screen import delete_campaign as dc
from dmbot.dm_screen.settings import management_view
from tests import test_retention_channels as rcs
from tests.answer_first import Timeline
from tests.pg import DatabaseTest

GUILD, OTHER_GUILD = 111, 222
OWNER, OTHER_DM, PLAYER = 7, 9, 8
CID = "0123456789abcdef0123456789abcdef"


class Words(unittest.TestCase):
    def campaign(self, owner: int | None = OWNER, dms: frozenset[int] = frozenset({OWNER})) -> Any:
        return SimpleNamespace(name="Curse of  Strahd", owner_user_id=owner, dm_user_ids=dms)

    def test_only_the_owner_who_is_still_a_game_master_holds_the_plan(self) -> None:
        both = frozenset({OWNER, OTHER_DM})
        self.assertTrue(dc.holder_may(self.campaign(dms=both), OWNER))
        self.assertFalse(dc.holder_may(self.campaign(dms=both), OTHER_DM))
        self.assertFalse(dc.holder_may(self.campaign(dms=frozenset({OTHER_DM})), OWNER))
        # no owner yet (#2556): any game master may, and still only a game master
        self.assertTrue(dc.holder_may(self.campaign(owner=None, dms=both), OTHER_DM))
        self.assertFalse(dc.holder_may(self.campaign(owner=None, dms=frozenset({OTHER_DM})), OWNER))

    def test_the_typed_name_ignores_case_and_extra_spaces(self) -> None:
        c = self.campaign()
        self.assertTrue(dc.same_name("curse of strahd", c))
        self.assertTrue(dc.same_name("  CURSE   OF STRAHD ", c))
        self.assertFalse(dc.same_name("Curse of Strahd 2", c))
        self.assertFalse(dc.same_name("", c))

    def test_the_button_id_fits_discord_and_matches_its_template(self) -> None:
        custom_id = getattr(dc.DeleteButton(CID).item, "custom_id", None)
        assert custom_id is not None and len(custom_id) <= 100
        self.assertIsNotNone(dc.DeleteButton.__discord_ui_compiled_template__.fullmatch(custom_id))

    def test_the_settings_card_has_it_for_game_masters_only(self) -> None:
        from tests import test_dm_screen_settings as st

        def has_it(viewer: int) -> bool:
            children: list[Any] = list(management_view(st.campaign(), None, viewer).children)
            ids = [str(getattr(i, "item", i).custom_id) for i in children]
            return any(i.startswith("dmbot:delcampaign:") for i in ids)

        self.assertTrue(has_it(st.DM))
        self.assertFalse(has_it(st.PLAYER))


class Retire(unittest.IsolatedAsyncioTestCase):
    async def test_a_campaign_deleted_by_hand_is_not_blamed_on_nobody_playing_it(self) -> None:
        chat = rcs.channel(rcs.CHAT)
        guild = rcs.guild_with({rcs.CHAT: chat})
        only_chat = rcs.campaign(
            dm_screen_channel_id=None, gametime_channel_id=None, transcript_channel_id=None
        )
        await rc.retire_channels(guild, only_chat, rcs.ME, rcs.NOW, by_hand=True)
        await rc.retire_channels(guild, only_chat, rcs.ME, rcs.NOW)
        by_hand, by_time = (c.args[0] for c in chat.send.await_args_list)
        self.assertIn("This campaign was deleted on <t:1700000000:D>.", by_hand)
        self.assertNotIn("nobody played", by_hand)
        self.assertIn("nobody played it for a long time", by_time)


class Fake:
    """What the screen needs from the bot."""

    def __init__(self, campaigns: CampaignStore) -> None:
        self.campaigns = campaigns
        self.playing: set[str] = set()
        self.can_copy = True
        self._lock = asyncio.Lock()
        self.retired: list[tuple[Any, bool]] = []
        self.told: list[tuple[int, str]] = []

    def name_of(self, guild_id: int, user_id: int) -> str:
        return {OWNER: "Mira", OTHER_DM: "Tom"}.get(user_id, f"<@{user_id}>")

    async def is_campaign_playing(self, guild_id: int, campaign_id: str) -> bool:
        return campaign_id in self.playing

    def session_lock(self, guild_id: int) -> asyncio.Lock:
        return self._lock

    async def plan_gate(self, action: str, guild_id: int, campaign: Any, user_id: int) -> Any:
        return None if self.can_copy else "No copies on this plan."

    async def retire_campaign_channels(self, campaign: Any, now: int, *, by_hand: bool) -> None:
        self.retired.append((campaign, by_hand))

    async def dm_user(self, user_id: int, text: str) -> bool:
        self.told.append((user_id, text))
        return True


class Deleting(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.campaign = await self.store.create(GUILD, "Curse of Strahd", OWNER)
        await self.store.add_dm(GUILD, self.campaign.id, OTHER_DM)
        self.client = Fake(self.store)

    def interaction(self, user: int = OWNER, guild: int = GUILD) -> Any:
        done = {"yes": False}

        async def defer(**kw: Any) -> None:
            done["yes"] = True

        return SimpleNamespace(
            guild=SimpleNamespace(id=guild),
            user=SimpleNamespace(id=user),
            client=self.client,
            response=SimpleNamespace(
                send_message=AsyncMock(),
                send_modal=AsyncMock(),
                defer=AsyncMock(side_effect=defer),
                is_done=lambda: done["yes"],
                edit_message=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def exists(self) -> Campaign | None:
        return await self.store.get(GUILD, self.campaign.id)

    def said(self, it: Any) -> str:
        if it.response.send_message.await_args_list:
            return str(it.response.send_message.await_args_list[-1].args[0])
        return str(it.followup.send.await_args_list[-1].args[0])

    async def press(self, user: int = OWNER, guild: int = GUILD) -> Any:
        it = self.interaction(user, guild)
        await dc.DeleteButton(self.campaign.id).callback(it)
        return it

    async def test_the_plan_holder_gets_the_warning_with_a_backup_button(self) -> None:
        it = await self.press()
        kwargs = it.followup.send.await_args.kwargs
        text = it.followup.send.await_args.args[0]
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("deletes **Curse of Strahd** for everyone", text)
        self.assertIn("It can't be undone. Copies people already downloaded stay", text)
        self.assertIn("Sure? Press **Delete it**.", text)
        self.assertIn("press 💾 Back up first", text)
        labels = [str(b.label) for b in kwargs["view"].children]
        self.assertEqual(labels, ["Delete it", "Back up first", "Keep it"])

    async def no_owner(self) -> None:
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = NULL WHERE id = %s", (self.campaign.id,)
            )

    async def test_with_no_owner_any_game_master_may_delete_and_the_warning_says_why(self) -> None:
        await self.no_owner()
        it = await self.press(OTHER_DM)
        text = it.followup.send.await_args.args[0]
        self.assertIn("This campaign has no owner, so any of its game masters may delete it.", text)
        done = self.interaction(OTHER_DM)
        self.assertTrue(await dc.delete_now(done, self.campaign.id, "Curse of Strahd"))
        self.assertIsNone(await self.exists())

    async def test_with_an_owner_a_co_dm_is_still_refused_with_the_owners_name(self) -> None:
        it = await self.press(OTHER_DM)
        self.assertEqual(self.said(it), dc.NOT_YOURS.format(who="Mira"))
        self.assertIsNotNone(await self.exists())

    async def test_with_no_owner_someone_who_is_not_a_game_master_is_refused(self) -> None:
        await self.no_owner()
        it = await self.press(555)
        self.assertEqual(self.said(it), dc.ONLY_GMS)
        self.assertIsNotNone(await self.exists())

    async def test_with_no_owner_a_running_session_still_stops_it(self) -> None:
        await self.no_owner()
        self.client.playing.add(self.campaign.id)
        it = await self.press(OTHER_DM)
        self.assertIn("is being played right now", self.said(it))
        self.assertIsNotNone(await self.exists())

    async def test_the_delete_checks_again_under_the_lock_for_a_campaign_with_no_owner(
        self,
    ) -> None:
        await self.no_owner()
        with self.assertRaisesRegex(CampaignError, "changed just now"):
            await self.store.delete(GUILD, self.campaign.id, only_if_owner=555)
        await self.store.delete(GUILD, self.campaign.id, only_if_owner=OTHER_DM)
        self.assertIsNone(await self.exists())

    async def test_the_warning_answers_discord_before_the_database(self) -> None:
        line = Timeline()
        it = self.interaction()
        it.response, it.followup = line.response, line.followup
        real_get = self.client.campaigns.get
        # (a stand-in with only the read the button makes; the cast is for mypy alone)
        self.client.campaigns = cast(
            CampaignStore,
            SimpleNamespace(get=line.slow("get", await real_get(GUILD, self.campaign.id))),
        )
        await dc.DeleteButton(self.campaign.id).callback(it)
        line.assert_answered_first()
        self.assertIn("slow:get", line.events)

    async def test_no_backup_button_where_there_are_no_copies(self) -> None:
        self.client.can_copy = False
        it = await self.press()
        text = it.followup.send.await_args.args[0]
        self.assertNotIn("Back up", text)
        labels = [str(b.label) for b in it.followup.send.await_args.kwargs["view"].children]
        self.assertEqual(labels, ["Delete it", "Keep it"])

    async def test_another_game_master_is_told_whose_it_is(self) -> None:
        it = await self.press(OTHER_DM)
        self.assertEqual(
            self.said(it),
            "Only Mira, whose plan this campaign uses, can delete it. Ask them to do it.",
        )
        self.assertIsNotNone(await self.exists())

    async def test_a_player_is_refused(self) -> None:
        it = await self.press(PLAYER)
        self.assertEqual(self.said(it), dc.ONLY_GMS)
        self.assertIsNotNone(await self.exists())

    async def test_not_while_a_session_is_running(self) -> None:
        self.client.playing.add(self.campaign.id)
        it = await self.press()
        self.assertIn("Stop the session first", self.said(it))
        it = self.interaction()
        self.assertFalse(await dc.delete_now(it, self.campaign.id, "Curse of Strahd"))
        self.assertIsNotNone(await self.exists())

    async def test_a_wrong_name_deletes_nothing(self) -> None:
        it = self.interaction()
        self.assertFalse(await dc.delete_now(it, self.campaign.id, "Curse"))
        self.assertEqual(self.said(it), dc.WRONG_NAME.format(name="Curse of Strahd"))
        self.assertIsNotNone(await self.exists())
        self.assertEqual((self.client.retired, self.client.told), ([], []))

    async def test_the_right_name_in_any_case_deletes_it_and_tidies_up(self) -> None:
        it = self.interaction()
        self.assertTrue(await dc.delete_now(it, self.campaign.id, "  curse OF strahd "))
        self.assertIsNone(await self.exists())
        [(retired, by_hand)] = self.client.retired
        self.assertEqual((retired.id, by_hand), (self.campaign.id, True))
        self.assertIn("🗑 Deleted **Curse of Strahd**.", self.said(it))

    async def test_the_other_game_masters_are_told_once_and_not_the_deleter(self) -> None:
        await dc.delete_now(self.interaction(), self.campaign.id, "Curse of Strahd")
        self.assertEqual(self.client.told, [(OTHER_DM, "🗑 Mira deleted **Curse of Strahd**.")])

    async def test_a_failed_message_does_not_undo_the_delete(self) -> None:
        self.client.dm_user = AsyncMock(side_effect=RuntimeError("messages off"))  # type: ignore[method-assign]
        self.assertTrue(
            await dc.delete_now(self.interaction(), self.campaign.id, "Curse of Strahd")
        )
        self.assertIsNone(await self.exists())

    async def test_the_name_is_checked_against_the_campaign_read_again(self) -> None:
        await self.store.rename(GUILD, self.campaign.id, "Lost Mine")
        it = self.interaction()
        self.assertFalse(await dc.delete_now(it, self.campaign.id, "Curse of Strahd"))
        self.assertIsNotNone(await self.exists())

    async def test_a_game_master_taken_off_since_the_warning_is_refused(self) -> None:
        # (the owner can't be removed as a game master; a hand-over moves the plan instead)
        it = self.interaction(OTHER_DM)
        self.assertFalse(await dc.delete_now(it, self.campaign.id, "Curse of Strahd"))
        self.assertIsNotNone(await self.exists())

    async def test_a_campaign_of_another_server_cannot_be_reached(self) -> None:
        it = self.interaction(OWNER, OTHER_GUILD)
        self.assertFalse(await dc.delete_now(it, self.campaign.id, "Curse of Strahd"))
        self.assertEqual(self.said(it), dc.GONE)
        self.assertIsNotNone(await self.exists())

    async def test_a_campaign_already_gone_is_said_plainly(self) -> None:
        await self.store.delete(GUILD, self.campaign.id)
        it = await self.press()
        self.assertEqual(self.said(it), dc.GONE)

    async def test_the_delete_form_opens_the_name_box(self) -> None:
        it = await self.press()
        view = it.followup.send.await_args.kwargs["view"]
        pressed = self.interaction()
        await view.delete.callback(pressed)
        form = pressed.response.send_modal.await_args.args[0]
        self.assertIsInstance(form, dc.ConfirmForm)
        self.assertLessEqual(len(str(form.name_box.label)), 45)

    async def test_a_hand_over_between_the_look_and_the_delete_stops_it(self) -> None:
        async with self.db.guild(GUILD) as conn:  # the plan moved after the checks passed
            await conn.execute(
                "UPDATE campaigns SET owner_user_id = %s WHERE id = %s",
                (OTHER_DM, self.campaign.id),
            )
        with self.assertRaisesRegex(CampaignError, "changed just now"):
            await self.store.delete(GUILD, self.campaign.id, only_if_owner=OWNER)
        self.assertIsNotNone(await self.exists())
        await self.store.delete(GUILD, self.campaign.id, only_if_owner=OTHER_DM)
        self.assertIsNone(await self.exists())

    async def test_the_delete_waits_for_a_session_start_holding_the_lock(self) -> None:
        async with self.client.session_lock(GUILD):
            task = asyncio.ensure_future(
                dc.delete_now(self.interaction(), self.campaign.id, "Curse of Strahd")
            )
            await asyncio.sleep(0.2)
            self.assertFalse(task.done())
            self.assertIsNotNone(await self.exists())
        self.assertTrue(await task)

    async def test_the_deleter_hears_before_the_others_are_messaged(self) -> None:
        order: list[str] = []
        it = self.interaction()
        it.response.is_done = lambda: True
        it.followup.send = AsyncMock(side_effect=lambda *a, **k: order.append("deleter"))
        self.client.dm_user = AsyncMock(side_effect=lambda *a: order.append("other"))  # type: ignore[method-assign]
        await dc.delete_now(it, self.campaign.id, "Curse of Strahd")
        self.assertEqual(order, ["deleter", "other"])

    async def test_two_presses_delete_it_once(self) -> None:
        results = await asyncio.gather(
            dc.delete_now(self.interaction(), self.campaign.id, "Curse of Strahd"),
            dc.delete_now(self.interaction(), self.campaign.id, "Curse of Strahd"),
        )
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(len(self.client.retired), 1)
