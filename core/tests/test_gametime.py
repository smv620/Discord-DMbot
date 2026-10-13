"""The players' game-time channel (#1116): the 14 approximate words at every boundary, the
channel made at start (and repaired, and its failure not blocking), the players' pinned clock
edited in place with no exact time, timer or button, the one line for a finished rest or a new
day, the rests moved off the pinned DM clock into the private Set time… panel, and the
migration. The store and channel tests need Postgres."""

from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.bot import DMBot
from dmbot.campaigns import Campaign, CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.db import Database, drop_schema
from dmbot.dm_screen import clock as ui
from dmbot.dm_screen import messages
from dmbot.dm_screen.gametime_channel import (
    GametimeChannelError,
    gametime_channel_name,
    is_gametime_name,
    setup_gametime_channel,
)
from dmbot.schema import MIGRATIONS
from dmbot.sessions import SessionStore
from dmbot.timebot import clock as game
from dmbot.timebot.clock import Clock
from dmbot.timebot.store import ClockSection, ClockStore
from tests.pg import TEST_URL, DatabaseTest
from tests.test_dm_screen_setup import (
    BOT,
    BOT_PERMS,
    FORBIDDEN,
    GENERAL,
    text_channel,
)

GUILD, OTHER_GUILD = 111, 222
DM, PLAYER = 7, 8
NEW, PLAYERS_CHANNEL, SCREEN = 60, 700, 555
NOON4 = game.from_day_and_time(4, 12)


class DayPart(unittest.TestCase):
    """PLAN's table, at every boundary minute."""

    TABLE = (
        ((3, 30), (4, 59), "pre-dawn"),
        ((5, 0), (5, 59), "dawn"),
        ((6, 0), (9, 59), "morning"),
        ((10, 0), (11, 29), "late morning"),
        ((11, 30), (12, 59), "about noon"),
        ((13, 0), (14, 59), "afternoon"),
        ((15, 0), (16, 59), "late afternoon"),
        ((17, 0), (17, 59), "early evening"),
        ((18, 0), (18, 59), "dusk"),
        ((19, 0), (20, 29), "evening"),
        ((20, 30), (21, 59), "late evening"),
        ((22, 0), (23, 14), "night"),
        ((23, 15), (23, 59), "near midnight"),
        ((0, 0), (0, 44), "near midnight"),  # it spans midnight
        ((0, 45), (3, 29), "late night"),
    )

    def at(self, hour: int, minute: int, day: int = 5) -> str:
        return game.day_part(game.from_day_and_time(day, hour, minute))

    def test_both_ends_of_every_range(self) -> None:
        for (h1, m1), (h2, m2), word in self.TABLE:
            self.assertEqual(self.at(h1, m1), word, f"{h1}:{m1:02d}")
            self.assertEqual(self.at(h2, m2), word, f"{h2}:{m2:02d}")

    def test_every_minute_of_the_day_has_one_of_exactly_fourteen_words(self) -> None:
        words = {game.day_part(m) for m in range(game.MINUTES_PER_DAY)}
        self.assertEqual(
            words,
            {
                "pre-dawn", "dawn", "morning", "late morning", "about noon", "afternoon",
                "late afternoon", "early evening", "dusk", "evening", "late evening", "night",
                "near midnight", "late night",
            },
        )  # fmt: skip
        self.assertEqual(len(words), 14)

    def test_the_word_does_not_depend_on_the_day(self) -> None:
        for day in (1, 2, 99):
            self.assertEqual(self.at(23, 30, day), "near midnight")
            self.assertEqual(self.at(0, 10, day), "near midnight")

    def test_the_dms_label_has_the_exact_time_and_the_players_has_not(self) -> None:
        minute = game.from_day_and_time(5, 6, 20)
        self.assertEqual(game.label(minute), "Day 5, morning (6:20)")
        self.assertEqual(game.short_label(minute), "Day 5, morning")
        self.assertEqual(game.label(game.from_day_and_time(5, 14, 5)), "Day 5, afternoon (14:05)")
        self.assertEqual(game.label(game.from_day_and_time(5, 0, 0)), "Day 5, near midnight (0:00)")

    def test_the_players_text_never_has_an_exact_time(self) -> None:
        for minute in range(0, game.MINUTES_PER_DAY, 7):
            text = ui.players_text(Clock(minute, 0))
            self.assertIsNone(re.search(r"\d:\d\d", text), text)
            self.assertNotIn("(", text)
            self.assertNotIn("imer", text)
        self.assertEqual(
            ui.players_text(Clock(game.from_day_and_time(5, 6, 20), 0)), "🕰️ **Day 5, morning**"
        )


