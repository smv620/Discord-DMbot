"""FightBot (#1129): the leak rule (nothing but the player's own sheet, the round and the table's
own lines, without secret names, ever reaches the prompt), only in combat, at most a call every
two minutes per player and only when something changed, each way to silence it, and that it is
always a private message. The store tests need Postgres."""

from __future__ import annotations

import unittest
from collections import deque
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot import fightbot
from dmbot.ai import AIError, Reply
from dmbot.memory import fight_prompt
from dmbot.memory.sheet_store import CharacterSheet
from dmbot.timebot.clock import Clock
from tests.test_memory_store import GUILD_A
from tests.test_sheet_reread import base
from tests.test_sheet_store import PLAYER, SheetTest

GUILD, CAMPAIGN = 111, "a" * 32
SECRET = "Zargothrax"


def sheet(*, user: int | None = PLAYER, fight: bool = True, **extra: Any) -> CharacterSheet:
    snap = {**base(), "spells": ["Hunter's Mark", "Cure Wounds"], **extra}
    return CharacterSheet("e1", "Belleros", user, None, snap, 1, fightbot=fight)


class Prompt(unittest.TestCase):
    def test_the_sheet_facts_are_only_the_allow_listed_fields(self) -> None:
        snap = {**base(), "notes": "LEAK-NOTE", "backstory": "LEAK-STORY", "email": "x@y"}
        facts = fight_prompt.sheet_facts(snap)
        self.assertIn(f"Character: {snap['name']}", facts)
        self.assertIn(snap["spells"][0], facts)
        self.assertIn(snap["features"][0], facts)
        for leak in ("LEAK-NOTE", "LEAK-STORY", "x@y"):
            self.assertNotIn(leak, facts)

    def test_a_line_with_a_secret_name_is_left_out_whole(self) -> None:
        lines = [
            ("Mira", "I check the door"),
            ("DM", f"{SECRET.lower()} is the lich behind it all"),
            ("DM", "You hear a growl"),
        ]
        kept = fight_prompt.safe_lines(lines, [SECRET])
        self.assertEqual(kept, ["Mira: I check the door", "DM: You hear a growl"])
        self.assertEqual(fight_prompt.safe_lines(lines, []), [f"{a}: {b}" for a, b in lines])

    def test_only_the_last_few_lines_in_a_short_prompt(self) -> None:
        lines = [("A", f"line {i} " + "x" * 150) for i in range(30)]
        kept = fight_prompt.safe_lines(lines, [])
        self.assertLessEqual(len(kept), fight_prompt.LINES_MAX)
        self.assertLessEqual(sum(len(k) for k in kept), fight_prompt.LINES_CHARS + 250)
        self.assertIn("line 29", kept[-1])

    def test_what_counts_as_something_new(self) -> None:
        snap = base()
        a = fight_prompt.fingerprint(snap, 2)
        self.assertEqual(a, fight_prompt.fingerprint({**snap, "spells": ["x"]}, 2))  # not a change
        self.assertNotEqual(a, fight_prompt.fingerprint(snap, 3))  # a new round
        self.assertNotEqual(a, fight_prompt.fingerprint({**snap, "hp": 3}, 2))
        self.assertNotEqual(a, fight_prompt.fingerprint({**snap, "conditions": ["Prone"]}, 2))

    def test_replies(self) -> None:
        names = ["Cure Wounds", "Hunter's Mark"]
        self.assertEqual(
            fight_prompt.clean_reply("  You still have\nCure Wounds. ", names),
            "You still have Cure Wounds.",
        )
        self.assertIsNone(fight_prompt.clean_reply("NOTHING", names))
        self.assertIsNone(fight_prompt.clean_reply("nothing to add", names))
        self.assertIsNone(fight_prompt.clean_reply("", names))
        self.assertIsNone(fight_prompt.clean_reply("Cure Wounds " + "x" * 400, names))
        # Not from the sheet (or steered by the table talk): never sent.
        self.assertIsNone(fight_prompt.clean_reply("Tell Bob to flee from the dragon.", names))
        self.assertIsNone(fight_prompt.clean_reply("You have Fireball.", names))


