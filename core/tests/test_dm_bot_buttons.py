"""The DM Bot channel's panel (#1666, slice 2): who is what, the private card each role gets, the
check on every press, the panel kept up (edited, re-posted when deleted) and never acting on
another server's campaign. The real campaign and memory stores, against the database."""

from __future__ import annotations

import unittest.mock
from functools import partial
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import CampaignStore
from dmbot.dm_screen import dm_bot_buttons as buttons
from dmbot.dm_screen import dm_bot_panel as panel
from dmbot.dm_screen.dm_bot_panel import Role
from dmbot.memory.models import DM as MEMORY_DM
from dmbot.memory.models import PLAYER_CHARACTER, Heard
from dmbot.memory.store import MemoryStore
from tests.pg import DatabaseTest

GUILD, OTHER_GUILD = 111, 222
DM, PLAYER, MEMBER, GUEST, MANAGER = 7, 8, 9, 10, 11
CHANNEL, MESSAGE = 680000000000000081, 680000000000000082


class Fake:
    """The bot, as far as the panel reads it."""

    def __init__(self, campaigns: CampaignStore, memory: MemoryStore) -> None:
        self.campaigns = campaigns
        self.memory: Any = memory
        self.sheets: object | None = object()
        self.all_house_rules: Any = AsyncMock(return_value=[])
        self.homebrew_reader: Any = None
        self.settings: Any = SimpleNamespace(site_url="")
        self.consenting = {PLAYER, MEMBER}
        self.posted: list[tuple[int, str, Any]] = []
        self.channel: Any = None
        self.pins: list[Any] = []  # the messages posted, to see which were pinned
        self.pin_error: Exception | None = None

    async def recorded(self, guild_id: int, user_id: int) -> bool:
        return user_id in self.consenting

    def get_channel(self, channel_id: int) -> Any:
        return self.channel

    async def post_message(self, channel_id: int, text: str, view: Any = None) -> Any:
        self.posted.append((channel_id, text, view))
        message = SimpleNamespace(
            id=MESSAGE + len(self.posted), channel=SimpleNamespace(id=channel_id), pinned=False
        )
        message.pin = AsyncMock(side_effect=self.pin_error)
        self.pins.append(message)
        return message


def _note(order: list[str], what: str, **_: Any) -> None:
    order.append(what)


class PanelTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.memory = MemoryStore(self.db)
        self.campaign = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.bot = Fake(self.campaigns, self.memory)

    async def make_player(self) -> None:
        await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name="Kesh", source=MEMORY_DM,
            status="confirmed", role=PLAYER_CHARACTER, played_by=PLAYER,
        )  # fmt: skip

    def press(self, user: int, guild: int = GUILD) -> Any:
        sent: dict[str, Any] = {}

        async def send_message(text: str = "", **kw: Any) -> None:
            sent.update(text=text, **kw)

        member = MagicMock(spec=discord.Member)
        member.id = user
        member.guild_permissions = SimpleNamespace(manage_guild=user == MANAGER)
        it = MagicMock()
        it.client = self.bot
        it.guild = SimpleNamespace(id=guild)
        it.user = member
        it.response.send_message = send_message
        it.response.defer = AsyncMock()  # the answer-first buttons defer, then send a follow-up
        it.followup.send = send_message
        it.sent = sent
        return it

    def labels(self, it: Any) -> list[str]:
        return [str(getattr(getattr(c, "item", c), "label", "")) for c in it.sent["view"].children]