class PlayersLines(unittest.TestCase):
    def test_a_finished_rest_and_a_new_day_each_get_one_line(self) -> None:
        noon = Clock(NOON4, NOON4)
        long_done = Clock(NOON4 + 480, NOON4 + 480)
        self.assertEqual(
            ui.players_line("long", noon, long_done), "Long rest done. Day 4, evening."
        )
        short = Clock(NOON4 + 60, NOON4)
        self.assertEqual(
            ui.players_line("short", noon, short), "Short rest done. Day 4, afternoon."
        )
        late = Clock(game.from_day_and_time(4, 23, 30), 0)
        past_midnight = Clock(game.from_day_and_time(5, 0, 30), 0)
        self.assertEqual(
            ui.players_line("h1", late, past_midnight), "A new day begins. Day 5, near midnight."
        )
        dawn = Clock(game.from_day_and_time(5, 6), 0)
        self.assertEqual(
            ui.players_line("dawn", Clock(NOON4, 0), dawn), "A new day begins. Day 5, morning."
        )

    def test_a_rest_that_crosses_midnight_is_one_line_not_two(self) -> None:
        before = Clock(game.from_day_and_time(4, 20), 0)
        after = game.long_rest(before)
        self.assertEqual(ui.players_line("long", before, after), "Long rest done. Day 5, pre-dawn.")

    def test_nothing_else_says_anything(self) -> None:
        noon = Clock(NOON4, NOON4)
        self.assertIsNone(ui.players_line("m10", noon, Clock(NOON4 + 10, NOON4)))
        self.assertIsNone(ui.players_line("h1", noon, Clock(NOON4 + 60, NOON4)))
        # Setting the time is a correction, even across a day, and going back says nothing.
        self.assertIsNone(ui.players_line("set", noon, Clock(NOON4 + 2000, NOON4)))
        self.assertIsNone(ui.players_line("short", noon, Clock(NOON4 - 5, NOON4 - 5)))
        self.assertIsNone(ui.players_line("short", None, noon))

    def test_the_words_never_have_an_exact_time(self) -> None:
        line = ui.players_line("long", Clock(NOON4, 0), Clock(NOON4 + 480, 0))
        assert line is not None
        self.assertIsNone(re.search(r"\d:\d\d", line))