class Runner(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now = 1000.0
        self.patch = patch("time.monotonic", lambda: self.now)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def table(self) -> Any:
        return SimpleNamespace(
            guild_id=GUILD,
            campaign_id=CAMPAIGN,
            listening=True,
            fight=fightbot.FightState(),
            recent=SimpleNamespace(
                lines=deque(
                    [
                        (990.0, PLAYER, "I swing my axe"),
                        (991.0, 5, f"{SECRET} laughs"),
                        (992.0, 77, "a withdrawn player's line"),
                    ]
                )
            ),
        )

    def bot(
        self,
        table: Any,
        *,
        round_number: int | None = 1,
        sheets_: list[CharacterSheet] | None = None,
        reply: str = "You still have Hunter's Mark.",
        plan: bool = True,
    ) -> Any:
        user = SimpleNamespace(send=AsyncMock())
        ai = SimpleNamespace(complete=AsyncMock(return_value=Reply(reply, False)))
        clock = None if round_number is None else Clock(0, 0, None, round_number, 0)
        stored = None if clock is None else SimpleNamespace(clock=clock)
        return SimpleNamespace(
            tables={GUILD: table},
            fight_ai=ai,
            sheets=SimpleNamespace(sheets=AsyncMock(return_value=sheets_ or [sheet()])),
            clocks=SimpleNamespace(get=AsyncMock(return_value=stored)),
            consent=SimpleNamespace(has_consent=lambda g, u: u != 77),
            campaigns=SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(screen="DMSCREEN-LEAK"))
            ),
            plan_allows=AsyncMock(return_value=plan),
            lookup=SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        names=[
                            SimpleNamespace(text=SECRET, secret=True),
                            SimpleNamespace(text="Waterdeep", secret=False),
                        ]
                    )
                )
            ),
            name_of=lambda g, u: {PLAYER: "Mira", 5: "DM"}.get(u, f"u{u}"),
            get_user=lambda uid: user,
            fetch_user=AsyncMock(return_value=user),
            post=AsyncMock(),
            post_message=AsyncMock(),
            user=user,
        )

    async def test_a_reminder_is_a_private_message_with_the_three_silences(self) -> None:
        table = self.table()
        bot = self.bot(table)
        await fightbot.tick(bot, table)
        bot.user.send.assert_awaited_once()
        text = bot.user.send.await_args.args[0]
        self.assertIn("You still have Hunter's Mark.", text)
        labels = [str(b.item.label) for b in bot.user.send.await_args.kwargs["view"].children]
        self.assertEqual(labels, ["Snooze 10 min", "Quiet this session", "Stop FightBot"])
        self.assertIn("FightBot reminds you", text)  # the first one says what it is
        self.assertIn("It's your call", text)
        bot.post.assert_not_awaited()  # never in a channel
        bot.post_message.assert_not_awaited()

    async def test_the_prompt_holds_nothing_but_the_sheet_the_round_and_safe_table_lines(
        self,
    ) -> None:
        table = self.table()
        bot = self.bot(table, sheets_=[sheet(notes="LEAK-NOTE")])
        await fightbot.tick(bot, table)
        system, prompt = bot.fight_ai.complete.await_args.args
        self.assertEqual(system, fight_prompt.PROMPT)
        self.assertIn("Round: 1", prompt)
        self.assertIn("Mira: I swing my axe", prompt)
        for leak in (SECRET, "LEAK-NOTE", "DMSCREEN-LEAK", "withdrawn player", "Waterdeep"):
            self.assertNotIn(leak, prompt)
            self.assertNotIn(leak, system)

    async def test_nothing_is_said_outside_combat(self) -> None:
        table = self.table()
        bot = self.bot(table, round_number=None)
        await fightbot.tick(bot, table)
        bot2 = self.bot(table, round_number=None)
        bot2.clocks.get = AsyncMock(
            return_value=SimpleNamespace(clock=Clock(0, 0))
        )  # a clock, no fight
        await fightbot.tick(bot2, table)
        for b in (bot, bot2):
            b.fight_ai.complete.assert_not_awaited()
            b.user.send.assert_not_awaited()

    async def test_at_most_one_call_every_two_minutes_and_only_when_something_changed(self) -> None:
        table = self.table()
        bot = self.bot(table)
        await fightbot.tick(bot, table)
        await fightbot.tick(bot, table)  # nothing changed
        self.assertEqual(bot.fight_ai.complete.await_count, 1)
        bot.clocks.get = AsyncMock(
            return_value=SimpleNamespace(clock=Clock(0, 0, None, 2, 0))
        )  # a new round, but too soon
        self.now += 60
        await fightbot.tick(bot, table)
        self.assertEqual(bot.fight_ai.complete.await_count, 1)
        self.now += 61  # two minutes since the call
        await fightbot.tick(bot, table)
        self.assertEqual(bot.fight_ai.complete.await_count, 2)
        self.assertEqual(bot.user.send.await_count, 2)

    async def test_the_plan_is_asked_once_a_tick_not_once_a_player(self) -> None:
        table = self.table()
        others = [CharacterSheet(f"x{i}", f"P{i}", 40 + i, None, base(), 1) for i in range(3)]
        bot = self.bot(table, sheets_=[sheet(), *others])
        await fightbot.tick(bot, table)
        self.assertEqual(bot.fight_ai.complete.await_count, 4)
        self.assertEqual(bot.plan_allows.await_count, 1)

    async def test_a_changed_condition_asks_again_after_the_gap(self) -> None:
        table = self.table()
        bot = self.bot(table)
        await fightbot.tick(bot, table)
        bot.sheets.sheets = AsyncMock(return_value=[sheet(conditions=["Prone"])])
        self.now += 130
        await fightbot.tick(bot, table)
        self.assertEqual(bot.fight_ai.complete.await_count, 2)

    async def test_nothing_to_say_sends_nothing_and_does_not_ask_again_for_the_same_thing(
        self,
    ) -> None:
        table = self.table()
        bot = self.bot(table, reply="NOTHING")
        await fightbot.tick(bot, table)
        self.now += 300
        await fightbot.tick(bot, table)  # same round, same sheet
        bot.user.send.assert_not_awaited()
        self.assertEqual(bot.fight_ai.complete.await_count, 1)

    async def test_snooze_and_quiet_until_next_session(self) -> None:
        table = self.table()
        bot = self.bot(table)
        table.fight.snoozed_until[PLAYER] = self.now + fightbot.SNOOZE_S
        await fightbot.tick(bot, table)
        bot.fight_ai.complete.assert_not_awaited()
        self.now += fightbot.SNOOZE_S + 1  # over: they are reminded again
        await fightbot.tick(bot, table)
        self.assertEqual(bot.fight_ai.complete.await_count, 1)
        table.fight.quiet.add(PLAYER)
        bot.clocks.get = AsyncMock(return_value=SimpleNamespace(clock=Clock(0, 0, None, 9, 0)))
        self.now += 1000
        await fightbot.tick(bot, table)
        self.assertEqual(bot.fight_ai.complete.await_count, 1)  # quiet for the rest of the session

    async def test_never_is_kept_on_the_sheet_and_respected(self) -> None:
        table = self.table()
        bot = self.bot(table, sheets_=[sheet(fight=False)])
        await fightbot.tick(bot, table)
        bot.fight_ai.complete.assert_not_awaited()

    async def test_only_players_who_agreed_and_who_have_a_sheet_and_the_plan_allows_it(
        self,
    ) -> None:
        table = self.table()
        gone = CharacterSheet("e2", "Rhea", 77, None, base(), 1)  # withdrew consent
        none = CharacterSheet("e3", "Ghost", PLAYER + 1, None, None, None)  # no sheet yet
        nobody = CharacterSheet("e4", "NPC", None, None, base(), 1)
        bot = self.bot(table, sheets_=[gone, none, nobody])
        await fightbot.tick(bot, table)
        bot.fight_ai.complete.assert_not_awaited()
        refused = self.bot(table, plan=False)
        await fightbot.tick(refused, table)
        refused.fight_ai.complete.assert_not_awaited()

    async def test_without_a_way_to_leave_secrets_out_nothing_is_sent(self) -> None:
        table = self.table()
        bot = self.bot(table)
        bot.lookup.get = AsyncMock(side_effect=RuntimeError("down"))
        await fightbot.tick(bot, table)
        bot.fight_ai.complete.assert_not_awaited()

    async def test_an_ai_failure_is_quiet_and_not_retried_at_once(self) -> None:
        table = self.table()
        bot = self.bot(table)
        bot.fight_ai.complete = AsyncMock(side_effect=AIError("down"))
        await fightbot.tick(bot, table)
        await fightbot.tick(bot, table)
        self.assertEqual(bot.fight_ai.complete.await_count, 1)
        bot.user.send.assert_not_awaited()

    async def test_consent_withdrawn_or_a_session_ended_during_the_call_sends_nothing(self) -> None:
        for what in ("consent", "ended", "paused"):
            table = self.table()
            bot = self.bot(table)

            async def during(
                system: str,
                text: str,
                *,
                max_tokens: int,
                _w: str = what,
                _b: Any = bot,
                _t: Any = table,
            ) -> Reply:
                if _w == "consent":
                    _b.consent = SimpleNamespace(has_consent=lambda g, u: False)
                elif _w == "ended":
                    _b.tables.clear()
                else:
                    _t.listening = False
                return Reply("You still have Hunter's Mark.", False)

            bot.fight_ai.complete = during
            await fightbot.tick(bot, table)
            bot.user.send.assert_not_awaited()

    async def test_a_reply_that_names_nothing_on_the_sheet_is_not_sent(self) -> None:
        table = self.table()
        bot = self.bot(table, reply="Tell Bob to flee from the dragon.")
        await fightbot.tick(bot, table)
        bot.fight_ai.complete.assert_awaited_once()
        bot.user.send.assert_not_awaited()

    async def test_only_the_first_reminder_says_what_fightbot_is(self) -> None:
        table = self.table()
        bot = self.bot(table)
        await fightbot.tick(bot, table)
        bot.clocks.get = AsyncMock(return_value=SimpleNamespace(clock=Clock(0, 0, None, 2, 0)))
        self.now += 130
        await fightbot.tick(bot, table)
        first, second = (c.args[0] for c in bot.user.send.await_args_list)
        self.assertIn("FightBot reminds you", first)
        self.assertNotIn("FightBot reminds you", second)

    async def test_a_player_with_closed_private_messages_is_only_logged(self) -> None:
        table = self.table()
        bot = self.bot(table)
        bot.user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "closed"))
        await fightbot.tick(bot, table)  # no exception