class Standings(PanelTest):
    async def test_each_kind_of_person_is_read_as_the_right_role(self) -> None:
        await self.make_player()
        got = {
            user: panel.role_of(await buttons.standing_of(self.bot, self.campaign, user))
            for user in (DM, PLAYER, MEMBER, GUEST, MANAGER)
        }
        self.assertEqual(
            got,
            {
                DM: Role.GAME_MASTER,
                PLAYER: Role.PLAYER,
                MEMBER: Role.MEMBER,  # consented, but not tied to this campaign
                GUEST: Role.GUEST,
                MANAGER: Role.GUEST,  # a manager who is not a DM gets nothing new
            },
        )

    async def entity(self) -> str:
        return (
            await self.memory.add_entity(
                GUILD, self.campaign.id, type="place", name="Ten-Towns", source=MEMORY_DM,
                status="confirmed",
            )
        ).value.id  # fmt: skip

    async def test_someone_recorded_in_a_session_here_is_a_player(self) -> None:
        speaker = 4242
        await self.memory.add_session_heard(
            GUILD, self.campaign.id, 1_700_000_000, [Heard(await self.entity(), speaker, 2)]
        )
        who = await buttons.standing_of(self.bot, self.campaign, speaker)
        self.assertTrue(who.in_campaign)

    async def test_being_in_another_campaign_is_not_being_in_this_one(self) -> None:
        other = await self.campaigns.create(GUILD, "Strahd", DM)
        await self.memory.add_entity(
            GUILD, other.id, type="character", name="Ireena", source=MEMORY_DM,
            status="confirmed", role=PLAYER_CHARACTER, played_by=PLAYER,
        )  # fmt: skip
        who = await buttons.standing_of(self.bot, self.campaign, PLAYER)
        self.assertFalse(who.in_campaign)

    async def test_a_failed_read_can_only_hide_buttons(self) -> None:
        self.bot.memory = SimpleNamespace(is_player_here=AsyncMock(side_effect=RuntimeError("x")))
        self.bot.recorded = AsyncMock(side_effect=RuntimeError("x"))  # type: ignore[method-assign]
        who = await buttons.standing_of(self.bot, self.campaign, PLAYER)
        self.assertEqual(
            (who.in_campaign, who.consented, who.is_game_master), (False, False, False)
        )


