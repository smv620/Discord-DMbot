"""The players' clock (#1116, #2877): the 14 approximate words at every boundary, the small
"-# 🕰️ Day 5, morning" line in the campaign's DMbot chat channel (posted when what the players see
changes, edited when it is still the newest message, with no exact time, timer or button), the one
line for a finished rest or a new day, the rests moved off the pinned DM clock into the private
Rests & time… panel, and the migration. The store tests need Postgres."""

from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.db import Database, drop_schema
from dmbot.dm_screen import clock as ui
from dmbot.schema import MIGRATIONS
from dmbot.timebot import clock as game
from dmbot.timebot.clock import Clock
from dmbot.timebot.store import ClockSection, ClockStore
from tests.pg import TEST_URL, DatabaseTest

GUILD, OTHER_GUILD = 111, 222
DM, PLAYER = 7, 8
CHAT, SCREEN, BOT_ID = 700, 555, 4242
NOON4 = game.from_day_and_time(4, 12)


def stored_at(clock: Clock) -> Any:
    """Just what the players' view reads of a stored clock."""
    return SimpleNamespace(clock=clock, split=False, groups=())


class DayPart(unittest.TestCase):
    """PLAN's table, at every boundary minute."""

    TABLE = (
        ((3, 30), (4, 59), "pre-dawn"),
        ((5, 0), (6, 14), "dawn"),  # Supervisor, 2026-10-13: 06:00 (the dawn mark) is dawn
        ((6, 15), (9, 59), "morning"),
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

    def test_the_players_line_never_has_an_exact_time(self) -> None:
        for minute in range(0, game.MINUTES_PER_DAY, 7):
            text = ui.small(ui.players_view(stored_at(Clock(minute, 0))))
            self.assertIsNone(re.search(r"\d:\d\d", text), text)
            self.assertNotIn("(", text)
            self.assertNotIn("imer", text)
        self.assertEqual(
            ui.small(ui.players_view(stored_at(Clock(game.from_day_and_time(5, 6, 20), 0)))),
            "-# 🕰️ Day 5, morning",
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
            ui.players_line("dawn", Clock(NOON4, 0), dawn), "A new day begins. Day 5, dawn."
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


class Msg:
    def __init__(self, message_id: int, channel_id: int, text: str, author: int = BOT_ID) -> None:
        self.id = message_id
        self.content = text
        self.author = SimpleNamespace(id=author)
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
    """Channels with messages (newest last), and the bot's `post_message`."""

    def __init__(self, clocks: ClockStore, campaigns: CampaignStore) -> None:
        self.clocks = clocks
        self.campaigns = SimpleNamespace(
            get=campaigns.get, optional_rule_overrides=AsyncMock(return_value={})
        )
        self.user = SimpleNamespace(id=BOT_ID)
        self.messages: dict[int, Msg] = {}
        self.posts: list[tuple[int, str, Any]] = []
        self.next_id = 9000
        self.channels: dict[int, Any] = {}
        self.fetch_error: Exception | None = None
        self.history_error: Exception | None = None
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

        async def history(*, limit: int = 100) -> Any:
            if self.history_error is not None:
                raise self.history_error
            here = [m for m in self.messages.values() if m.channel.id == channel_id]
            for message in reversed(here[-limit:]):  # newest first, as Discord gives them
                yield message

        channel.fetch_message = fetch_message
        channel.history = history
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

    def chat(self, channel_id: int, text: str, author: int = PLAYER) -> Msg:
        """Someone else says something in a channel."""
        self.next_id += 1
        message = Msg(self.next_id, channel_id, text, author)
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
        await self.campaigns.set_dmbchat_channel(GUILD, self.a.id, CHAT)
        self.discord = FakeDiscord(self.clocks, self.campaigns)
        self.discord.channel(SCREEN)
        self.discord.channel(CHAT)

    async def campaign(self) -> Campaign:
        campaign = await self.campaigns.get(GUILD, self.a.id)
        assert campaign is not None
        return campaign

    async def press(self, action: str, *, set_to: int | None = None) -> ui.Result:
        return await ui.press(self.discord, GUILD, self.a.id, DM, action, set_to=set_to)

    async def show(self) -> None:
        """What a press does for the players, from the clock as it is now."""
        await ui.show_players(self.discord, await self.campaign())

    def chat_lines(self) -> list[str]:
        """What the chat channel holds, oldest first."""
        return [m.content for m in self.discord.messages.values() if m.channel.id == CHAT]

    async def players_posts(self) -> list[str]:
        return [text for channel, text, _ in self.discord.posts if channel == CHAT]

    async def test_a_change_in_what_players_see_posts_one_small_line(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.show()
        self.assertEqual(self.chat_lines(), ["-# 🕰️ Day 4, about noon"])
        self.assertFalse(self.discord.posts[0][2])  # no buttons
        self.assertFalse(next(iter(self.discord.messages.values())).pinned)  # and nothing pinned

    async def test_a_change_inside_the_same_word_posts_nothing(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.show()
        message = next(iter(self.discord.messages.values()))
        await self.press("m10")  # still "about noon"
        await self.show()
        self.assertEqual((len(self.discord.posts), message.edits), (1, []))

    async def test_two_quick_changes_edit_the_one_line(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.show()
        await self.press("h1")
        await self.show()
        await self.press("h1")
        await self.show()
        self.assertEqual(len(await self.players_posts()), 1)  # edited, not posted again
        self.assertEqual(self.chat_lines(), ["-# 🕰️ Day 4, afternoon"])
        for line in self.chat_lines():
            self.assertIsNone(re.search(r"\d:\d\d", line))  # the exact time never appears

    async def test_a_line_after_other_chat_messages_is_a_new_line(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.show()
        self.discord.chat(CHAT, "Hey DMbot, how does grappling work?")
        await self.press("h1")
        await self.show()
        self.assertEqual(
            [line for line in self.chat_lines() if line.startswith("-#")],
            ["-# 🕰️ Day 4, about noon", "-# 🕰️ Day 4, afternoon"],
        )
        self.assertEqual(len(self.chat_lines()), 3)

    async def test_someone_elses_clock_looking_line_is_never_edited(self) -> None:
        await self.press("set", set_to=NOON4)
        faked = self.discord.chat(CHAT, "-# 🕰️ Day 1, dawn")  # a player typed it
        await self.show()
        self.assertEqual(faked.edits, [])
        self.assertEqual(self.chat_lines()[-1], "-# 🕰️ Day 4, about noon")

    async def test_a_deleted_line_gets_a_new_one_and_a_new_chat_channel_its_own(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.show()
        self.discord.messages.clear()  # someone deleted it
        await self.show()
        self.assertEqual(len(await self.players_posts()), 2)
        await self.campaigns.set_dmbchat_channel(GUILD, self.a.id, 701)
        self.discord.channel(701)
        await self.show()
        self.assertEqual(self.discord.posts[-1][0], 701)

    async def test_without_a_chat_channel_nothing_happens_and_nothing_breaks(self) -> None:
        await self.campaigns.set_dmbchat_channel(GUILD, self.a.id, None)
        await self.press("set", set_to=NOON4)
        await self.show()
        self.assertEqual(self.discord.posts, [])
        # A channel DMbot cannot read, or Discord refusing, is skipped too: the DM's clock is the
        # one that matters at the table.
        await self.campaigns.set_dmbchat_channel(GUILD, self.a.id, CHAT)
        self.discord.history_error = discord.HTTPException(MagicMock(status=403), "no")
        await self.show()
        self.assertEqual(self.discord.posts, [])
        self.discord.history_error = None
        self.discord.post_message = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
        await self.show()  # a failure is swallowed

    async def test_a_channel_missing_from_the_cache_posts_nothing(self) -> None:
        await self.press("set", set_to=NOON4)
        del self.discord.channels[CHAT]
        await self.show()
        self.assertEqual(self.discord.posts, [])

    async def test_a_rest_and_a_new_day_are_each_said_once_in_small_text(self) -> None:
        await self.press("set", set_to=NOON4)
        await ui.update_players(self.discord, await self.campaign(), "set", await self.press("m10"))
        result = await self.press("long")
        await ui.update_players(self.discord, await self.campaign(), "long", result)
        # the rest stands in for the plain line: it holds the day and the word too
        self.assertEqual(self.chat_lines(), ["-# 🕰️ Long rest done. Day 4, evening."])
        late = game.from_day_and_time(4, 23, 30)
        result = await self.press("set", set_to=late)
        await ui.update_players(self.discord, await self.campaign(), "set", result)
        result = await self.press("h1")
        await ui.update_players(self.discord, await self.campaign(), "h1", result)
        self.assertEqual(
            self.chat_lines(),
            [
                "-# 🕰️ Long rest done. Day 4, evening.",  # kept: it was a rest, not a clock line
                "-# 🕰️ A new day begins. Day 5, near midnight.",
            ],
        )
        result = await self.press("m10")
        await ui.update_players(self.discord, await self.campaign(), "m10", result)
        self.assertEqual(len(self.chat_lines()), 2)  # nothing for ten minutes
        # No tired line, no effect lines, no dawn/noon/dusk lines reach the players.
        for text in self.chat_lines():
            self.assertNotIn("Check:", text)
            self.assertNotIn("Dawn", text)
            self.assertNotIn("⏳", text)
            self.assertTrue(text.startswith("-# "))  # always Discord's small text


def _button(item: Any) -> Any:
    """The real button inside a dynamic item (or the item itself), untyped for the tests."""
    return getattr(item, "item", item)


class Buttons(unittest.TestCase):
    CID = "0123456789abcdef0123456789abcdef"

    def labels(self, view: discord.ui.View) -> list[str]:
        return [str(_button(item).label) for item in view.children]

    def test_the_pinned_dm_clock_has_no_rest_buttons(self) -> None:
        view = ui.clock_view(self.CID)
        self.assertEqual(
            self.labels(view),
            ["+10 min", "+1 hour", "Rests & time…", "Start a timer", "Start combat"],  # #1127
        )
        # (the opener "Rests & time…" is the one label that names them; the buttons are inside)
        others = [label for label in self.labels(view) if label != "Rests & time…"]
        for banned in ("rest", "dawn"):
            self.assertFalse(any(banned in label.lower() for label in others))

    def test_the_set_time_panel_has_them(self) -> None:
        view = ui.panel_view(self.CID)
        self.assertEqual(
            self.labels(view),
            ["Short rest", "Long rest", "Skip to dawn", "Type the time…", "Split the party"],
        )
        rows = [_button(item).row for item in view.children]
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
    """Pressing **Rests & time…** opens the private panel (once a clock exists)."""

    def interaction(self, message: Any = None) -> Any:
        self.discord.campaigns.get = self.campaigns.get
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
        labels = [_button(i).label for i in sent.kwargs["view"].children]
        self.assertEqual(
            labels, ["Short rest", "Long rest", "Skip to dawn", "Type the time…", "Split the party"]
        )

    async def test_typing_a_time_and_the_first_time_open_the_form(self) -> None:
        await self.press("set", set_to=NOON4)
        it = self.interaction()
        await ui.ClockButton(self.a.id, "typed").callback(cast(Any, it))
        it.response.send_modal.assert_awaited_once()
        # Before any clock exists, Rests & time… goes straight to the form.
        other = await self.campaigns.create(GUILD, "Beta", DM)
        it2 = self.interaction()
        await ui.ClockButton(other.id, "set").callback(cast(Any, it2))
        it2.response.send_modal.assert_awaited_once()
        it2.response.send_message.assert_not_awaited()

    async def test_a_rest_from_the_panel_moves_the_dm_clock_and_says_so_to_the_players(
        self,
    ) -> None:
        await self.press("set", set_to=NOON4)
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        campaign = await self.campaign()
        await ui.show(self.discord, campaign, stored)  # the DM's pinned message
        await self.show()
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        dm_message = self.discord.messages[stored.message_id or 0]
        panel = Msg(1, SCREEN, ui.PANEL_TEXT)  # the ephemeral panel
        it = self.interaction(message=panel)
        await ui.ClockButton(self.a.id, "long").callback(cast(Any, it))
        self.assertEqual(dm_message.content.splitlines()[0], "🕰️ **Day 4, evening (20:00)**")
        self.assertEqual(self.chat_lines(), ["-# 🕰️ Long rest done. Day 4, evening."])
        self.assertTrue(it.followup.send.await_args.kwargs["ephemeral"])  # the Undo note

    async def test_a_press_on_the_pinned_message_edits_it_and_tells_the_players(self) -> None:
        await self.press("set", set_to=NOON4)
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        campaign = await self.campaign()
        await ui.show(self.discord, campaign, stored)
        await self.show()
        stored = await self.clocks.get(GUILD, self.a.id)
        assert stored is not None
        dm_message = self.discord.messages[stored.message_id or 0]
        it = self.interaction(message=dm_message)
        await ui.ClockButton(self.a.id, "h1").callback(cast(Any, it))
        self.assertIn("(13:00)", dm_message.content)
        self.assertEqual(self.chat_lines(), ["-# 🕰️ Day 4, afternoon"])  # the one line, edited


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


class SayingWhereAndTakingBack(SetTimePanel):
    async def test_the_players_sentence_is_only_said_when_the_chat_channel_exists(self) -> None:
        campaign = await self.campaign()
        self.assertIn(f"<#{CHAT}>", ui.players_see(campaign))
        await self.campaigns.set_dmbchat_channel(GUILD, self.a.id, None)
        self.assertEqual(ui.players_see(await self.campaign()), "")

    async def test_an_undone_rest_is_corrected_for_the_players(self) -> None:
        await self.press("set", set_to=NOON4)
        result = await self.press("long")
        assert result.before is not None and result.after is not None
        it = self.interaction()
        await ui.ClockUndoButton(self.a.id, ui.undo_id(result.before, result.after)).callback(
            cast(Any, it)
        )
        self.assertEqual(self.chat_lines()[-1], "-# 🕰️ Correction: Day 4, about noon.")
        # Nothing to take back (the clock moved since): nothing is said.
        before = len(await self.players_posts())
        await ui.ClockUndoButton(self.a.id, ui.undo_id(result.before, result.after)).callback(
            cast(Any, it)
        )
        self.assertEqual(len(await self.players_posts()), before)


class NoSecondClock(PlayersClock):
    async def test_two_updates_at_once_post_one_line(self) -> None:
        import asyncio

        await self.press("set", set_to=NOON4)
        campaign = await self.campaign()
        await asyncio.gather(
            ui.show_players(self.discord, campaign),
            ui.show_players(self.discord, campaign),
            ui.show_players(self.discord, campaign),
        )
        self.assertEqual(len(await self.players_posts()), 1)

    async def test_older_words_never_land_over_newer(self) -> None:
        await self.press("set", set_to=NOON4)
        stale = await self.clocks.get(GUILD, self.a.id)
        await self.show()
        await self.press("h1")
        await self.press("h1")  # now afternoon... and later
        await ui.show_players(
            self.discord, await self.campaign(), stale
        )  # a caller holding old state
        self.assertEqual(self.chat_lines(), ["-# 🕰️ Day 4, afternoon"])

    async def test_a_typed_time_from_the_panel_gets_its_reply(self) -> None:
        panel = Msg(1, SCREEN, ui.PANEL_TEXT)
        it = SimpleNamespace(
            client=self.discord,
            guild=SimpleNamespace(id=GUILD),
            user=SimpleNamespace(id=DM),
            message=panel,
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        await ui.run_press(cast(Any, it), self.a.id, "set", set_to=NOON4)
        sent = it.followup.send.await_args
        self.assertIn("Clock set to Day 4, about noon", sent.args[0])
        self.assertIn(f"<#{CHAT}>", sent.args[0])


class Wording(unittest.TestCase):
    """The plain-words pass from the ux-critic (#1197)."""

    def test_the_bad_time_message_does_not_point_back_at_a_button(self) -> None:
        from dmbot.dm_screen import clock

        self.assertNotIn("**", clock.BAD_TIME)  # no button name to press again
        self.assertIn("day 4", clock.BAD_TIME)

    def test_the_combat_lines_use_the_buttons_word(self) -> None:
        from dmbot.dm_screen import clock

        for line in clock.NO_COMBAT_CHANGE.values():
            self.assertIn("combat", line.lower())
            self.assertNotIn("fight", line.lower())
        self.assertIn("round 99", clock.NO_COMBAT_CHANGE["cnext"])