class Buttons(unittest.IsolatedAsyncioTestCase):
    def interaction(self, table: Any, user: int = PLAYER) -> Any:
        bot = SimpleNamespace(
            tables={GUILD: table} if table is not None else {},
            sheets=SimpleNamespace(set_fightbot=AsyncMock(return_value=1)),
        )
        return SimpleNamespace(
            client=bot,
            user=SimpleNamespace(id=user),
            message=SimpleNamespace(content="⚔️ You still have Hunter's Mark."),
            response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
        )

    def table(self) -> Any:
        return SimpleNamespace(campaign_id=CAMPAIGN, fight=fightbot.FightState())

    async def test_each_silence_changes_only_the_presser(self) -> None:
        table = self.table()
        with patch("time.monotonic", return_value=50.0):
            it = self.interaction(table)
            await fightbot.FightButton(GUILD, CAMPAIGN, "snooze").callback(it)
        self.assertEqual(table.fight.snoozed_until, {PLAYER: 50.0 + fightbot.SNOOZE_S})
        it = self.interaction(table, user=9)
        await fightbot.FightButton(GUILD, CAMPAIGN, "quiet").callback(it)
        self.assertEqual(table.fight.quiet, {9})
        edited = it.response.edit_message.await_args.kwargs
        self.assertIn("Hunter's Mark", edited["content"])  # the reminder stays, the buttons go
        self.assertIsNone(edited["view"])
        it = self.interaction(table)
        await fightbot.FightButton(GUILD, CAMPAIGN, "never").callback(it)
        it.client.sheets.set_fightbot.assert_awaited_once_with(GUILD, CAMPAIGN, PLAYER, False)
        self.assertIn("FightBot is off", it.response.edit_message.await_args.kwargs["content"])
        it = self.interaction(table)
        it.client.sheets.set_fightbot = AsyncMock(return_value=0)  # no sheet row: not saved
        await fightbot.FightButton(GUILD, CAMPAIGN, "never").callback(it)
        self.assertIn("didn't save", it.response.edit_message.await_args.kwargs["content"])

    async def test_another_campaigns_session_is_not_touched(self) -> None:
        table = self.table()
        it = self.interaction(table)
        await fightbot.FightButton(GUILD, "b" * 32, "quiet").callback(it)
        self.assertEqual(table.fight.quiet, set())

    async def test_no_session_is_fine(self) -> None:
        it = self.interaction(None)
        await fightbot.FightButton(GUILD, CAMPAIGN, "snooze").callback(it)
        it.response.edit_message.assert_awaited_once()

    def test_the_ids_fit_and_match(self) -> None:
        for action in ("snooze", "quiet", "never"):
            button = fightbot.FightButton(2**63, "f" * 32, action)
            custom_id = str(button.item.custom_id)
            self.assertLessEqual(len(custom_id), 100)
            self.assertIsNotNone(
                fightbot.FightButton.__discord_ui_compiled_template__.fullmatch(custom_id)
            )
            self.assertLessEqual(len(str(button.item.label)), 80)


