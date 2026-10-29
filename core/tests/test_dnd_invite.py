"""The DM's D&D Beyond campaign invite (#1119; docs/PLAN.md, "The D&D Beyond campaign
invite"): which links are kept, where it is stored and backed up, the Join button on the
messages players see, the DM's field and button, and a character made from a player's sheet.
The store tests need Postgres; the rest use fakes."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.campaigns.invite import NOT_AN_INVITE, parse_invite
from dmbot.consent_dm import JOIN_LABEL, menu_view, request_view
from dmbot.dm_screen import settings as screen_settings
from dmbot.dm_screen import transcript_channel as transcript
from dmbot.memory import sheets
from dmbot.memory.sheet_store import PlayerCharacter, SheetStore
from dmbot.ui import dmbot_commands as cmds
from dmbot.ui import sheets as ui
from tests.pg import DatabaseTest
from tests.test_sheets import answer

GUILD, OTHER_GUILD, DM, PLAYER = 111, 222, 7, 9
LINK = "https://www.dndbeyond.com/campaigns/join/AbC123xyz"
CAMPAIGN = "c" * 32
ENTITY = "e" * 32


class Links(unittest.TestCase):
    def test_the_standard_link_is_kept_with_or_without_www(self) -> None:
        for given in (
            LINK,
            "https://dndbeyond.com/campaigns/join/AbC123xyz",
            f"  {LINK}  ",
            LINK + "/",
            LINK + "\n",
        ):
            self.assertEqual(parse_invite(given), LINK, given)

    def test_only_the_campaigns_join_shape_is_accepted(self) -> None:
        with self.assertRaises(CampaignError):
            parse_invite("https://www.dndbeyond.com/join-campaign/AbC123xyz")

    def test_an_empty_box_removes_the_link(self) -> None:
        self.assertIsNone(parse_invite(""))
        self.assertIsNone(parse_invite("   "))

    def test_anything_else_is_refused_with_one_plain_line(self) -> None:
        for bad in (
            "http://www.dndbeyond.com/campaigns/join/AbC123xyz",  # not https
            "https://www.dndbeyond.com/campaigns/12345",  # another D&D Beyond page
            "https://www.dndbeyond.com/characters/12345678",
            "https://www.dndbeyond.com/campaigns/join/",
            "https://www.dndbeyond.com/campaigns/join/ab",  # too short to be a code
            "https://www.dndbeyond.com/campaigns/join/AbC123xyz?x=1",
            "https://www.dndbeyond.com/campaigns/join/AbC123xyz#frag",
            "https://www.dndbeyond.com.evil.example/campaigns/join/AbC123xyz",
            "https://evildndbeyond.com/campaigns/join/AbC123xyz",
            "https://user:pw@www.dndbeyond.com/campaigns/join/AbC123xyz",
            "https://www.dndbeyond.com:8080/campaigns/join/AbC123xyz",
            "https://ww2.dndbeyond.com/campaigns/join/AbC123xyz",
            "join my campaign " + LINK,
            LINK + " join us",
            "https://example.com/",
            "javascript:alert(1)",
            "https://www.dndbeyond.com/campaigns/join/AbC12\n3xyz",
            "https://www.dndbeyond.com/campaigns/join/AbC123xyz\nhttps://evil.example",
        ):
            with self.assertRaises(CampaignError, msg=bad) as caught:
                parse_invite(bad)
            self.assertEqual(str(caught.exception), NOT_AN_INVITE)


class Stored(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.campaign = await self.store.create(GUILD, "Rime", DM)

    async def test_it_is_kept_changed_and_removed(self) -> None:
        self.assertIsNone(self.campaign.dndbeyond_invite)
        saved = await self.store.set_dndbeyond_invite(GUILD, self.campaign.id, LINK)
        self.assertEqual(saved.dndbeyond_invite, LINK)
        got = await self.store.get(GUILD, self.campaign.id)
        assert got is not None and got.dndbeyond_invite == LINK
        other = "https://dndbeyond.com/campaigns/join/Zz99Zz99"
        changed = await self.store.set_dndbeyond_invite(GUILD, self.campaign.id, other)
        self.assertEqual(
            changed.dndbeyond_invite, "https://www.dndbeyond.com/campaigns/join/Zz99Zz99"
        )
        gone = await self.store.set_dndbeyond_invite(GUILD, self.campaign.id, None)
        self.assertIsNone(gone.dndbeyond_invite)

    async def test_the_store_refuses_what_is_not_an_invite(self) -> None:
        with self.assertRaises(CampaignError):
            await self.store.set_dndbeyond_invite(GUILD, self.campaign.id, "https://example.com/")

    async def test_the_database_refuses_it_too(self) -> None:
        import psycopg

        async with self.db.guild(GUILD) as conn:
            with self.assertRaises(psycopg.errors.CheckViolation):
                await conn.execute(
                    "UPDATE campaigns SET dndbeyond_invite = 'https://example.com/' WHERE id = %s",
                    (self.campaign.id,),
                )

    async def test_it_is_in_a_backup_but_a_new_campaign_made_from_it_gets_none(self) -> None:
        await self.store.set_dndbeyond_invite(GUILD, self.campaign.id, LINK)
        data = await self.store.export(GUILD, self.campaign.id)
        self.assertEqual(data["campaign"]["dndbeyond_invite"], LINK)
        # It is the original DM's join link for the original table: a copy made by someone
        # else, here or in another server, doesn't show it to its own players.
        copy = await self.store.import_backup(OTHER_GUILD, data, DM)
        self.assertIsNone(copy.dndbeyond_invite)

    async def test_replacing_a_campaign_keeps_its_own_link_and_ignores_the_backups(self) -> None:
        # A backup doesn't record its server, so its link could be another server's table.
        await self.store.set_dndbeyond_invite(GUILD, self.campaign.id, LINK)
        data = await self.store.export(GUILD, self.campaign.id)
        other = "https://www.dndbeyond.com/campaigns/join/Other999"
        await self.store.set_dndbeyond_invite(GUILD, self.campaign.id, other)
        kept = await self.store.import_backup(GUILD, data, DM, replace_campaign_id=self.campaign.id)
        self.assertEqual(kept.dndbeyond_invite, other)
        await self.store.set_dndbeyond_invite(GUILD, self.campaign.id, None)
        none = await self.store.import_backup(GUILD, data, DM, replace_campaign_id=self.campaign.id)
        self.assertIsNone(none.dndbeyond_invite)

    async def test_a_new_campaign_is_made_with_its_link_in_one_step(self) -> None:
        made = await self.store.create(GUILD, "Linked", DM, dndbeyond_invite=LINK)
        self.assertEqual(made.dndbeyond_invite, LINK)
        with self.assertRaises(CampaignError):
            await self.store.create(GUILD, "Bad link", DM, dndbeyond_invite="https://example.com/")
        self.assertEqual(
            sorted(c.name for c in await self.store.list_campaigns(GUILD)),
            sorted(["Linked", self.campaign.name]),
        )

    async def test_a_backup_from_before_has_none_and_a_damaged_link_is_refused(self) -> None:
        data = await self.store.export(GUILD, self.campaign.id)
        del data["campaign"]["dndbeyond_invite"]
        restored = await self.store.import_backup(OTHER_GUILD, data, DM)
        self.assertIsNone(restored.dndbeyond_invite)
        for bad in (
            "https://example.com/",
            5,
            "  " + LINK,
            "http://www.dndbeyond.com/campaigns/join/AbC123xyz",
        ):
            data["campaign"]["dndbeyond_invite"] = bad
            with self.assertRaises(CampaignError, msg=str(bad)):
                await self.store.import_backup(OTHER_GUILD, data, DM)

    async def test_it_goes_with_the_campaign_and_is_not_shared(self) -> None:
        await self.store.set_dndbeyond_invite(GUILD, self.campaign.id, LINK)
        other = await self.store.create(GUILD, "Elsewhere", DM)
        got = await self.store.get(GUILD, other.id)
        assert got is not None
        self.assertIsNone(got.dndbeyond_invite)
        await self.store.delete(GUILD, self.campaign.id)
        self.assertIsNone(await self.store.get(GUILD, self.campaign.id))


def link_buttons(view: discord.ui.View) -> list[Any]:
    return [i for i in view.children if getattr(i, "url", None)]


class JoinButton(unittest.TestCase):
    def test_it_shows_only_when_a_link_is_set(self) -> None:
        self.assertEqual(link_buttons(request_view(GUILD)), [])
        self.assertEqual(link_buttons(menu_view(GUILD, CAMPAIGN)), [])
        for view in (request_view(GUILD, invite=LINK), menu_view(GUILD, CAMPAIGN, invite=LINK)):
            [button] = link_buttons(view)
            self.assertEqual((button.label, button.url), (JOIN_LABEL, LINK))
            self.assertEqual(button.style, discord.ButtonStyle.link)

    def test_the_other_buttons_are_unchanged(self) -> None:
        plain = [getattr(i, "custom_id", None) for i in request_view(GUILD).children]
        with_link = [
            getattr(i, "custom_id", None) for i in request_view(GUILD, invite=LINK).children
        ]
        self.assertEqual(with_link[: len(plain)], plain)


class Card(unittest.IsolatedAsyncioTestCase):
    """The transcript channel's pinned card carries the Join button, posted again when the
    link changes."""

    def channel(self, existing: list[Any]) -> Any:
        channel = MagicMock(spec=discord.TextChannel)
        channel.pins = AsyncMock(return_value=existing)

        async def history(limit: int = 0) -> Any:
            empty: list[Any] = []
            for message in empty:
                yield message

        channel.history = history
        channel.send = AsyncMock(return_value=MagicMock(pin=AsyncMock()))
        return channel

    def me(self) -> Any:
        return SimpleNamespace(id=2)

    def campaign(self, invite: str | None) -> Any:
        return SimpleNamespace(name="Rime", dndbeyond_invite=invite)

    async def test_a_new_card_has_the_button_only_with_a_link(self) -> None:
        for invite in (LINK, None):
            channel = self.channel([])
            await transcript._ensure_card(channel, self.campaign(invite), self.me())
            sent = channel.send.await_args
            view = sent.kwargs.get("view")
            if invite:
                [button] = link_buttons(view)
                self.assertEqual(button.url, LINK)
            else:
                self.assertIsNone(view)

    async def test_a_card_with_the_right_link_is_left_alone_and_a_changed_one_is_replaced(
        self,
    ) -> None:
        from dmbot.dm_screen import messages

        text = messages.transcript_card("Rime")
        card = MagicMock(spec=discord.Message)
        card.id = 5
        card.author = self.me()
        card.content = text
        card.pinned = True
        card.delete = AsyncMock()
        card.components = [SimpleNamespace(children=[SimpleNamespace(label=JOIN_LABEL, url=LINK)])]
        same = self.channel([card])
        await transcript._ensure_card(same, self.campaign(LINK), self.me())
        same.send.assert_not_awaited()
        card.delete.assert_not_awaited()
        moved = self.channel([card])
        await transcript._ensure_card(
            moved, self.campaign("https://www.dndbeyond.com/campaigns/join/Other999"), self.me()
        )
        card.delete.assert_awaited()
        moved.send.assert_awaited()
        removed = self.channel([card])
        card.delete.reset_mock()
        await transcript._ensure_card(removed, self.campaign(None), self.me())
        card.delete.assert_awaited()


def settings_interaction(user: int) -> Any:
    store = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                id=CAMPAIGN, guild_id=GUILD, dm_user_ids=frozenset({DM}), dndbeyond_invite=None
            )
        ),
        set_dndbeyond_invite=AsyncMock(
            return_value=SimpleNamespace(guild_id=GUILD, id=CAMPAIGN, dndbeyond_invite=LINK)
        ),
    )
    follow = AsyncMock()
    it = MagicMock()
    it.user = MagicMock(id=user)
    it.guild = MagicMock(id=GUILD)
    it.client = SimpleNamespace(campaigns=store, after_invite_change=follow)
    it.response.send_message = AsyncMock()
    it.response.send_modal = AsyncMock()
    return it


class DmSide(unittest.IsolatedAsyncioTestCase):
    def campaign(self, invite: str | None = None) -> Any:
        from dmbot.campaigns import Campaign

        return Campaign(
            id=CAMPAIGN, guild_id=GUILD, name="Rime", created_at=1,
            last_played_at=None, target_ruleset="2024", fallback_ruleset="2014",
            optional_rules_default=True, dm_user_ids=frozenset({DM}), dm_screen_channel_id=None,
            last_voice_channel_id=None, dm_screen_visibility="peek", dndbeyond_invite=invite,
        )  # fmt: skip

    def test_settings_says_whether_it_is_set_and_only_dms_get_the_button(self) -> None:
        campaign = self.campaign()
        view = screen_settings.settings_view(campaign, None, DM)
        self.assertTrue(
            any(
                "dmbot:invite:" in str(getattr(i, "custom_id", ""))
                or (
                    isinstance(getattr(i, "item", None), discord.ui.Button)
                    and "invite" in str(cast(Any, i).item.custom_id)
                )
                for i in view.children
            )
        )
        player_view = screen_settings.settings_view(campaign, None, PLAYER)
        self.assertFalse(
            any(
                "invite" in str(getattr(getattr(i, "item", i), "custom_id", ""))
                for i in player_view.children
            )
        )
        # (the card has no line for it any more: it waits on the "Not on the website yet" list)
        self.assertIn("D&D Beyond invite", screen_settings.settings_text(campaign, None, DM))

    def test_the_buttons_id_matches_its_template(self) -> None:
        custom_id = screen_settings.InviteButton(CAMPAIGN).item.custom_id
        assert custom_id is not None
        self.assertIsNotNone(
            screen_settings.InviteButton.__discord_ui_compiled_template__.fullmatch(custom_id)
        )

    async def test_a_dm_saves_changes_and_removes_it(self) -> None:
        it = settings_interaction(DM)
        form = screen_settings.InviteForm(self.campaign())
        form.link._value = "https://dndbeyond.com/campaigns/join/AbC123xyz"
        with patch.object(screen_settings, "_allowed", AsyncMock(return_value=self.campaign())):
            await form.on_submit(it)
        it.client.campaigns.set_dndbeyond_invite.assert_awaited_with(GUILD, CAMPAIGN, LINK)
        it.client.after_invite_change.assert_awaited_once()
        self.assertIn("Join on D&D Beyond", it.response.send_message.await_args.args[0])
        form.link._value = ""
        with patch.object(screen_settings, "_allowed", AsyncMock(return_value=self.campaign())):
            await form.on_submit(it)
        it.client.campaigns.set_dndbeyond_invite.assert_awaited_with(GUILD, CAMPAIGN, None)

    async def test_a_link_that_is_not_an_invite_saves_nothing(self) -> None:
        it = settings_interaction(DM)
        form = screen_settings.InviteForm(self.campaign())
        form.link._value = "https://example.com/"
        with patch.object(screen_settings, "_allowed", AsyncMock(return_value=self.campaign())):
            await form.on_submit(it)
        it.client.campaigns.set_dndbeyond_invite.assert_not_awaited()
        self.assertEqual(it.response.send_message.await_args.args[0], NOT_AN_INVITE)

    async def test_a_player_pressing_the_button_gets_no_form(self) -> None:
        it = settings_interaction(PLAYER)
        await screen_settings.InviteButton(CAMPAIGN).callback(it)
        it.response.send_modal.assert_not_awaited()
        self.assertEqual(it.response.send_message.await_args.args[0], screen_settings.ONLY_DM)
        dm = settings_interaction(DM)
        await screen_settings.InviteButton(CAMPAIGN).callback(dm)
        dm.response.send_modal.assert_awaited_once()

    async def test_the_new_campaign_form_refuses_a_bad_link_before_anything_is_made(self) -> None:
        it = MagicMock()
        it.guild = MagicMock(id=GUILD)
        it.client = SimpleNamespace(
            campaigns=SimpleNamespace(list_campaigns=AsyncMock(return_value=[]))
        )
        it.response.is_done = MagicMock(return_value=False)
        it.response.send_message = AsyncMock()
        form = cmds.NewCampaignForm()
        form.name._value = "Rime"
        form.invite._value = "https://example.com/"
        await form.on_submit(it)
        it.client.campaigns.list_campaigns.assert_not_awaited()
        self.assertEqual(it.response.send_message.await_args.args[0], NOT_AN_INVITE)

    async def test_create_hands_the_link_to_the_store_and_a_failure_is_told(self) -> None:
        # The link is saved with the campaign, so there is no half-made campaign whose
        # "link wasn't saved" warning gets overwritten by the next step.
        create = AsyncMock(return_value=SimpleNamespace())
        it: Any = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD),
            user=SimpleNamespace(id=DM),
            client=SimpleNamespace(
                campaigns=SimpleNamespace(create=create),
                create_refusal=AsyncMock(return_value=None),
            ),
        )
        with patch.object(cmds, "show_voice_step", AsyncMock()):
            await cmds.NewCampaignSettings("Rime", LINK)._create(it)
        self.assertEqual(create.call_args.kwargs["dndbeyond_invite"], LINK)
        create.side_effect = CampaignError("That isn't a D&D Beyond invite link.")
        told = AsyncMock()
        with (
            patch.object(cmds, "show_voice_step", AsyncMock()) as voice,
            patch.object(cmds, "_tell", told),
        ):
            await cmds.NewCampaignSettings("Rime", LINK)._create(it)
        voice.assert_not_awaited()
        told.assert_awaited_once()

    def test_the_new_campaign_settings_carry_the_checked_link(self) -> None:
        view = cmds.NewCampaignSettings("Rime", LINK)
        self.assertEqual(view.invite, LINK)
        self.assertIsNone(cmds.NewCampaignSettings("Rime").invite)


def sheet_interaction(*, found: list[PlayerCharacter] | None = None) -> Any:
    store = MagicMock(spec=SheetStore)
    store.characters_of = AsyncMock(return_value=found or [])
    store.sheet = AsyncMock(return_value=None)
    store.link = AsyncMock()
    store.save = AsyncMock(return_value=True)
    memory = SimpleNamespace(
        add_entity=AsyncMock(
            return_value=SimpleNamespace(value=SimpleNamespace(id=ENTITY, name="Test Character"))
        )
    )
    campaign = SimpleNamespace(
        id=CAMPAIGN,
        guild_id=GUILD,
        name="Rime",
        dm_screen_channel_id=555,
        dm_user_ids=frozenset({DM}),
    )
    bot = SimpleNamespace(
        sheets=store,
        memory=memory,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
        lookup=None,
        sheets_changed=MagicMock(),
        post_message=AsyncMock(),
        get_guild=MagicMock(return_value=MagicMock(get_member=MagicMock(return_value=MagicMock()))),
    )
    it = MagicMock()
    it.user = MagicMock(id=PLAYER)
    it.guild = None
    it.type = discord.InteractionType.component
    it.client = bot
    done = {"yes": False}

    async def answered(*_: Any, **__: Any) -> None:
        done["yes"] = True

    for name in ("send_message", "edit_message", "defer", "send_modal"):
        setattr(it.response, name, AsyncMock(side_effect=answered))
    it.response.is_done = MagicMock(side_effect=lambda: done["yes"])
    it.followup.send = AsyncMock()
    it.edit_original_response = AsyncMock()
    return it


def said(it: Any) -> str:
    call = it.followup.send.await_args or it.response.send_message.await_args
    return str(call.args[0])


class FromTheSheet(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        ui._adding.clear()
        ui._last_link.clear()

    async def test_a_player_with_no_character_gets_the_link_button_on_a_sessions_reminder(
        self,
    ) -> None:
        it = sheet_interaction()
        await ui.MySheetButton(GUILD, CAMPAIGN).callback(it)
        # The form opens on the same press (#2986); the panel with its one button is the fallback
        # when the read is slow.
        it.response.send_modal.assert_awaited_once()
        self.assertIsInstance(it.response.send_modal.await_args.args[0], ui.NewSheetForm)
        slow = sheet_interaction()
        read = slow.client.sheets.characters_of

        async def slow_read(*args: Any) -> Any:
            await asyncio.sleep(0.2)
            return await read(*args)

        slow.client.sheets.characters_of = slow_read
        with patch.object(ui, "FORM_WAIT_S", 0.01):
            await ui.MySheetButton(GUILD, CAMPAIGN).callback(slow)
        self.assertIn("Link my D&D Beyond sheet", said(slow))
        call = slow.followup.send.await_args or slow.response.send_message.await_args
        self.assertEqual([i.label for i in call.kwargs["view"].children], [ui.LINK_LABEL])
        # From the consent confirmation (no campaign) it still says who adds it.
        plain = sheet_interaction()
        await ui.MySheetButton(GUILD).callback(plain)
        self.assertEqual(said(plain), ui.NO_CHARACTER)

    async def test_a_sheet_dmbot_can_read_makes_the_character(self) -> None:
        it = sheet_interaction()
        with patch.object(sheets, "fetch", AsyncMock(return_value=sheets.parse(answer()))):
            await ui.add_from_sheet(it, GUILD, CAMPAIGN, 42)
        bot = it.client
        bot.memory.add_entity.assert_awaited_once()
        kwargs = bot.memory.add_entity.await_args.kwargs
        self.assertEqual((kwargs["type"], kwargs["played_by"]), ("player_character", PLAYER))
        bot.sheets.link.assert_awaited_once_with(GUILD, CAMPAIGN, ENTITY, 42, player=PLAYER)
        bot.sheets.save.assert_awaited_once()
        self.assertIn("added", said(it))
        # One line for the DM, with the card's button.
        channel_id, text, view = bot.post_message.await_args.args
        self.assertEqual(channel_id, 555)
        self.assertEqual(
            text,
            ui.DM_ADDED.format(name="Test Character"),
        )
        [button] = view.children
        self.assertEqual(button.item.custom_id, f"dmbot:namecard:{CAMPAIGN}:{ENTITY}")

    async def test_a_sheet_it_cannot_read_makes_nothing(self) -> None:
        for error, expect in (
            (sheets.SheetError(sheets.NOT_PUBLIC, refused=True), "Character Privacy"),
            (sheets.SheetError("down"), "couldn't reach"),
        ):
            it = sheet_interaction()
            with patch.object(sheets, "fetch", AsyncMock(side_effect=error)):
                await ui.add_from_sheet(it, GUILD, CAMPAIGN, 42)
            it.client.memory.add_entity.assert_not_awaited()
            it.client.sheets.link.assert_not_awaited()
            it.client.post_message.assert_not_awaited()
            self.assertIn(expect, said(it))
            self.assertIn("Nothing was added", said(it))

    async def test_a_second_link_by_the_same_player_does_not_make_a_second_character(self) -> None:
        mine = PlayerCharacter(CAMPAIGN, "Rime", ENTITY, "Test Character")
        it = sheet_interaction(found=[mine])
        read = AsyncMock(return_value=sheets.parse(answer()))
        with patch.object(sheets, "fetch", read), patch.object(ui, "show_panel", AsyncMock()):
            await ui.add_from_sheet(it, GUILD, CAMPAIGN, 43)
        it.client.memory.add_entity.assert_not_awaited()  # linked to the one it knows
        it.client.sheets.link.assert_awaited_once_with(GUILD, CAMPAIGN, ENTITY, 43, player=PLAYER)
        it.client.post_message.assert_not_awaited()

    async def test_two_links_at_once_make_one_character(self) -> None:
        import asyncio

        it = sheet_interaction()
        made: list[bool] = []

        async def characters_of(*_: Any, **__: Any) -> list[PlayerCharacter]:
            return [PlayerCharacter(CAMPAIGN, "Rime", ENTITY, "Test Character")] if made else []

        async def add_entity(*_: Any, **__: Any) -> Any:
            made.append(True)
            await asyncio.sleep(0)
            return SimpleNamespace(value=SimpleNamespace(id=ENTITY, name="Test Character"))

        it.client.sheets.characters_of = characters_of
        it.client.memory.add_entity = AsyncMock(side_effect=add_entity)
        with (
            patch.object(sheets, "fetch", AsyncMock(return_value=sheets.parse(answer()))),
            patch.object(ui, "show_panel", AsyncMock()),
        ):
            await asyncio.gather(
                ui.add_from_sheet(it, GUILD, CAMPAIGN, 42),
                ui.add_from_sheet(it, GUILD, CAMPAIGN, 42),
            )
        self.assertEqual(it.client.memory.add_entity.await_count, 1)

    async def test_a_sheet_with_no_name_makes_no_character(self) -> None:
        it = sheet_interaction()
        nameless = sheets.parse(answer())
        nameless["name"] = ""
        with patch.object(sheets, "fetch", AsyncMock(return_value=nameless)):
            await ui.add_from_sheet(it, GUILD, CAMPAIGN, 42)
        it.client.memory.add_entity.assert_not_awaited()
        self.assertEqual(said(it), ui.CHARACTER_NOT_ADDED)

    async def test_the_dm_still_hears_of_a_character_whose_link_failed_and_the_lock_is_released(
        self,
    ) -> None:
        it = sheet_interaction()
        it.client.sheets.link = AsyncMock(side_effect=RuntimeError("db"))
        fetched = patch.object(sheets, "fetch", AsyncMock(return_value=sheets.parse(answer())))
        with fetched, self.assertRaises(RuntimeError):
            await ui.add_from_sheet(it, GUILD, CAMPAIGN, 42)
        it.client.sheets_changed.assert_called()
        it.client.post_message.assert_awaited_once()  # the DM still hears of the character
        self.assertEqual(ui._adding, {})  # no lock kept for a player who is done

    async def test_a_link_that_is_not_a_character_link_does_nothing(self) -> None:
        it = sheet_interaction()
        form = ui.NewSheetForm(GUILD, CAMPAIGN)
        form.link._value = "https://example.com/"
        await form.on_submit(it)
        it.client.memory.add_entity.assert_not_awaited()
        self.assertEqual(said(it), sheets.NOT_A_LINK)

    async def test_the_cards_button_is_for_the_campaigns_dms(self) -> None:
        button = ui.OpenCardButton(CAMPAIGN, ENTITY)
        custom_id = button.item.custom_id
        assert custom_id is not None
        self.assertIsNotNone(
            ui.OpenCardButton.__discord_ui_compiled_template__.fullmatch(custom_id)
        )
        with patch("dmbot.ui.name_card.show_card", AsyncMock()) as show:
            await button.callback(MagicMock())
        show.assert_awaited_once()


class KeepsTheButton(unittest.TestCase):
    """A new view for an edited message keeps the Join button the message already has."""

    def message(self, url: str | None) -> Any:
        parts = (
            [SimpleNamespace(label=JOIN_LABEL, url=url)]
            if url
            else [SimpleNamespace(label="x", url=None)]
        )
        return SimpleNamespace(components=[SimpleNamespace(children=parts)])

    def test_the_link_survives_the_players_next_press(self) -> None:
        from dmbot.consent_dm import consent_view, keep_invite, menu_view, warning_view

        for view in (menu_view(GUILD), consent_view(GUILD), warning_view(GUILD)):
            it = cast(Any, SimpleNamespace(message=self.message(LINK), client=SimpleNamespace()))
            [button] = link_buttons(keep_invite(it, view, GUILD))
            self.assertEqual(button.url, LINK)

    def test_nothing_is_added_where_there_was_nothing_or_already_one(self) -> None:
        from dmbot.consent_dm import keep_invite

        for message in (self.message(None), SimpleNamespace(components=[]), None):
            it = cast(Any, SimpleNamespace(message=message, client=SimpleNamespace()))
            self.assertEqual(link_buttons(keep_invite(it, menu_view(GUILD), GUILD)), [])
        it = cast(Any, SimpleNamespace(message=self.message(LINK), client=SimpleNamespace()))
        once = keep_invite(it, menu_view(GUILD, invite=LINK), GUILD)
        self.assertEqual(len(link_buttons(once)), 1)

    def test_the_link_is_the_one_on_the_message_not_a_foreign_button(self) -> None:
        from dmbot.consent_dm import keep_invite

        other = cast(
            Any,
            SimpleNamespace(
                client=SimpleNamespace(),
                message=SimpleNamespace(
                    components=[
                        SimpleNamespace(
                            children=[SimpleNamespace(label="Docs", url="https://example.com/")]
                        )
                    ]
                ),
            ),
        )
        self.assertEqual(link_buttons(keep_invite(other, menu_view(GUILD), GUILD)), [])

    def test_while_a_session_runs_the_campaigns_current_link_decides(self) -> None:
        from dmbot.consent_dm import keep_invite

        old = self.message(LINK)
        # The DM removed the link: an old message must not bring it back.
        removed = SimpleNamespace(campaign_id=CAMPAIGN, dndbeyond_invite=None)
        client = SimpleNamespace(tables={GUILD: removed})
        it = cast(Any, SimpleNamespace(message=old, client=client))
        self.assertEqual(link_buttons(keep_invite(it, menu_view(GUILD), GUILD)), [])
        # The DM changed it: the new one is used.
        new = "https://www.dndbeyond.com/campaigns/join/New4567"
        client.tables[GUILD] = SimpleNamespace(campaign_id=CAMPAIGN, dndbeyond_invite=new)
        [button] = link_buttons(keep_invite(it, menu_view(GUILD), GUILD))
        self.assertEqual(button.url, new)


class Resumed(unittest.TestCase):
    def test_a_resumed_session_table_carries_the_link(self) -> None:
        import inspect

        from dmbot.bot import DMBot

        source = inspect.getsource(DMBot)
        # Both places a table is built set it (a session that starts, and one that resumes).
        self.assertEqual(source.count("dndbeyond_invite=campaign.dndbeyond_invite"), 2)