class Cards(PanelTest):
    async def test_the_card_holds_only_the_buttons_of_that_role(self) -> None:
        await self.make_player()
        expected = {
            DM: ["Manage DMbot", "Manage campaign", "My data", "❓ Help"],
            PLAYER: ["My data", "❓ Help"],
            MEMBER: ["My data", "❓ Help"],
            GUEST: ["❓ Help"],
            MANAGER: ["❓ Help"],
        }
        for user, want in expected.items():
            it = self.press(user)
            await buttons.OpenButton(self.campaign.id).callback(it)
            self.assertTrue(it.sent["ephemeral"], user)  # every answer is private
            self.assertEqual(self.labels(it), want, user)
            self.assertLessEqual(len(want), 4)  # never more than four (#2986)

    async def test_manage_campaign_is_a_link_to_this_campaigns_website_page(self) -> None:
        it = self.press(DM)
        await buttons.OpenButton(self.campaign.id).callback(it)
        (link,) = [
            c for c in it.sent["view"].children if getattr(c, "label", "") == "Manage campaign"
        ]
        self.assertEqual(link.style, discord.ButtonStyle.link)
        self.assertEqual(
            link.url, f"https://getdmbot.com/account/campaigns/{GUILD}.{self.campaign.id}"
        )

    async def test_a_dm_of_several_campaigns_here_gets_their_campaign_list(self) -> None:
        await self.campaigns.create(GUILD, "Strahd", DM)
        it = self.press(DM)
        await buttons.OpenButton(self.campaign.id).callback(it)
        (link,) = [
            c for c in it.sent["view"].children if getattr(c, "label", "") == "Manage campaign"
        ]
        self.assertEqual(link.url, "https://getdmbot.com/account/campaigns")

    async def test_the_campaign_link_uses_the_configured_site(self) -> None:
        self.assertEqual(
            panel.campaign_url("https://site.example.org/", 5, "abc"),
            "https://site.example.org/account/campaigns/5.abc",
        )
        self.assertEqual(
            panel.campaign_url("http://plain.example.org", 5, "abc"),
            "https://getdmbot.com/account/campaigns/5.abc",
        )

    async def test_manage_dmbot_is_the_old_settings_button_with_the_new_name(self) -> None:
        it = self.press(DM)
        await buttons.OpenButton(self.campaign.id).callback(it)
        (button,) = [
            c
            for c in it.sent["view"].children
            if getattr(getattr(c, "item", c), "label", "") == "Manage DMbot"
        ]
        self.assertEqual(button.item.custom_id, f"dmbot:settings:{self.campaign.id}")

    async def test_help_is_for_anyone_and_private(self) -> None:
        it = self.press(GUEST)
        await buttons.HelpButton(self.campaign.id).callback(it)
        self.assertTrue(it.sent["ephemeral"])
        self.assertTrue(it.sent["text"])

    async def test_my_buttons_and_help_answer_before_any_slow_read(self) -> None:
        # Discord drops a button not answered in 3 seconds (#1964): both defer first, privately,
        # and send what they have as the follow-up. The campaign read is the slow part.
        for button in (buttons.OpenButton, buttons.HelpButton):
            it = self.press(GUEST)
            order: list[str] = []
            real = self.bot.campaigns.get

            async def slow_get(*a: Any, _real: Any = real, _order: list[str] = order) -> Any:
                _order.append("read")
                return await _real(*a)

            it.response.defer = AsyncMock(side_effect=partial(_note, order, "defer"))
            with unittest.mock.patch.object(self.bot.campaigns, "get", slow_get):
                await button(self.campaign.id).callback(it)
            self.assertEqual(order[:2], ["defer", "read"], button.__name__)
            it.response.defer.assert_awaited_once_with(ephemeral=True)
            self.assertTrue(it.sent["ephemeral"])

    async def test_a_gone_campaign_is_said_in_the_follow_up(self) -> None:
        it = self.press(GUEST)
        await buttons.HelpButton("0" * 32).callback(it)
        self.assertEqual(it.sent["text"], buttons.GONE)

    async def test_the_help_card_has_the_rule_button_and_the_transcripts_link(self) -> None:
        it = self.press(GUEST)
        await buttons.HelpButton(self.campaign.id).callback(it)
        self.assertEqual(self.labels(it), ["📖 Look up a rule", "📜 Transcripts"])
        await self.the_transcripts_link_goes_to_the_websites_page()

    async def the_transcripts_link_goes_to_the_websites_page(self) -> None:
        it = self.press(GUEST)
        await buttons.HelpButton(self.campaign.id).callback(it)
        (link,) = [
            c for c in it.sent["view"].children if getattr(c, "label", "") == "📜 Transcripts"
        ]
        self.assertEqual(link.url, panel.TRANSCRIPTS_URL)
        self.assertEqual(link.style, discord.ButtonStyle.link)
        self.assertEqual(
            panel.transcripts_url("https://site.example.org/"),
            "https://site.example.org/account/transcripts",
        )
        self.assertEqual(panel.transcripts_url("http://plain.example.org"), panel.TRANSCRIPTS_URL)

    async def test_a_client_without_settings_still_gets_the_card_with_the_public_link(self) -> None:
        self.bot.settings = None  # (no settings at all)
        del self.bot.settings
        it = self.press(GUEST)
        await buttons.HelpButton(self.campaign.id).callback(it)
        (link,) = [
            c for c in it.sent["view"].children if getattr(c, "label", "") == "📜 Transcripts"
        ]
        self.assertEqual(link.url, panel.TRANSCRIPTS_URL)

    async def test_the_rule_button_opens_the_lookup_form_for_a_guest_and_a_dm(self) -> None:
        for user in (GUEST, DM):
            it = self.press(user)
            it.response.send_modal = AsyncMock()
            await buttons.RuleButton(self.campaign.id).callback(it)
            (form,) = it.response.send_modal.await_args.args
            self.assertEqual(form.campaign.id, self.campaign.id, user)

    async def test_the_rule_button_with_an_unreadable_campaign_says_gone(self) -> None:
        it = self.press(GUEST)
        it.response.send_modal = AsyncMock()
        self.bot.campaigns = cast(
            Any, SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("down")))
        )
        await buttons.RuleButton(self.campaign.id).callback(it)
        it.response.send_modal.assert_not_awaited()
        self.assertEqual(it.sent["text"], buttons.GONE)

    async def test_a_lookup_form_submitted_from_another_server_is_refused(self) -> None:
        from dmbot.ui import rule_lookup

        told: list[str] = []

        async def tell(interaction: Any, text: str) -> None:
            told.append(text)

        it = self.press(GUEST, guild=OTHER_GUILD)
        it.guild_id = OTHER_GUILD
        with (
            unittest.mock.patch.object(rule_lookup, "_tell", tell),
            unittest.mock.patch.object(rule_lookup, "_answer_first", AsyncMock()),
        ):
            await rule_lookup.look_up(it, self.campaign.id, "Fireball", guild_id=GUILD, paced=False)
        self.assertEqual(told, [rule_lookup.NOT_HERE])

    async def test_the_rule_button_from_another_server_is_refused(self) -> None:
        it = self.press(DM, guild=OTHER_GUILD)
        it.response.send_modal = AsyncMock()
        await buttons.RuleButton(self.campaign.id).callback(it)
        it.response.send_modal.assert_not_awaited()
        self.assertEqual(it.sent["text"], buttons.GONE)

    async def test_a_players_answer_ends_with_the_dm_decides_line(self) -> None:
        from dmbot.ui import rule_lookup

        sent: list[str] = []

        async def capture(interaction: Any, text: str, view: Any = None) -> None:
            sent.append(text)

        it = self.press(GUEST)
        it.guild_id = GUILD
        it.client = self.bot
        with (
            unittest.mock.patch.object(rule_lookup, "_send", capture),
            unittest.mock.patch.object(rule_lookup, "_answer_first", AsyncMock()),
        ):
            await rule_lookup.look_up(it, self.campaign.id, "Fireball", guild_id=GUILD, paced=False)
        self.assertTrue(sent)
        self.assertIn("Your DM decides what applies at your table.", sent[-1])

    async def test_a_player_pressing_the_settings_button_is_refused(self) -> None:
        from dmbot.dm_screen.settings import SettingsButton

        it = self.press(PLAYER)
        await SettingsButton(self.campaign.id).callback(it)
        self.assertNotIn("view", it.sent)  # no card
        self.assertTrue(it.sent["ephemeral"])

    async def test_a_panel_never_acts_on_another_servers_campaign(self) -> None:
        it = self.press(DM, guild=OTHER_GUILD)
        await buttons.OpenButton(self.campaign.id).callback(it)
        self.assertEqual(it.sent["text"], buttons.GONE)
        self.assertNotIn("view", it.sent)
        again = self.press(DM, guild=OTHER_GUILD)
        await buttons.HelpButton(self.campaign.id).callback(again)
        self.assertEqual(again.sent["text"], buttons.GONE)

    async def test_the_shared_message_has_only_the_open_and_help_buttons(self) -> None:
        view = buttons.panel_view(self.campaign.id)
        self.assertEqual(
            [getattr(getattr(c, "item", c), "label", "") for c in view.children],
            ["👤 DMbot Main Menu", "❓ Help"],
        )