class Switch(SheetTest):
    async def test_never_is_per_player_per_campaign_and_the_toggle_turns_it_back_on(self) -> None:
        await self.sheets.save(GUILD_A, self.c, self.pc, self.sheet_data, 5)  # a typed sheet
        (found,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertTrue(found.fightbot)
        self.assertEqual(
            await self.sheets.set_fightbot(GUILD_A, self.c, 999, False), 0
        )  # not theirs
        self.assertEqual(await self.sheets.set_fightbot(GUILD_A, self.c, PLAYER, False), 1)
        (found,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertFalse(found.fightbot)
        self.assertEqual(await self.sheets.set_fightbot(GUILD_A, self.c, PLAYER, True), 1)
        (found,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertTrue(found.fightbot)

    async def test_it_does_not_cross_into_another_campaign(self) -> None:
        other = await self.campaigns.create(GUILD_A, "Other", 7)
        self.assertEqual(await self.sheets.set_fightbot(GUILD_A, other.id, PLAYER, False), 0)

    async def test_the_panel_shows_the_switch_for_any_sheet(self) -> None:
        from dmbot.memory.sheet_store import PlayerCharacter
        from dmbot.ui import sheets as ui

        pc = PlayerCharacter(self.c, "C", self.pc, "Testa")

        def labels(view: discord.ui.View) -> list[str]:
            return [str(getattr(i, "label", "")) for i in view.children]

        self.assertNotIn(ui.FIGHTBOT_ON, labels(ui.SheetPanel(GUILD_A, pc, has_sheet=False)))
        on = labels(ui.SheetPanel(GUILD_A, pc, has_sheet=True, fight=True))
        off = labels(ui.SheetPanel(GUILD_A, pc, has_sheet=True, fight=False))
        self.assertIn(ui.FIGHTBOT_ON, on)
        self.assertIn(ui.FIGHTBOT_OFF, off)
