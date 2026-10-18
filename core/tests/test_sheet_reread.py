"""Re-reading linked D&D Beyond sheets (#1128): the phrases that say a character changed and the
ones that mustn't, whose change it is, the 60-second batching, the 15-minute schedule that
pauses in combat, back-off after a refusal, the once-per-change private question that is never
sent when the sheet matches, no reads for sheets that aren't from D&D Beyond, and the player's
switch. The store tests need Postgres."""

from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from dmbot import sheet_reread
from dmbot.memory import sheet_changes as changes
from dmbot.memory import sheet_checks, sheets
from dmbot.memory import sheet_watch as watch
from dmbot.memory.sheet_changes import Heard
from dmbot.memory.sheet_questions import Questions
from dmbot.memory.sheet_store import CharacterSheet, PlayerCharacter
from dmbot.memory.sheet_watch import SheetWatch
from tests.test_memory_store import GUILD_A
from tests.test_sheet_store import CHARACTER, PLAYER, SheetTest
from tests.test_sheets import answer

GUILD, CAMPAIGN = 111, "a" * 32


def base() -> dict[str, Any]:
    snap = sheets.parse(answer())
    snap.update({"hp": 26, "max_hp": 26, "temp_hp": 0, "conditions": []})
    return snap


class Phrases(unittest.TestCase):
    def test_what_plainly_changes_a_character(self) -> None:
        said = {
            "Belleros takes 8 damage": [Heard("damage", 8)],
            "Belleros takes eight points of fire damage": [Heard("damage", 8)],
            "8 damage to Belleros": [Heard("damage", 8)],
            "Belleros is hit for 12": [Heard("damage", 12)],
            "Mira is healed for 5": [Heard("heal", 5)],
            "Mira regains twenty-two hit points": [Heard("heal", 22)],
            "Belleros is poisoned": [Heard("condition", condition="Poisoned")],
            "that uses a level-2 slot": [Heard("slot")],
            "Mira spends a second-level spell slot": [Heard("slot")],
        }
        for line, expect in said.items():
            self.assertEqual(changes.find(line), expect, line)

    def test_questions_wishes_conditions_and_stories_say_nothing(self) -> None:
        for line in (
            "does Belleros take 8 damage?",
            "if Belleros takes 8 damage he is out",
            "Mira will heal 5 when she wakes",
            "Belleros doesn't take 8 damage",
            "you could be poisoned",
            "last week Mira healed 5 hit points and then went on to tell a very long story",
            "he takes no damage",
            "Belleros takes 8000 damage",
        ):
            self.assertEqual(changes.find(line), [], line)

    def test_whose_change_it_is_is_never_guessed(self) -> None:
        def who(line: str, name: str) -> bool:
            found = changes.find(line)
            assert found, line
            return changes.about(line, found[0], name)

        self.assertTrue(who("Belleros takes 8 damage", "Belleros"))
        self.assertTrue(who("Belleros the Ranger takes 8 damage", "Belleros the Ranger"))
        self.assertTrue(who("Belleros Vane takes 8 damage", "Belleros Vane"))
        self.assertTrue(who("8 damage to Belleros", "Belleros"))
        self.assertTrue(who("Mira is poisoned", "Mira"))
        self.assertFalse(who("Belleros hits the goblin, it takes 8 damage", "Belleros"))
        self.assertFalse(who("the goblin hits Mira, she takes 8 damage", "Mira"))
        self.assertFalse(who("the goblin takes 8 damage", "Belleros"))