class Keeping(PanelTest):
    async def with_channel(self) -> Any:
        await self.campaigns.set_dm_bot_channel(GUILD, self.campaign.id, CHANNEL)
        campaign = await self.campaigns.get(GUILD, self.campaign.id)
        assert campaign is not None
        return campaign

    async def reload(self) -> Any:
        campaign = await self.campaigns.get(GUILD, self.campaign.id)
        assert campaign is not None
        return campaign

    def a_channel(self) -> Any:
        channel = MagicMock(spec=discord.TextChannel)
        channel.fetch_message = AsyncMock()
        self.bot.channel = channel
        return channel

    async def test_no_panel_yet_posts_one_and_remembers_it(self) -> None:
        campaign = await self.with_channel()
        self.a_channel()
        message = await buttons.ensure_panel(self.bot, campaign)
        assert message is not None
        self.assertEqual(len(self.bot.posted), 1)
        self.assertEqual(self.bot.posted[0][:2], (CHANNEL, panel.PANEL_TEXT))
        saved = await self.campaigns.get(GUILD, self.campaign.id)
        assert saved is not None and saved.dm_bot_message_id == message.id

    async def test_an_existing_panel_is_edited_in_place_not_posted_again(self) -> None:
        campaign = await self.with_channel()
        channel = self.a_channel()
        await self.campaigns.set_dm_bot_message(GUILD, campaign.id, MESSAGE)
        campaign = await self.reload()
        existing = MagicMock()
        existing.edit = AsyncMock()
        existing.pin = AsyncMock()
        existing.pinned = False
        channel.fetch_message.return_value = existing
        await buttons.ensure_panel(self.bot, campaign)
        existing.edit.assert_awaited_once()
        existing.pin.assert_awaited_once()  # one posted before pinning, or unpinned by hand
        self.assertEqual(self.bot.posted, [])

    async def test_a_new_panel_is_pinned_and_one_already_pinned_is_left_alone(self) -> None:
        campaign = await self.with_channel()
        channel = self.a_channel()
        message = await buttons.ensure_panel(self.bot, campaign)
        assert message is not None
        self.bot.pins[0].pin.assert_awaited_once()
        await self.campaigns.set_dm_bot_message(GUILD, campaign.id, MESSAGE)
        campaign = await self.reload()
        pinned = MagicMock()
        pinned.edit = AsyncMock()
        pinned.pin = AsyncMock()
        pinned.pinned = True
        channel.fetch_message.return_value = pinned
        await buttons.ensure_panel(self.bot, campaign)
        pinned.pin.assert_not_awaited()

    async def test_a_refused_or_failed_pin_never_stops_the_panel(self) -> None:
        campaign = await self.with_channel()
        self.a_channel()
        refused = discord.Forbidden(MagicMock(status=403), "no")
        failed = discord.HTTPException(MagicMock(status=400), "pin limit")
        for error in (refused, failed):
            self.bot.pins.clear()
            await self.campaigns.set_dm_bot_message(GUILD, campaign.id, MESSAGE)
            self.bot.channel.fetch_message.side_effect = discord.NotFound(
                MagicMock(status=404), "x"
            )
            self.bot.pin_error = error
            message = await buttons.ensure_panel(self.bot, await self.reload())
            self.assertIsNotNone(message)

    async def test_a_deleted_panel_is_posted_again(self) -> None:
        campaign = await self.with_channel()
        channel = self.a_channel()
        await self.campaigns.set_dm_bot_message(GUILD, campaign.id, MESSAGE)
        campaign = await self.reload()
        channel.fetch_message.side_effect = discord.NotFound(MagicMock(status=404), "gone")
        message = await buttons.ensure_panel(self.bot, campaign)
        assert message is not None
        self.assertEqual(len(self.bot.posted), 1)
        saved = await self.campaigns.get(GUILD, campaign.id)
        assert saved is not None and saved.dm_bot_message_id == message.id != MESSAGE

    async def test_a_hiccup_does_not_pile_a_second_panel_beside_the_first(self) -> None:
        campaign = await self.with_channel()
        channel = self.a_channel()
        await self.campaigns.set_dm_bot_message(GUILD, campaign.id, MESSAGE)
        campaign = await self.reload()
        channel.fetch_message.side_effect = discord.HTTPException(MagicMock(status=500), "oops")
        self.assertIsNone(await buttons.ensure_panel(self.bot, campaign))
        self.assertEqual(self.bot.posted, [])

    async def test_no_channel_means_nothing_is_posted(self) -> None:
        self.assertIsNone(await buttons.ensure_panel(self.bot, self.campaign))
        self.assertEqual(self.bot.posted, [])

    async def test_two_starts_at_once_post_one_panel(self) -> None:
        import asyncio

        await self.with_channel()
        self.a_channel()
        campaign = await self.reload()
        await asyncio.gather(
            buttons.ensure_panel(self.bot, campaign), buttons.ensure_panel(self.bot, campaign)
        )
        self.assertEqual(len(self.bot.posted), 1)  # the second saw the first's message id

    async def test_a_post_that_fails_saves_nothing(self) -> None:
        campaign = await self.with_channel()
        self.a_channel()
        self.bot.post_message = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.assertIsNone(await buttons.ensure_panel(self.bot, campaign))
        saved = await self.reload()
        self.assertIsNone(saved.dm_bot_message_id)

    async def test_a_panel_whose_id_cannot_be_saved_is_taken_down_again(self) -> None:
        campaign = await self.with_channel()
        self.a_channel()
        posted = MagicMock()
        posted.id = MESSAGE + 9
        posted.delete = AsyncMock()
        self.bot.post_message = AsyncMock(return_value=posted)  # type: ignore[method-assign]
        self.bot.campaigns = SimpleNamespace(
            get=self.campaigns.get, set_dm_bot_message=AsyncMock(side_effect=RuntimeError("x"))
        )  # type: ignore[assignment]
        self.assertIsNone(await buttons.ensure_panel(self.bot, campaign))
        posted.delete.assert_awaited_once()  # not left as an orphan with live buttons


class RoundTrip(PanelTest):
    async def test_the_buttons_come_back_from_their_ids_after_a_restart(self) -> None:
        for cls, action in (
            (buttons.OpenButton, "open"),
            (buttons.HelpButton, "help"),
            (buttons.RuleButton, "rule"),
        ):
            custom_id = f"dmbot:panel:{action}:{self.campaign.id}"
            match = cls.__discord_ui_compiled_template__.fullmatch(custom_id)
            assert match is not None
            made = await cls.from_custom_id(MagicMock(), MagicMock(), match)
            self.assertEqual(made.campaign_id, self.campaign.id)

    async def test_a_proposed_character_does_not_tie_someone_to_the_campaign(self) -> None:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, type="character", name="Kesh", source="extractor",
            status="proposed", role=PLAYER_CHARACTER, played_by=PLAYER,
        )  # fmt: skip
        self.assertEqual(made.value.status, "proposed")
        self.assertFalse(await self.memory.is_player_here(GUILD, self.campaign.id, PLAYER))
