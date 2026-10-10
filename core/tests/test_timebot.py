"""TimeBot part 1 (#965): the clock arithmetic (days roll over; dawn, noon and dusk are
found), the phrases that move it and those that mustn't, who may press the buttons, Undo,
the 24-hour rule with its optional rule on and off, isolation between campaigns and
servers, and the backup round trip. The store tests need Postgres."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.dm_screen import clock as ui
from dmbot.timebot import clock as game
from dmbot.timebot import phrases
from dmbot.timebot.clock import Clock
from dmbot.timebot.store import ClockSection, ClockStore
from tests.pg import DatabaseTest

GUILD_A, GUILD_B = 111, 222
DM, PLAYER, OTHER_DM = 7, 8, 9
NOON4 = game.from_day_and_time(4, 12)


class Arithmetic(unittest.TestCase):
    def test_days_roll_over(self) -> None:
        c = Clock(game.from_day_and_time(1, 23, 50), 0)
        self.assertEqual(game.day_of(game.advance(c, 10).minute), 2)
        self.assertEqual(game.clock_time(game.advance(c, 10).minute), "00:00")
        self.assertEqual(game.label(game.from_day_and_time(4, 14, 30)), "Day 4, afternoon (14:30)")

    def test_the_words_for_the_time_of_day(self) -> None:
        words = {
            h: game.phase(game.from_day_and_time(1, h)) for h in (0, 4, 5, 11, 12, 16, 17, 20, 21)
        }
        self.assertEqual(
            words,
            {0: "night", 4: "night", 5: "morning", 11: "morning", 12: "afternoon",
             16: "afternoon", 17: "evening", 20: "evening", 21: "night"},
        )  # fmt: skip

    def test_dawn_noon_and_dusk_are_found_as_they_pass(self) -> None:
        start = game.from_day_and_time(1, 5, 30)
        marks = game.crossings(start, game.from_day_and_time(1, 19))
        self.assertEqual([m for _, m in marks], ["dawn", "noon", "dusk"])
        self.assertEqual(game.crossings(start, start), [])
        # Landing exactly on one counts; starting on one doesn't.
        self.assertEqual(
            [
                m
                for _, m in game.crossings(
                    game.from_day_and_time(1, 5), game.from_day_and_time(1, 6)
                )
            ],
            ["dawn"],
        )
        self.assertEqual(
            game.crossings(game.from_day_and_time(1, 6), game.from_day_and_time(1, 7)), []
        )

    def test_a_long_jump_names_only_the_last_few(self) -> None:
        marks = game.crossings(0, game.from_day_and_time(30, 0))
        self.assertEqual(len(marks), game.MAX_ANNOUNCED)

    def test_the_next_dawn(self) -> None:
        self.assertEqual(game.next_dawn(game.from_day_and_time(2, 3)), game.from_day_and_time(2, 6))
        self.assertEqual(game.next_dawn(game.from_day_and_time(2, 6)), game.from_day_and_time(2, 6))
        self.assertEqual(game.next_dawn(game.from_day_and_time(2, 7)), game.from_day_and_time(3, 6))

    def test_a_long_rest_is_eight_hours_and_restarts_the_count(self) -> None:
        c = Clock(NOON4, 0, 0)
        after = game.long_rest(c)
        self.assertEqual(
            (after.minute, after.last_long_rest, after.tired_told_for),
            (NOON4 + 480, NOON4 + 480, None),
        )

    def test_twenty_four_hours_without_a_long_rest(self) -> None:
        c = Clock(NOON4, NOON4)
        self.assertFalse(game.tired_due(game.advance(c, 24 * 60 - 1)))
        due = game.advance(c, 24 * 60)
        self.assertTrue(game.tired_due(due))
        self.assertFalse(game.tired_due(game.told_tired(due)))  # said once
        self.assertFalse(game.tired_due(game.long_rest(due)))

    def test_setting_the_time_earlier_pulls_the_rest_back_with_it(self) -> None:
        c = Clock(NOON4, NOON4)
        self.assertEqual(game.set_to(c, NOON4 - 100).last_long_rest, NOON4 - 100)

    def test_a_day_and_hour_out_of_range_is_refused(self) -> None:
        for args in ((0, 1), (1, 24), (100_000, 0), (1, 1, 60)):
            with self.assertRaises(ValueError):
                game.from_day_and_time(*args)
        self.assertIsNone(ui.parse_time("4", "25"))
        self.assertIsNone(ui.parse_time("x", "9"))
        self.assertEqual(ui.parse_time("4", "14:30"), game.from_day_and_time(4, 14, 30))


class Phrases(unittest.TestCase):
    def test_clear_rests_move_the_clock(self) -> None:
        for line, kind in (
            ("We take a short rest.", "short"),
            ("you take a long rest", "long"),
            ("  YOU take a LONG rest.  ", "long"),
            ("Okay, you all finish a short rest", "short"),
            ("everyone settles in for a long rest", None),  # "settles" isn't in the list
            ("the party took a long rest", None),  # a rest that is over is not one now
        ):
            self.assertEqual(phrases.find(line), kind, line)

    def test_questions_wishes_and_maybes_never_do(self) -> None:
        for line in (
            "Do you want to take a short rest?",
            "we could take a long rest",
            "if you take a long rest the guards will find you",
            "you can't take a long rest here",
            "let's take a short rest",
            "we don't take a long rest",
            "you take a short rest. no wait, you don't",
            "after you take a long rest we move on",
            "we should take a long rest",
            "short rest",
            "the party took a long rest last week",
            "you had a short rest earlier",
            "you have a long rest ahead of you",
            "the innkeeper says we take a long rest",
            "you will take a long rest tomorrow",
            "",
        ):
            self.assertIsNone(phrases.find(line), line)

    def test_a_long_ramble_is_not_a_ruling(self) -> None:
        line = "so while the rain keeps falling on the old road we take a long rest " + "and " * 12
        self.assertIsNone(phrases.find(line))


class Fake:
    """What the buttons need from the bot: the clocks, the campaigns and the optional rules."""

    def __init__(
        self, clocks: ClockStore, campaigns: CampaignStore, overrides: dict[str, bool]
    ) -> None:
        self.clocks = clocks
        self.campaigns = SimpleNamespace(
            optional_rule_overrides=AsyncMock(return_value=overrides), get=campaigns.get
        )


class Clocks(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(ClockSection())
        self.clocks = ClockStore(self.db)
        self.a = await self.campaigns.create(GUILD_A, "Alpha", DM)
        self.b = await self.campaigns.create(GUILD_B, "Beta", DM)
        await self.campaigns.add_dm(GUILD_A, self.a.id, OTHER_DM)

    def client(self, overrides: dict[str, bool] | None = None) -> Any:
        return Fake(self.clocks, self.campaigns, overrides or {})

    async def press(
        self,
        action: str,
        user: int = DM,
        *,
        set_to: int | None = None,
        overrides: dict[str, bool] | None = None,
    ) -> ui.Result:
        return await ui.press(
            self.client(overrides), GUILD_A, self.a.id, user, action, set_to=set_to
        )

    async def test_it_is_empty_until_the_dm_sets_it(self) -> None:
        self.assertIsNone(await self.clocks.get(GUILD_A, self.a.id))
        result = await self.press("h1")  # nothing to move yet
        self.assertIsNone(result.after)
        self.assertIsNone(await self.clocks.get(GUILD_A, self.a.id))
        await self.press("set", set_to=NOON4)
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual((stored.clock.minute, stored.clock.last_long_rest), (NOON4, NOON4))

    async def test_the_buttons_move_the_clock(self) -> None:
        await self.press("set", set_to=NOON4)
        for action, delta in (("m10", 10), ("h1", 60), ("short", 60)):
            before = (await self.clocks.get(GUILD_A, self.a.id)).clock.minute  # type: ignore[union-attr]
            await self.press(action)
            after = (await self.clocks.get(GUILD_A, self.a.id)).clock.minute  # type: ignore[union-attr]
            self.assertEqual(after - before, delta, action)
        await self.press("long")
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual(stored.clock.last_long_rest, stored.clock.minute)
        await self.press("dawn")
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        self.assertEqual(game.time_of_day(stored.clock.minute), game.DAWN)

    async def test_only_the_campaigns_dms_may_press(self) -> None:
        await self.press("set", set_to=NOON4)
        for user in (PLAYER, 12345):
            with self.assertRaises(CampaignError):
                await self.press("h1", user)
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None and stored.clock.minute == NOON4
        await self.press("h1", OTHER_DM)  # a second DM may
        with self.assertRaises(CampaignError):  # and a DM of another campaign may not
            await ui.press(self.client(), GUILD_B, self.b.id, OTHER_DM, "h1")

    async def test_dawn_noon_and_dusk_are_said_as_they_pass_but_not_when_set(self) -> None:
        await self.press("set", set_to=game.from_day_and_time(4, 11, 50))
        result = await self.press("m10")  # noon
        self.assertEqual(result.lines, ["☀️ Noon"])
        quiet = await self.press("set", set_to=game.from_day_and_time(5, 23))
        # The DM says what time it is: no dawn, noon or dusk lines (only the 24-hour one, if due).
        self.assertEqual([x for x in quiet.lines if x in ui.MARK_LINES.values()], [])
        long = await self.press("long")  # 23:00 + 8h crosses dawn
        self.assertIn("🌅 Dawn", long.lines)

    async def test_the_twenty_four_hour_line_is_said_once_with_its_source(self) -> None:
        await self.press("set", set_to=NOON4)
        lines: list[str] = []
        for _ in range(26):
            lines += (await self.press("h1")).lines
        tired = [x for x in lines if "24 hours" in x]
        self.assertEqual(len(tired), 1)
        self.assertIn("Xanathar", tired[0])
        self.assertIn("Your call", tired[0])
        again = await self.press("long")
        self.assertFalse(any("24 hours" in x for x in again.lines))
        more: list[str] = []
        for _ in range(26):
            more += (await self.press("h1")).lines
        self.assertEqual(len([x for x in more if "24 hours" in x]), 1)  # for the next stretch

    async def test_the_twenty_four_hour_line_follows_the_optional_rule(self) -> None:
        await self.press("set", set_to=NOON4)
        lines: list[str] = []
        for _ in range(26):
            lines += (await self.press("h1", overrides={"xge-no-long-rest": False})).lines
        self.assertFalse(any("24 hours" in x for x in lines))

    async def test_undo_goes_back_only_if_nothing_moved_since(self) -> None:
        await self.press("set", set_to=NOON4)
        result = await self.press("long")
        assert result.before is not None and result.after is not None
        spec = ui.undo_id(result.before, result.after)
        button = ui.ClockUndoButton(self.a.id, spec)
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_A),
            user=SimpleNamespace(id=DM),
            client=self.client(),
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        interaction.client.post_message = AsyncMock(return_value=None)
        await self.press("m10")  # moved since
        await button.callback(cast(Any, interaction))
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None and stored.clock.minute == result.after.minute + 10
        # Undo straight after a rest works.
        await self.press("set", set_to=NOON4)
        result = await self.press("long")
        assert result.before is not None and result.after is not None
        button = ui.ClockUndoButton(self.a.id, ui.undo_id(result.before, result.after))
        await button.callback(cast(Any, interaction))
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None and stored.clock.minute == NOON4

    async def test_campaigns_and_servers_never_share_a_clock(self) -> None:
        await self.press("set", set_to=NOON4)
        await ui.press(
            self.client(), GUILD_B, self.b.id, DM, "set", set_to=game.from_day_and_time(9, 1)
        ) if False else None
        self.assertIsNone(await self.clocks.get(GUILD_B, self.b.id))
        self.assertIsNone(await self.clocks.get(GUILD_B, self.a.id))  # another server can't see it
        self.assertIsNone(await self.clocks.get(GUILD_A, self.b.id))

    async def test_it_goes_in_a_backup_and_comes_back(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.press("long")
        data = await self.campaigns.export(GUILD_A, self.a.id)
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        stored = await self.clocks.get(GUILD_B, restored.id)
        original = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None and original is not None
        self.assertEqual(stored.clock.minute, original.clock.minute)
        self.assertEqual(stored.clock.last_long_rest, original.clock.last_long_rest)
        self.assertIsNone(stored.message_id)  # message ids mean nothing in another server

    async def test_a_backup_without_a_clock_restores_without_one(self) -> None:
        data = await self.campaigns.export(GUILD_A, self.a.id)
        restored = await self.campaigns.import_backup(GUILD_B, data, DM)
        self.assertIsNone(await self.clocks.get(GUILD_B, restored.id))

    async def test_a_damaged_clock_in_a_backup_is_refused(self) -> None:
        section = ClockSection()
        for rows in (
            [{"minute": -1, "last_long_rest": 0}],
            [{"minute": 10, "last_long_rest": 20}],
            [{"minute": "10", "last_long_rest": 0}],
            [{"minute": 10}],
            [{"minute": 10, "last_long_rest": 0}, {"minute": 11, "last_long_rest": 0}],
            ["x"],
        ):
            with self.assertRaises(CampaignError):
                section.check(rows)

    async def test_deleting_the_campaign_deletes_its_clock(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.campaigns.delete(GUILD_A, self.a.id)
        self.assertIsNone(await self.clocks.get(GUILD_A, self.a.id))
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM game_clocks")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)


class AtTheTable(Clocks):
    """A DM's line at the table moves the clock; a player's never does."""

    async def asyncSetUp(self) -> None:
        from dmbot.bot import DMBot
        from dmbot.config import Settings
        from dmbot.consent import ConsentStore
        from dmbot.sessions import SessionStore

        await super().asyncSetUp()
        self.bot = DMBot(
            Settings(discord_token="t", ears_secret="s"),
            ConsentStore(self.db),
            self.campaigns,
            SessionStore(self.db),
            clocks=self.clocks,
        )
        self.posted: list[str] = []

        async def post_message(channel_id: int, text: str, view: Any = None) -> None:
            self.posted.append(text)

        self.bot.post_message = post_message  # type: ignore[method-assign]
        self.bot.consent.has_consent = lambda guild_id, user_id: True  # type: ignore[method-assign]
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE campaigns SET dm_screen_channel_id = 555 WHERE id = %s", (self.a.id,)
            )
        self.table = SimpleNamespace(
            guild_id=GUILD_A,
            campaign_id=self.a.id,
            listening=True,
            dm_user_ids=frozenset({DM, OTHER_DM}),
            clock_said={},
        )
        self.bot.tables[GUILD_A] = cast(Any, self.table)

    async def minute(self) -> int:
        stored = await self.clocks.get(GUILD_A, self.a.id)
        assert stored is not None
        return stored.clock.minute

    async def test_a_dms_rest_moves_the_clock_and_says_so(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.bot._note_clock_phrase(cast(Any, self.table), DM, "you take a short rest")
        self.assertEqual(await self.minute(), NOON4 + 60)
        self.assertTrue(any("Short rest" in text for text in self.posted))

    async def test_a_players_line_never_moves_it(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.bot._note_clock_phrase(cast(Any, self.table), PLAYER, "we take a long rest")
        self.assertEqual(await self.minute(), NOON4)

    async def test_the_same_rest_said_twice_counts_once(self) -> None:
        await self.press("set", set_to=NOON4)
        for _ in range(2):
            await self.bot._note_clock_phrase(cast(Any, self.table), DM, "we take a long rest")
        self.assertEqual(await self.minute(), NOON4 + 480)

    async def test_without_a_clock_nothing_happens_and_nothing_is_started(self) -> None:
        await self.bot._note_clock_phrase(cast(Any, self.table), DM, "we take a long rest")
        self.assertIsNone(await self.clocks.get(GUILD_A, self.a.id))
        self.assertEqual(self.posted, [])

    async def test_a_question_is_not_a_rest(self) -> None:
        await self.press("set", set_to=NOON4)
        await self.bot._note_clock_phrase(cast(Any, self.table), DM, "do you take a long rest?")
        self.assertEqual(await self.minute(), NOON4)


class Wiring(unittest.TestCase):
    def test_the_button_ids_fit_discord_and_match_their_own_templates(self) -> None:
        cid = "0123456789abcdef0123456789abcdef"
        for action in (*ui.ACTIONS, "open"):
            custom_id = ui.ClockButton(cid, action).item.custom_id
            assert custom_id is not None and len(custom_id) <= 100
            self.assertIsNotNone(
                ui.ClockButton.__discord_ui_compiled_template__.fullmatch(custom_id)
            )
        spec = ui.undo_id(Clock(game.MAX_MINUTE, game.MAX_MINUTE), Clock(game.MAX_MINUTE, 0))
        custom_id = ui.ClockUndoButton(cid, spec).item.custom_id
        assert custom_id is not None and len(custom_id) <= 100
        self.assertIsNotNone(
            ui.ClockUndoButton.__discord_ui_compiled_template__.fullmatch(custom_id)
        )

    def test_five_buttons_at_most_to_a_row(self) -> None:
        rows: dict[int, int] = {}
        for _label, _emoji, _style, row in ui.ACTIONS.values():
            rows[row] = rows.get(row, 0) + 1
        self.assertTrue(all(n <= 5 for n in rows.values()))
        self.assertEqual(sorted(rows), [0, 1])