class Mismatch(unittest.TestCase):
    def test_damage_the_sheet_does_not_show_is_asked_with_the_hp_it_shows(self) -> None:
        was, now = {**base(), "hp": 26}, {**base(), "hp": 26}
        question = changes.mismatch(Heard("damage", 8), was, now, "Belleros")
        assert question is not None
        self.assertIn("Belleros took 8 damage", question)
        self.assertIn("still shows 26 HP", question)
        self.assertIn("You may want to update it.", question)

    def test_a_sheet_that_shows_it_is_never_asked(self) -> None:
        was = {**base(), "hp": 26}
        self.assertIsNone(changes.mismatch(Heard("damage", 8), was, {**base(), "hp": 18}, "B"))
        self.assertIsNone(  # temporary hit points soaked it up: the total still dropped
            changes.mismatch(Heard("damage", 8), {**was, "temp_hp": 10}, {**was, "temp_hp": 2}, "B")
        )
        self.assertIsNone(  # healing: up, or already at the maximum
            changes.mismatch(Heard("heal", 5), {**was, "hp": 10}, {**was, "hp": 15}, "B")
        )
        self.assertIsNone(changes.mismatch(Heard("heal", 5), was, was, "B"))
        poisoned = {**was, "conditions": ["Poisoned"]}
        self.assertIsNone(
            changes.mismatch(Heard("condition", condition="Poisoned"), was, poisoned, "B")
        )

    def test_heal_condition_and_unknowns(self) -> None:
        hurt = {**base(), "hp": 10}
        heal = changes.mismatch(Heard("heal", 5), hurt, hurt, "Mira")
        assert heal is not None
        self.assertIn("Mira healed 5", heal)
        cond = changes.mismatch(Heard("condition", condition="Poisoned"), hurt, hurt, "Mira")
        assert cond is not None
        self.assertIn("poisoned", cond)
        self.assertIsNone(changes.mismatch(Heard("slot"), hurt, hurt, "Mira"))  # not kept
        self.assertIsNone(changes.mismatch(Heard("damage", 8), None, hurt, "Mira"))  # first read

    def test_only_a_part_that_got_worse_sends_the_card_again(self) -> None:
        was = base()
        self.assertFalse(sheet_checks.got_worse(was, {**was, "hp": 3}))  # ordinary HP: silent
        self.assertFalse(sheet_checks.got_worse(None, was))
        worse = {**was, "abilities": {**was["abilities"], "str": 45}, "flags": ["abilities"]}
        self.assertTrue(sheet_checks.got_worse(was, worse))  # abilities were ✅, now ⛔
        self.assertFalse(sheet_checks.got_worse(worse, worse))  # nothing new