class ChannelSetup(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = CampaignStore(self.db)
        self.campaign = await self.store.create(GUILD, "Rime of the Frostmaiden", DM)
        me = MagicMock(spec=discord.Member)
        me.id = BOT
        me.guild_permissions = BOT_PERMS
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        guild.me = me
        guild.get_role = lambda _id: None
        guild.get_member = lambda _id: None
        self.general = text_channel(GENERAL, "general", guild)
        self.new = text_channel(NEW, "dmb-gametime-rmfthfrstmdn", guild)
        guild.fetch_channel = AsyncMock(return_value=self.general)
        guild.create_text_channel = AsyncMock(return_value=self.new)
        guild.get_channel = lambda cid: self.new if cid == NEW else None
        self.guild = guild

    async def test_a_new_channel_is_named_saved_and_view_only_for_everyone(self) -> None:
        channel = await setup_gametime_channel(self.guild, self.campaign.id, self.store)
        assert channel is self.new
        self.assertEqual(
            self.guild.create_text_channel.call_args.args[0], "dmb-gametime-rmfthfrstmdn"
        )
        created: dict[Any, discord.PermissionOverwrite] = (
            self.guild.create_text_channel.call_args.kwargs["overwrites"]
        )
        everyone = next(ow for key, ow in created.items() if key.id == GUILD)
        self.assertTrue(everyone.view_channel and everyone.read_message_history)
        self.assertFalse(everyone.send_messages)
        self.assertFalse(everyone.add_reactions)
        self.assertTrue(next(ow for key, ow in created.items() if key.id == BOT).send_messages)
        self.assertIn("only DMbot posts", self.guild.create_text_channel.call_args.kwargs["topic"])
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None and saved.gametime_channel_id == NEW
        self.assertIsNone(saved.transcript_channel_id)  # the transcript's own id is separate

    async def test_the_name_is_the_gametime_one(self) -> None:
        self.assertEqual(gametime_channel_name(self.campaign), "dmb-gametime-rmfthfrstmdn")
        self.assertTrue(is_gametime_name("dmb-gametime-x"))
        self.assertFalse(is_gametime_name("dmb-transcript-x"))

    async def test_an_existing_channel_is_made_view_only_again_not_recreated(self) -> None:
        await self.store.set_gametime_channel(GUILD, self.campaign.id, NEW)
        self.guild.fetch_channel = AsyncMock(return_value=self.new)
        await setup_gametime_channel(self.guild, self.campaign.id, self.store)
        self.guild.create_text_channel.assert_not_called()
        self.new.edit.assert_awaited()
        sent = self.new.edit.await_args_list[0].kwargs["overwrites"]
        everyone = next(ow for key, ow in sent.items() if key.id == GUILD)
        self.assertFalse(everyone.send_messages)

    async def test_an_ordinary_or_transcript_channel_is_never_taken_over(self) -> None:
        for wrong in (GENERAL, 99):
            other = text_channel(
                wrong, "dmb-transcript-x" if wrong == 99 else "general", self.guild
            )
            self.guild.fetch_channel = AsyncMock(return_value=other)
            await self.store.set_gametime_channel(GUILD, self.campaign.id, wrong)
            self.guild.create_text_channel.reset_mock()
            await setup_gametime_channel(self.guild, self.campaign.id, self.store)
            other.edit.assert_not_called()
            self.guild.create_text_channel.assert_awaited_once()

    async def test_a_refusal_is_a_plain_cause_for_the_dm(self) -> None:
        self.guild.create_text_channel = AsyncMock(side_effect=FORBIDDEN)
        with self.assertRaises(GametimeChannelError) as caught:
            await setup_gametime_channel(self.guild, self.campaign.id, self.store)
        self.assertEqual(str(caught.exception), messages.TRANSCRIPT_FORBIDDEN)
        saved = await self.store.get(GUILD, self.campaign.id)
        assert saved is not None and saved.gametime_channel_id is None

    async def test_a_channel_that_cant_be_saved_is_deleted_again(self) -> None:
        self.store.set_gametime_channel = AsyncMock(side_effect=RuntimeError("db"))  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            await setup_gametime_channel(self.guild, self.campaign.id, self.store)
        self.new.delete.assert_awaited()

    async def test_its_id_is_not_in_a_backup(self) -> None:
        await self.store.set_gametime_channel(GUILD, self.campaign.id, NEW)
        data = await self.store.export(GUILD, self.campaign.id)
        self.assertNotIn(str(NEW), str(data))
        restored = await self.store.import_backup(OTHER_GUILD, data, DM)
        self.assertIsNone(restored.gametime_channel_id)


class StartNeverBlocks(DatabaseTest):
    """`/dmbot start` makes the channel; a failure is a note for the DM, never an error."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(ClockSection())
        self.a = await self.campaigns.create(GUILD, "Alpha", DM)
        self.clocks = ClockStore(self.db)
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            ConsentStore(self.db),
            self.campaigns,
            SessionStore(self.db),
            clocks=self.clocks,
        )

    async def test_a_refusal_or_a_crash_is_a_note_not_an_exception(self) -> None:
        import dmbot.bot as bot_module

        guild = MagicMock(spec=discord.Guild)
        for error in (GametimeChannelError("Discord won't let DMbot"), RuntimeError("boom")):
            original = bot_module.setup_gametime_channel
            bot_module.setup_gametime_channel = AsyncMock(side_effect=error)  # type: ignore[assignment]
            try:
                note = await self.bot._gametime_channel(guild, self.a, object())
            finally:
                bot_module.setup_gametime_channel = original  # type: ignore[assignment]
            assert note is not None
            self.assertIn("game-time channel", note)
            self.assertIn("session goes on", note)


class Msg:
    def __init__(self, message_id: int, channel_id: int, text: str) -> None:
        self.id = message_id
        self.content = text
        self.channel = SimpleNamespace(id=channel_id)
        self.edits: list[dict[str, Any]] = []
        self.pinned = False
        self.jump_url = f"https://discord.test/{channel_id}/{message_id}"

    async def edit(self, **kw: Any) -> None:
        self.edits.append(kw)
        self.content = kw.get("content", self.content)

    async def pin(self, **_: Any) -> None:
        self.pinned = True


class FakeDiscord:
    """Channels with messages, and the bot's `post_message`."""

    def __init__(self, clocks: ClockStore, campaigns: CampaignStore) -> None:
        self.clocks = clocks
        self.campaigns = SimpleNamespace(
            get=campaigns.get, optional_rule_overrides=AsyncMock(return_value={})
        )
        self.messages: dict[int, Msg] = {}
        self.posts: list[tuple[int, str, Any]] = []
        self.next_id = 9000
        self.channels: dict[int, Any] = {}
        self.fetch_error: Exception | None = None
        self.effects = None

    def channel(self, channel_id: int) -> Any:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id

        async def fetch_message(message_id: int) -> Msg:
            if self.fetch_error is not None:
                raise self.fetch_error
            if message_id not in self.messages:
                raise discord.NotFound(MagicMock(status=404), "gone")
            return self.messages[message_id]

        channel.fetch_message = fetch_message
        self.channels[channel_id] = channel
        return channel

    def get_channel(self, channel_id: int) -> Any:
        return self.channels.get(channel_id)

    async def post_message(self, channel_id: int, text: str, view: Any = None) -> Msg | None:
        self.posts.append((channel_id, text, view))
        self.next_id += 1
        message = Msg(self.next_id, channel_id, text)
        self.messages[message.id] = message
        return message


class PlayersClock(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(ClockSection())
        self.clocks = ClockStore(self.db)
        self.a = await self.campaigns.create(GUILD, "Alpha", DM)
        await self.campaigns.set_dm_screen(GUILD, self.a.id, SCREEN)
        await self.campaigns.set_gametime_channel(GUILD, self.a.id, PLAYERS_CHANNEL)
        self.discord = FakeDiscord(self.clocks, self.campaigns)
        self.discord.channel(SCREEN)
        self.discord.channel(PLAYERS_CHANNEL)

    async def campaign(self) -> Campaign:
        campaign = await self.campaigns.get(GUILD, self.a.id)
        assert campaign is not None
        return campaign

    async def press(self, action: str, *, set_to: int | None = None) -> ui.Result:
        return await ui.press(self.discord, GUILD, self.a.id, DM, action, set_to=set_to)

    async def players_posts(self) -> list[str]:
        return [text for channel, text, _ in self.discord.posts if channel == PLAYERS_CHANNEL]

    async def test_it_is_posted_pinned_and_remembered_then_edited_in_place(self) -> None:
        await self.press("set", set_to=NOON4)
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        await ui.show_players(self.discord, await self.campaign(), stored)
        [first] = [m for m in self.discord.messages.values() if m.channel.id == PLAYERS_CHANNEL]
        self.assertEqual(first.content, "🕰️ **Day 4, about noon**")
        self.assertTrue(first.pinned)
        remembered = await self.clocks.get(GUILD, self.a.id)
        assert remembered is not None
        self.assertEqual(
            (remembered.players_channel_id, remembered.players_message_id),
            (PLAYERS_CHANNEL, first.id),
        )
        await self.press("h1")
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        await ui.show_players(self.discord, await self.campaign(), stored)
        self.assertEqual(len(await self.players_posts()), 1)  # edited, not posted again
        self.assertEqual(first.content, "🕰️ **Day 4, afternoon**")
        self.assertNotIn("view", first.edits[-1])  # no buttons
        self.assertIsNone(re.search(r"\d:\d\d", first.content))

    async def test_nothing_is_edited_when_the_words_have_not_changed(self) -> None:
        await self.press("set", set_to=NOON4)
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        await ui.show_players(self.discord, await self.campaign(), stored)
        await self.press("m10")  # still "about noon"
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        message = next(iter(self.discord.messages.values()))
        await ui.show_players(self.discord, await self.campaign(), stored)
        self.assertEqual(message.edits, [])

    async def test_a_deleted_message_or_a_new_channel_gets_a_new_clock(self) -> None:
        await self.press("set", set_to=NOON4)
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        await ui.show_players(self.discord, await self.campaign(), stored)
        self.discord.messages.clear()  # someone deleted it
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        await ui.show_players(self.discord, await self.campaign(), stored)
        self.assertEqual(len(await self.players_posts()), 2)
        # The channel itself was made again, under a new id: not the old one's message.
        await self.campaigns.set_gametime_channel(GUILD, self.a.id, 701)
        self.discord.channel(701)
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        await ui.show_players(self.discord, await self.campaign(), stored)
        self.assertEqual(self.discord.posts[-1][0], 701)

    async def test_a_hiccup_does_not_pile_a_second_clock_beside_it(self) -> None:
        await self.press("set", set_to=NOON4)
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        await ui.show_players(self.discord, await self.campaign(), stored)
        self.discord.fetch_error = discord.HTTPException(MagicMock(status=500), "oops")
        await self.press("h1")
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        await ui.show_players(self.discord, await self.campaign(), stored)
        self.assertEqual(len(await self.players_posts()), 1)

    async def test_without_a_channel_nothing_happens_and_nothing_breaks(self) -> None:
        await self.campaigns.set_gametime_channel(GUILD, self.a.id, None)
        await self.press("set", set_to=NOON4)
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        await ui.show_players(self.discord, await self.campaign(), stored)
        self.assertEqual(self.discord.posts, [])
        # And a failure is swallowed: the DM's clock is the one that matters.
        self.discord.post_message = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        await self.campaigns.set_gametime_channel(GUILD, self.a.id, PLAYERS_CHANNEL)
        await ui.show_players(self.discord, await self.campaign(), stored)

    async def test_a_rest_and_a_new_day_are_each_said_once(self) -> None:
        await self.press("set", set_to=NOON4)
        result = await self.press("long")
        await ui.update_players(self.discord, await self.campaign(), "long", result)
        self.assertEqual(
            await self.players_posts(),
            ["🕰️ **Day 4, evening**", "Long rest done. Day 4, evening."],
        )
        late = game.from_day_and_time(4, 23, 30)
        result = await self.press("set", set_to=late)
        await ui.update_players(self.discord, await self.campaign(), "set", result)
        posts = len(await self.players_posts())
        result = await self.press("h1")
        await ui.update_players(self.discord, await self.campaign(), "h1", result)
        self.assertEqual(
            (await self.players_posts())[posts:], ["A new day begins. Day 5, near midnight."]
        )
        result = await self.press("m10")
        await ui.update_players(self.discord, await self.campaign(), "m10", result)
        self.assertEqual(len(await self.players_posts()), posts + 1)  # nothing for ten minutes
        # No tired line, no effect lines, no dawn/noon/dusk lines reach the players.
        for text in await self.players_posts():
            self.assertNotIn("Check:", text)
            self.assertNotIn("Dawn", text)
            self.assertNotIn("⏳", text)


class Buttons(unittest.TestCase):
    CID = "0123456789abcdef0123456789abcdef"

    def labels(self, view: discord.ui.View) -> list[str]:
        return [str(getattr(item, "item", item).label) for item in view.children]

    def test_the_pinned_dm_clock_has_no_rest_buttons(self) -> None:
        view = ui.clock_view(self.CID)
        self.assertEqual(self.labels(view), ["+10 min", "+1 hour", "Set time…", "Start a timer"])
        for banned in ("rest", "dawn"):
            self.assertFalse(any(banned in label.lower() for label in self.labels(view)))

    def test_the_set_time_panel_has_them(self) -> None:
        view = ui.panel_view(self.CID)
        self.assertEqual(
            self.labels(view), ["Short rest", "Long rest", "Skip to dawn", "Type the time…"]
        )
        rows = [getattr(item, "item", item).row for item in view.children]
        self.assertTrue(all(isinstance(r, int) for r in rows))

    def test_every_button_id_fits_and_matches_the_template(self) -> None:
        for action in (*ui.ACTIONS, "open"):
            custom_id = ui.ClockButton(self.CID, action).item.custom_id
            assert custom_id is not None and len(custom_id) <= 100
            self.assertIsNotNone(
                ui.ClockButton.__discord_ui_compiled_template__.fullmatch(custom_id)
            )

    def test_five_buttons_at_most_to_a_row(self) -> None:
        for names in (ui.PINNED, ui.PANEL):
            rows: dict[int, int] = {}
            for action in names:
                row = ui.ACTIONS[action][3]
                rows[row] = rows.get(row, 0) + 1
            self.assertTrue(all(n <= 5 for n in rows.values()))


class SetTimePanel(PlayersClock):
    """Pressing **Set time…** opens the private panel (once a clock exists)."""

    def interaction(self, message: Any = None) -> Any:
        self.discord.campaigns.get = self.campaigns.get  # type: ignore[assignment]
        it = SimpleNamespace(
            client=self.discord,
            guild=SimpleNamespace(id=GUILD),
            user=SimpleNamespace(id=DM),
            message=message,
            response=SimpleNamespace(
                defer=AsyncMock(),
                send_message=AsyncMock(),
                send_modal=AsyncMock(),
                is_done=lambda: False,
            ),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        return it

    async def test_set_time_answers_privately_with_the_rests(self) -> None:
        await self.press("set", set_to=NOON4)
        it = self.interaction()
        await ui.ClockButton(self.a.id, "set").callback(cast(Any, it))
        it.response.send_modal.assert_not_awaited()
        sent = it.response.send_message.await_args
        self.assertTrue(sent.kwargs["ephemeral"])  # only the DM who pressed sees it
        self.assertFalse(sent.args[0].startswith("🕰️"))  # never taken for the clock message
        labels = [getattr(i, "item", i).label for i in sent.kwargs["view"].children]
        self.assertEqual(labels, ["Short rest", "Long rest", "Skip to dawn", "Type the time…"])

    async def test_typing_a_time_and_the_first_time_open_the_form(self) -> None:
        await self.press("set", set_to=NOON4)
        it = self.interaction()
        await ui.ClockButton(self.a.id, "typed").callback(cast(Any, it))
        it.response.send_modal.assert_awaited_once()
        # Before any clock exists, Set time… goes straight to the form.
        other = await self.campaigns.create(GUILD, "Beta", DM)
        it2 = self.interaction()
        await ui.ClockButton(other.id, "set").callback(cast(Any, it2))
        it2.response.send_modal.assert_awaited_once()
        it2.response.send_message.assert_not_awaited()

    async def test_a_rest_from_the_panel_moves_both_clocks_and_says_so_to_the_players(self) -> None:
        await self.press("set", set_to=NOON4)
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        campaign = await self.campaign()
        await ui.show(self.discord, campaign, stored)  # the DM's pinned message
        await ui.show_players(self.discord, campaign, await self.clocks.get(GUILD, self.a.id))  # type: ignore[arg-type]
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        dm_message = self.discord.messages[stored.message_id or 0]
        players_message = self.discord.messages[stored.players_message_id or 0]
        panel = Msg(1, SCREEN, "Move the clock. Only you see this.")  # the ephemeral panel
        it = self.interaction(message=panel)
        await ui.ClockButton(self.a.id, "long").callback(cast(Any, it))
        self.assertEqual(dm_message.content.splitlines()[0], "🕰️ **Day 4, evening (20:00)**")
        self.assertEqual(players_message.content, "🕰️ **Day 4, evening**")
        self.assertEqual((await self.players_posts())[-1], "Long rest done. Day 4, evening.")
        self.assertEqual((await self.players_posts()).count("Long rest done. Day 4, evening."), 1)
        self.assertTrue(it.followup.send.await_args.kwargs["ephemeral"])  # the Undo note

    async def test_a_press_on_the_pinned_message_edits_both(self) -> None:
        await self.press("set", set_to=NOON4)
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        campaign = await self.campaign()
        await ui.show(self.discord, campaign, stored)
        await ui.show_players(self.discord, campaign, await self.clocks.get(GUILD, self.a.id))  # type: ignore[arg-type]
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        dm_message = self.discord.messages[stored.message_id or 0]
        players_message = self.discord.messages[stored.players_message_id or 0]
        it = self.interaction(message=dm_message)
        await ui.ClockButton(self.a.id, "h1").callback(cast(Any, it))
        self.assertIn("(13:00)", dm_message.content)
        self.assertEqual(players_message.content, "🕰️ **Day 4, afternoon**")


class Migration0050(DatabaseTest):
    """The migration adds nullable columns: rows that exist keep their values."""

    async def test_rows_that_exist_are_unchanged_and_the_new_ids_are_empty(self) -> None:
        before = [m for m in MIGRATIONS if m[0] < "0050"]
        await self.db.close()
        await drop_schema(TEST_URL, self.schema)
        self.db = await Database.open(TEST_URL, schema=self.schema, migrations=before)
        campaigns = CampaignStore(self.db, clock=lambda: 1)
        campaigns.register_section(ClockSection())
        made = await campaigns.create(GUILD, "Frozen Wastes", DM)
        # Written by hand: the clock store already reads the columns this migration adds.
        async with self.db.guild(GUILD) as conn:
            await conn.execute(
                "INSERT INTO game_clocks (guild_id, campaign_id, minute, last_long_rest,"
                " channel_id, message_id, updated_at) VALUES (%s, %s, %s, %s, 5, 6, 1)",
                (GUILD, made.id, NOON4, NOON4),
            )
        clocks = ClockStore(self.db)
        await self.db.migrate()
        campaign = await campaigns.get(GUILD, made.id)
        stored = await clocks.get(GUILD, made.id)
        assert campaign is not None and stored is not None
        self.assertEqual(campaign.name, "Frozen Wastes")
        self.assertIsNone(campaign.gametime_channel_id)
        self.assertEqual((stored.clock.minute, stored.channel_id, stored.message_id), (NOON4, 5, 6))
        self.assertEqual((stored.players_channel_id, stored.players_message_id), (None, None))
        await clocks.set_players_message(GUILD, made.id, 7, 8)
        again = await clocks.get(GUILD, made.id)
        assert again is not None
        self.assertEqual((again.players_channel_id, again.players_message_id), (7, 8))