class Schedule(unittest.TestCase):
    def fresh(self, n: int = 3) -> SheetWatch:
        w = SheetWatch()
        w.track([f"e{i}" for i in range(n)], 0.0)
        return w

    def test_a_change_is_read_a_minute_later_and_changes_in_that_minute_share_it(self) -> None:
        w = self.fresh()
        self.assertTrue(w.note("e0", [Heard("damage", 8)], 100.0))
        w.note("e0", [Heard("heal", 5)], 130.0)  # inside the minute
        self.assertEqual(w.due(159.0, in_combat=True), [])
        self.assertEqual(w.due(160.0, in_combat=True), ["e0"])  # one read, 60 s after the first
        self.assertEqual(
            [n.heard for n in w.take("e0", 160.0)], [Heard("damage", 8), Heard("heal", 5)]
        )
        self.assertEqual(w.waiting("e0"), 0)
        self.assertEqual(w.due(161.0, in_combat=True), [])

    def test_an_untracked_character_is_not_read(self) -> None:
        self.assertFalse(self.fresh().note("someone-else", [Heard("slot")], 0.0))

    def test_every_sheet_is_read_every_quarter_hour_spread_out(self) -> None:
        w = self.fresh(3)
        times: dict[str, float] = {}
        t = 0.0
        while t < watch.PERIOD_S and len(times) < 3:
            for e in w.due(t, in_combat=False):
                times.setdefault(e, t)
                w.take(e, t)
                w.read_ok(e, t)
            t += 5.0
        self.assertEqual(sorted(times), ["e0", "e1", "e2"])
        self.assertEqual(len({round(v) for v in times.values()}), 3)  # not all at once
        self.assertLess(max(times.values()), watch.PERIOD_S)
        self.assertGreater(min(times.values()), 0)

    def test_nothing_is_read_on_the_schedule_in_combat_but_changes_still_are(self) -> None:
        w = self.fresh()
        self.assertEqual(w.due(watch.PERIOD_S + 1, in_combat=True), [])
        self.assertEqual(len(self.fresh().due(watch.PERIOD_S + 1, in_combat=False)), watch.PER_PASS)
        w.note("e1", [Heard("damage", 3)], 10.0)
        self.assertEqual(w.due(100.0, in_combat=True), ["e1"])

    def test_at_most_one_read_a_minute_for_a_sheet(self) -> None:
        w = self.fresh(1)
        w.note("e0", [Heard("damage", 1)], 0.0)
        w.take("e0", 60.0)
        w.read_ok("e0", 60.0)
        w.note("e0", [Heard("damage", 2)], 61.0)  # due at 121 anyway
        self.assertEqual(w.due(100.0, in_combat=True), [])
        self.assertEqual(w.due(121.0, in_combat=True), ["e0"])
        # A flood of changes in one minute is one read, not one each.
        w2 = self.fresh(1)
        for i in range(50):
            w2.note("e0", [Heard("damage", 1)], float(i))
        self.assertEqual(w2.waiting("e0"), watch.HEARD_MAX)
        self.assertEqual(w2.due(60.0, in_combat=True), ["e0"])

    def test_a_refusal_backs_off_doubling_to_thirty_minutes_and_keeps_the_changes(self) -> None:
        w = self.fresh(1)
        w.note("e0", [Heard("damage", 8)], 0.0)
        now = 60.0
        waits = []
        for _ in range(8):
            heard = w.take("e0", now)
            w.read_failed("e0", now, heard)
            sheet = w._sheets["e0"]
            waits.append(sheet.blocked_until - now)
            self.assertEqual(w.due(now + 1, in_combat=False), [])
            now = sheet.blocked_until
        self.assertEqual(waits[:3], [120.0, 240.0, 480.0])
        self.assertEqual(max(waits), watch.BACKOFF_MAX_S)
        self.assertEqual(w.waiting("e0"), 1)  # still to be checked after the wait
        self.assertEqual(w.due(now, in_combat=True), ["e0"])
        w.take("e0", now)
        w.read_ok("e0", now)
        self.assertEqual(w._sheets["e0"].backoff, 0.0)

    def test_sheets_that_are_gone_are_forgotten_and_campaigns_do_not_share(self) -> None:
        w = self.fresh(2)
        w.track(["e1"], 5.0)
        self.assertFalse(w.note("e0", [Heard("slot")], 6.0))
        other = SheetWatch()  # another session (campaign) has its own
        self.assertFalse(other.note("e1", [Heard("slot")], 6.0))

    def test_the_schedule_does_not_stampede_when_combat_ends(self) -> None:
        w = self.fresh(5)
        late = watch.PERIOD_S * 3  # a long fight: every sheet is overdue
        self.assertEqual(w.due(late, in_combat=True), [])
        due_times = sorted(s.next_periodic for s in w._sheets.values())
        self.assertGreater(due_times[0], late)  # none due the moment combat ends
        self.assertGreater(len({round(t) for t in due_times}), 1)  # and spread out again
        self.assertEqual(w.due(late + 1, in_combat=False), [])

    def test_a_speaker_who_stops_being_recorded_takes_their_changes_with_them(self) -> None:
        w = self.fresh()
        w.note("e0", [Heard("damage", 8)], 0.0, 7)
        w.note("e0", [Heard("heal", 3)], 1.0, 8)
        w.drop_speaker(7)
        self.assertEqual(w.waiting("e0"), 1)
        w.drop_speaker(8)
        self.assertEqual(w.waiting("e0"), 0)
        self.assertEqual(w.due(100.0, in_combat=True), [])  # nothing waits for a read

    def test_a_held_read_loses_nothing_and_is_not_penalised(self) -> None:
        w = self.fresh(1)
        w.note("e0", [Heard("damage", 8)], 0.0, 7)
        taken = w.take("e0", 60.0)
        w.defer("e0", taken)
        self.assertEqual(w.waiting("e0"), 1)
        self.assertEqual(w._sheets["e0"].backoff, 0.0)
        self.assertEqual(w.due(61.0, in_combat=True), ["e0"])  # tried again at once

    def test_a_busy_table_never_starts_more_than_two_reads_a_pass(self) -> None:
        w = self.fresh(8)
        for i in range(8):
            w.note(f"e{i}", [Heard("damage", 1)], 0.0)
        first = w.due(60.0, in_combat=True)
        self.assertEqual(len(first), watch.PER_PASS)
        for e in first:
            w.take(e, 60.0)
        self.assertEqual(len(w.due(61.0, in_combat=True)), watch.PER_PASS)  # the next two

    def test_the_failure_sequence_ends_at_thirty_minutes_and_a_good_read_resets_it(self) -> None:
        w = self.fresh(1)
        now = 0.0
        for _ in range(7):
            w.read_failed("e0", now, [])
            now = w._sheets["e0"].blocked_until
        self.assertEqual(w._sheets["e0"].backoff, watch.BACKOFF_MAX_S)
        w.read_ok("e0", now)
        self.assertEqual(w._sheets["e0"].backoff, 0.0)

    def test_a_five_player_table_reads_about_twenty_times_an_hour_plus_changes(self) -> None:
        w = SheetWatch()
        w.track([f"e{i}" for i in range(5)], 0.0)
        reads = 0
        t = 0.0
        while t < 3600.0:
            for e in w.due(t, in_combat=False):
                w.take(e, t)
                w.read_ok(e, t)
                reads += 1
            t += 20.0
        self.assertLessEqual(reads, 5 * 4)  # one per sheet per quarter hour


def snapshot_sheet(
    entity: str = "e1", *, linked: bool = True, played_by: int | None = PLAYER, **extra: Any
) -> CharacterSheet:
    return CharacterSheet(
        entity,
        "Belleros",
        played_by,
        sheets.sheet_url(CHARACTER) if linked else None,
        {**base(), **extra},
        1,
    )


class Dummy:
    async def __aenter__(self) -> Dummy:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class Runner(unittest.IsolatedAsyncioTestCase):
    def table(self, *found: CharacterSheet, listening: bool = True) -> Any:
        table = SimpleNamespace(
            guild_id=GUILD,
            campaign_id=CAMPAIGN,
            listening=listening,
            sheet_links={},
            sheet_watch=SheetWatch(),
            sheet_questions=Questions(),
        )
        sheet_reread.track(table, list(found))
        return table

    def bot(
        self,
        table: Any,
        *,
        asks: bool = True,
        in_combat: bool = False,
        played_by: int | None = PLAYER,
        withdrawn: frozenset[int] = frozenset(),
    ) -> Any:
        user = SimpleNamespace(send=AsyncMock())
        sent_to: list[int] = []

        def get_user(uid: int) -> Any:
            sent_to.append(uid)
            return user

        async def stored(guild_id: int, campaign_id: str, entity_id: str) -> CharacterSheet:
            current: CharacterSheet = table.sheet_links[entity_id]
            return replace(current, asks=asks, played_by=played_by)

        return SimpleNamespace(
            tables={GUILD: table},
            consent=SimpleNamespace(has_consent=lambda g, u: u not in withdrawn),
            sheets=SimpleNamespace(save=AsyncMock(return_value=True), sheet=stored),
            clocks=SimpleNamespace(in_combat=AsyncMock(return_value=in_combat)),
            campaigns=SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        name="Alpha", target_ruleset="2024", fallback_ruleset="2014"
                    )
                )
            ),
            sheets_changed=MagicMock(),
            get_user=get_user,
            sent_to=sent_to,
            fetch_user=AsyncMock(return_value=user),
            user=user,
        )

    def fetching(self, snapshot: dict[str, Any] | Exception) -> Any:
        if isinstance(snapshot, Exception):
            return patch.object(sheets, "fetch", AsyncMock(side_effect=snapshot))
        return patch.object(sheets, "fetch", AsyncMock(return_value=snapshot))

    async def read(self, bot: Any, table: Any) -> None:
        with patch.object(sheets, "new_session", lambda: Dummy()):
            await sheet_reread.tick(bot, table)

    def say(self, table: Any, line: str, user_id: int = 5) -> None:
        sheet_reread.note_line(table, user_id, line)

    async def test_a_change_heard_is_read_after_a_minute_and_a_mismatch_asks_once(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        self.say(table, "Belleros takes 8 damage")
        table.sheet_watch._sheets["e1"].due_at = 0.0  # a minute has passed
        with self.fetching(base()) as fetched, patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)  # the sheet still shows 26 HP
            await self.read(bot, table)  # nothing more is due: no second question
        fetched.assert_awaited_once()
        bot.user.send.assert_awaited_once()
        text = bot.user.send.await_args.args[0]
        self.assertIn("Belleros took 8 damage", text)
        self.assertIn("still shows 26 HP", text)
        view = bot.user.send.await_args.kwargs["view"]
        (button,) = view.children
        self.assertEqual(button.label, sheet_reread.OPEN_LABEL)
        self.assertEqual(button.url, sheets.sheet_url(CHARACTER))
        self.assertIn("Turn sheet questions off", text)
        bot.sheets.save.assert_awaited_once()

    async def test_a_sheet_that_shows_it_is_never_asked_about(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        self.say(table, "Belleros takes 8 damage")
        table.sheet_watch._sheets["e1"].due_at = 0.0
        with self.fetching({**base(), "hp": 18}), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
        bot.user.send.assert_not_awaited()
        bot.sheets_changed.assert_called()  # the sheet did change; nobody is told

    async def test_a_player_who_turned_the_questions_off_is_not_asked(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table, asks=False)
        self.say(table, "Belleros takes 8 damage")
        table.sheet_watch._sheets["e1"].due_at = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
        bot.user.send.assert_not_awaited()

    async def test_the_schedule_pauses_in_combat_and_changes_still_run(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table, in_combat=True)
        with self.fetching(base()) as fetched, patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)  # a quarter hour has passed: but we are in combat
            fetched.assert_not_awaited()
            self.say(table, "Belleros is poisoned")
            table.sheet_watch._sheets["e1"].due_at = 0.0
            await self.read(bot, table)
            fetched.assert_awaited_once()
        bot2 = self.bot(table, in_combat=False)
        table.sheet_watch._sheets["e1"].next_periodic = 0.0
        table.sheet_watch._sheets["e1"].last_read = -1e9
        with self.fetching(base()) as fetched, patch("time.monotonic", return_value=2e6):
            await self.read(bot2, table)
            fetched.assert_awaited_once()

    async def test_a_refusal_backs_off_says_nothing_and_logs_no_content(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        self.say(table, "Belleros takes 8 damage")
        table.sheet_watch._sheets["e1"].due_at = 0.0
        refused = sheets.SheetError("private", refused=True)
        with (
            self.assertLogs("dmbot.sheet_reread", level="INFO") as logs,
            self.fetching(refused) as fetched,
            patch("time.monotonic", return_value=1e6),
        ):
            await self.read(bot, table)
            await self.read(bot, table)  # waiting now: no second try
        fetched.assert_awaited_once()
        bot.user.send.assert_not_awaited()
        bot.sheets.save.assert_not_awaited()
        joined = " ".join(r.getMessage() for r in logs.records)
        self.assertIn("not public", joined)
        for secret in ("Belleros", "HP", "damage"):
            self.assertNotIn(secret, joined)

    async def test_a_quiet_pass_asks_the_clock_nothing(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        with self.fetching(base()) as fetched, patch("time.monotonic", return_value=10.0):
            await self.read(bot, table)  # nothing is due yet
        fetched.assert_not_awaited()
        bot.clocks.in_combat.assert_not_awaited()

    async def test_an_unchanged_sheet_costs_no_card_look_up(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        table.sheet_watch._sheets["e1"].next_periodic = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
        bot.campaigns.get.assert_not_awaited()

    async def test_a_message_goes_to_whoever_plays_the_character_now(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table, played_by=777)  # the DM reassigned it during the session
        self.say(table, "Belleros takes 8 damage")
        table.sheet_watch._sheets["e1"].due_at = 0.0
        worse = {**base(), "abilities": {**base()["abilities"], "str": 45}, "flags": ["abilities"]}
        with self.fetching(worse), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
        # The messages go to whoever plays it now (777), never to the player it was loaded with.
        self.assertTrue(bot.sent_to)
        self.assertEqual(set(bot.sent_to), {777})

    async def test_a_sheet_unlinked_while_it_was_read_is_dropped_from_the_watch(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        bot.sheets.save = AsyncMock(return_value=False)
        table.sheet_watch._sheets["e1"].next_periodic = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
        self.assertEqual(table.sheet_links, {})
        self.assertEqual(table.sheet_watch.due(2e6, in_combat=False), [])
        bot.user.send.assert_not_awaited()

    async def test_a_session_that_ended_during_the_read_sends_nothing(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        self.say(table, "Belleros takes 8 damage")
        table.sheet_watch._sheets["e1"].due_at = 0.0

        async def ended(*args: Any, **kwargs: Any) -> bool:
            bot.tables.clear()
            return True

        bot.sheets.save = ended
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
        bot.user.send.assert_not_awaited()

    async def test_a_speaker_who_withdrew_makes_no_read_and_no_question(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table, withdrawn=frozenset({5}))
        self.say(table, "Belleros takes 8 damage", user_id=5)
        table.sheet_watch._sheets["e1"].due_at = 0.0
        table.sheet_watch._sheets["e1"].next_periodic = 1e12  # the schedule isn't asking
        with self.fetching(base()) as fetched, patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)  # (the hook drops them too; this is the re-check)
        fetched.assert_not_awaited()
        bot.user.send.assert_not_awaited()

    async def test_a_speaker_who_withdraws_during_the_read_gets_no_question(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        self.say(table, "Belleros takes 8 damage", user_id=5)
        self.say(table, "Belleros is poisoned", user_id=6)
        table.sheet_watch._sheets["e1"].due_at = 0.0

        async def withdraws_meanwhile(*args: Any, **kwargs: Any) -> dict[str, Any]:
            bot.consent = SimpleNamespace(has_consent=lambda g, u: u != 5)
            return base()

        with (
            patch.object(sheets, "fetch", withdraws_meanwhile),
            patch("time.monotonic", return_value=1e6),
        ):
            await self.read(bot, table)
        bot.user.send.assert_awaited_once()  # the other speaker's question still goes
        text = bot.user.send.await_args.args[0]
        self.assertIn("poisoned", text)
        self.assertNotIn("8 damage", text)

    async def test_a_question_held_for_a_speaker_who_later_withdraws_is_not_sent(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table, in_combat=True)
        self.say(table, "Belleros takes 8 damage", user_id=5)
        table.sheet_watch._sheets["e1"].due_at = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)  # held through the fight
            bot.clocks.in_combat = AsyncMock(return_value=False)
            bot.consent = SimpleNamespace(has_consent=lambda g, u: u != 5)  # no hook ran
            await sheet_reread.tick(bot, table)
        bot.user.send.assert_not_awaited()

    async def test_a_question_held_for_a_player_who_later_withdraws_is_not_sent(self) -> None:
        # The sheet's own player, not the speaker, stops being recorded while it is held.
        table = self.table(snapshot_sheet())
        bot = self.bot(table, in_combat=True)
        self.say(table, "Belleros takes 8 damage", user_id=5)
        table.sheet_watch._sheets["e1"].due_at = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
            bot.clocks.in_combat = AsyncMock(return_value=False)
            bot.consent = SimpleNamespace(has_consent=lambda g, u: u != PLAYER)
            await sheet_reread.tick(bot, table)
        bot.user.send.assert_not_awaited()
        self.assertEqual(bot.sent_to, [])  # not even looked up

    async def test_a_speaker_who_withdraws_while_the_sheet_is_loaded_gets_no_question(self) -> None:
        # The re-check after the stored sheet is fetched (an await) in `_ask`.
        table = self.table(snapshot_sheet())
        bot = self.bot(table, in_combat=True)
        self.say(table, "Belleros takes 8 damage", user_id=5)
        table.sheet_watch._sheets["e1"].due_at = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
            bot.clocks.in_combat = AsyncMock(return_value=False)
            stored = bot.sheets.sheet

            async def withdraws_meanwhile(*args: Any) -> Any:
                bot.consent = SimpleNamespace(has_consent=lambda g, u: u != 5)
                return await stored(*args)

            bot.sheets.sheet = withdraws_meanwhile
            # The speaker still agrees at the first check, so the question reaches `_ask`.
            await sheet_reread.tick(bot, table)
        bot.user.send.assert_not_awaited()

    async def test_each_question_carries_its_own_lines_speaker(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table, in_combat=True)
        self.say(table, "Belleros takes 8 damage", user_id=5)
        self.say(table, "Belleros is poisoned", user_id=6)
        table.sheet_watch._sheets["e1"].due_at = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
        speakers = {w.text: w.speaker for w in table.sheet_questions.waiting[PLAYER]}
        self.assertEqual(sorted(speakers.values()), [5, 6])
        table.sheet_questions.drop_speaker(5)  # one withdraws: only theirs goes
        (left,) = table.sheet_questions.waiting[PLAYER]
        self.assertEqual(left.speaker, 6)

    def test_with_no_campaign_the_project_default_rulesets_are_used(self) -> None:
        self.assertEqual(sheet_checks.NO_CAMPAIGN_RULESETS, ("2024", "2014"))

    async def test_the_withdrawal_hook_clears_what_they_said(self) -> None:
        table = self.table(snapshot_sheet())
        self.say(table, "Belleros takes 8 damage", user_id=5)
        table.sheet_watch.drop_speaker(5)
        table.sheet_questions.drop_speaker(5)
        self.assertEqual(table.sheet_watch.waiting("e1"), 0)

    async def test_a_held_back_read_waits_for_every_sheet_and_costs_no_one(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        self.say(table, "Belleros takes 8 damage")
        table.sheet_watch._sheets["e1"].due_at = 0.0
        limited = sheets.SheetError("D&D Beyond answered 429", limited=True, status=429)
        with self.fetching(limited), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
        self.assertEqual(table.sheet_watch.waiting("e1"), 1)  # still to be read
        self.assertEqual(table.sheet_watch._sheets["e1"].backoff, 0.0)  # no penalty
        bot.user.send.assert_not_awaited()

    async def test_a_save_that_fails_keeps_the_changes_for_another_try(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        bot.sheets.save = AsyncMock(side_effect=RuntimeError("db down"))
        self.say(table, "Belleros takes 8 damage")
        table.sheet_watch._sheets["e1"].due_at = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)  # no exception
        self.assertEqual(table.sheet_watch.waiting("e1"), 1)

    async def test_questions_are_one_merged_message_naming_the_campaign(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        self.say(table, "Belleros takes 8 damage")
        self.say(table, "Belleros is poisoned")
        table.sheet_watch._sheets["e1"].due_at = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
        bot.user.send.assert_awaited_once()
        text = bot.user.send.await_args.args[0]
        self.assertIn("in **Alpha**", text)
        self.assertIn("took 8 damage", text)
        self.assertIn("poisoned", text)

    async def test_no_question_in_combat_and_one_after_the_fight(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table, in_combat=True)
        self.say(table, "Belleros takes 8 damage")
        table.sheet_watch._sheets["e1"].due_at = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
            bot.user.send.assert_not_awaited()  # held through the fight
            self.assertTrue(table.sheet_questions.any_waiting())
            bot.clocks.in_combat = AsyncMock(return_value=False)  # the fight is over
            await sheet_reread.tick(bot, table)
        bot.user.send.assert_awaited_once()
        self.assertIn("took 8 damage", bot.user.send.await_args.args[0])

    async def test_at_most_one_message_in_fifteen_minutes_and_never_twice_for_one_value(
        self,
    ) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        with self.fetching(base()):
            for at, line in ((1e6, "Belleros takes 8 damage"), (1e6 + 60, "Belleros is poisoned")):
                with patch("time.monotonic", return_value=at):
                    self.say(table, line)
                    table.sheet_watch._sheets["e1"].due_at = 0.0
                    table.sheet_watch._sheets["e1"].last_read = -1e9
                    await self.read(bot, table)
            self.assertEqual(bot.user.send.await_count, 1)  # the second waits
            with patch("time.monotonic", return_value=1e6 + 16 * 60):
                await sheet_reread.tick(bot, table)
            self.assertEqual(bot.user.send.await_count, 2)
            # The same damage again, with the sheet still showing the same number: not asked.
            with patch("time.monotonic", return_value=1e6 + 40 * 60):
                self.say(table, "Belleros takes 8 damage")
                table.sheet_watch._sheets["e1"].due_at = 0.0
                table.sheet_watch._sheets["e1"].last_read = -1e9
                await self.read(bot, table)
                await sheet_reread.tick(bot, table)
        self.assertEqual(bot.user.send.await_count, 2)

    async def test_a_question_the_player_already_answered_by_updating_is_dropped(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table, in_combat=True)
        self.say(table, "Belleros takes 8 damage")
        table.sheet_watch._sheets["e1"].due_at = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)  # held (combat)
            table.sheet_links["e1"] = replace(
                table.sheet_links["e1"], sheet={**base(), "hp": 18}
            )  # they updated their sheet meanwhile
            bot.clocks.in_combat = AsyncMock(return_value=False)
            await sheet_reread.tick(bot, table)
        bot.user.send.assert_not_awaited()

    async def test_damage_and_healing_that_cancel_out_ask_nothing(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        self.say(table, "Belleros takes 8 damage")
        self.say(table, "Belleros is healed for 8")
        table.sheet_watch._sheets["e1"].due_at = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)
        bot.user.send.assert_not_awaited()

    async def test_a_sheet_not_from_dndbeyond_is_never_read(self) -> None:
        typed = snapshot_sheet(linked=False)
        table = self.table(typed)
        self.assertEqual(table.sheet_links, {})
        bot = self.bot(table)
        self.say(table, "Belleros takes 8 damage")
        with self.fetching(base()) as fetched, patch("time.monotonic", return_value=1e7):
            await self.read(bot, table)
        fetched.assert_not_awaited()

    async def test_unclear_names_start_no_read(self) -> None:
        other = CharacterSheet("e2", "Mira", 6, sheets.sheet_url(1), base(), 1)
        table = self.table(snapshot_sheet(), other)
        self.say(table, "the goblin takes 8 damage")
        self.say(table, "Belleros hits Mira, who takes 8 damage")  # whose? Mira follows "who"
        self.say(table, "someone takes 8 damage")
        self.assertEqual(table.sheet_watch.waiting("e1"), 0)
        self.assertEqual(table.sheet_watch.waiting("e2"), 0)
        self.say(table, "Mira takes 4 damage")
        self.assertEqual(table.sheet_watch.waiting("e2"), 1)
        self.assertEqual(table.sheet_watch.waiting("e1"), 0)

    async def test_i_means_the_speakers_own_character(self) -> None:
        table = self.table(snapshot_sheet())
        self.say(table, "I take 8 damage", user_id=PLAYER)
        self.assertEqual(table.sheet_watch.waiting("e1"), 1)
        self.say(table, "I take 8 damage", user_id=999)  # not their character
        self.assertEqual(table.sheet_watch.waiting("e1"), 1)

    async def test_the_card_comes_again_only_when_a_part_got_worse(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        table.sheet_watch._sheets["e1"].next_periodic = 0.0
        worse = {**base(), "abilities": {**base()["abilities"], "str": 45}, "flags": ["abilities"]}
        with (
            self.fetching(worse),
            patch("time.monotonic", return_value=1e6),
            patch("dmbot.ui.sheets.status_card", AsyncMock(return_value="THE CARD")),
        ):
            await self.read(bot, table)
        bot.user.send.assert_awaited_once()
        self.assertEqual(bot.user.send.await_args.args[0], "THE CARD")

    async def test_a_closed_dm_is_only_logged(self) -> None:
        table = self.table(snapshot_sheet())
        bot = self.bot(table)
        bot.user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "closed"))
        self.say(table, "Belleros takes 8 damage")
        table.sheet_watch._sheets["e1"].due_at = 0.0
        with self.fetching(base()), patch("time.monotonic", return_value=1e6):
            await self.read(bot, table)  # no exception


class Switch(SheetTest):
    async def test_the_questions_are_on_until_the_player_turns_them_off(self) -> None:
        await self.linked()
        (sheet,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertTrue(sheet.asks)
        self.assertFalse(await self.sheets.set_questions(GUILD_A, self.c, self.pc, 999, False))
        self.assertTrue(await self.sheets.set_questions(GUILD_A, self.c, self.pc, PLAYER, False))
        (sheet,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertFalse(sheet.asks)
        url = sheets.sheet_url(CHARACTER)  # a re-read keeps the choice
        await self.sheets.save(GUILD_A, self.c, self.pc, self.sheet_data, 5, url=url)
        (sheet,) = await self.sheets.sheets(GUILD_A, self.c)
        self.assertFalse(sheet.asks)
        self.assertTrue(await self.sheets.set_questions(GUILD_A, self.c, self.pc, PLAYER, True))

    async def test_the_panel_shows_the_switch_only_for_a_dndbeyond_sheet(self) -> None:
        from dmbot.ui import sheets as ui

        pc = PlayerCharacter(self.c, "C", self.pc, "Testa")

        def labels(view: discord.ui.View) -> list[str]:
            return [str(getattr(i, "label", "")) for i in view.children]

        self.assertNotIn(ui.QUESTIONS_ON, labels(ui.SheetPanel(GUILD_A, pc, has_sheet=True)))
        on = labels(ui.SheetPanel(GUILD_A, pc, has_sheet=True, asks=True))
        off = labels(ui.SheetPanel(GUILD_A, pc, has_sheet=True, asks=False))
        self.assertIn(ui.QUESTIONS_ON, on)
        self.assertIn(ui.QUESTIONS_OFF, off)
        for text in on + off:
            self.assertLessEqual(len(text), 80)
