"""A story thread's time limit on the game clock (quests slice 4a; design §8, ruled on #4448): the
DM's one-tap date, "1 day left" and "passed" worked out from the clock (never stored), the
group furthest ahead while split, no clock no count, a moved date a new row, and the pre-session
note's ⏰ line. No Discord; the store's part needs Postgres. Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from dmbot.memory import presession
from dmbot.memory import thread_parts as tp
from dmbot.memory import time_limits as tl
from dmbot.memory.models import DM
from dmbot.timebot.calendar import Calendar
from dmbot.timebot.clock import MINUTES_PER_DAY as DAY
from dmbot.ui import thread_parts as ui
from tests.test_memory_store import GUILD_A, MemoryTest
from tests.test_thread_parts import part


class Counting(unittest.TestCase):
    def test_soon_is_a_day_before_or_halfway_when_set_close(self) -> None:
        self.assertEqual(tl.soon_mark(10 * DAY, 0), 9 * DAY)
        self.assertEqual(tl.soon_mark(DAY, 0), DAY // 2)  # (set a day ahead: halfway)
        self.assertEqual(tl.soon_mark(10 * DAY, None), 9 * DAY)  # (when set isn't known)

    def test_the_stage_from_the_clock(self) -> None:
        due = 10 * DAY
        self.assertIsNone(tl.stage(due, 0, 8 * DAY))
        self.assertEqual(tl.stage(due, 0, 9 * DAY), tl.SOON)
        self.assertEqual(tl.stage(due, 0, due), tl.PASSED)
        self.assertEqual(tl.stage(due, 0, due + 1), tl.PASSED)

    def test_what_the_cards_say(self) -> None:
        days = Calendar()
        due = 3 * DAY  # (the start of Day 4)
        self.assertEqual(tl.left_said(due, 0, days), "3 days left (due Day 4)")
        self.assertEqual(tl.left_said(due, 2 * DAY, days), "1 day left (due Day 4)")
        self.assertEqual(tl.left_said(due, due - 90, days), "2 hours left (due Day 4)")
        self.assertEqual(tl.left_said(due, due, days), "time's up (was due Day 4)")

    def test_days_from_now_and_the_furthest_group(self) -> None:
        self.assertEqual(tl.days_from(100, 3), 100 + 3 * DAY)
        for bad in (0, -1, tl.MOST_DAYS + 1):
            with self.assertRaises(ValueError):
                tl.days_from(0, bad)
        self.assertEqual(tl.furthest([5, 900, 40]), 900)
        self.assertIsNone(tl.furthest([]))


class TheCard(unittest.TestCase):
    def test_a_dated_limit_counts_down_and_one_with_no_clock_says_so(self) -> None:
        limit = part(tp.TIME_LIMIT, words="before the new moon", due_minute=3 * DAY)
        counted = ui.card_lines([limit], {}, None, [], clock=(2 * DAY, Calendar()))
        self.assertEqual(
            counted, ["⏰ **Time limit:** before the new moon · 1 day left (due Day 4)"]
        )
        (no_clock,) = ui.card_lines([limit], {}, None, [])
        self.assertIn(ui.NO_CLOCK, no_clock)
        (words_only,) = ui.card_lines([part(tp.TIME_LIMIT, words="before the thaw")], {}, None, [])
        self.assertEqual(words_only, "⏰ **Time limit:** before the thaw")

    def test_every_new_label_fits_discord(self) -> None:
        for label in (
            ui.SET_DATE,
            ui.CHANGE_DATE,
            ui.CLEAR_DATE,
            ui.PICK_DAY,
            *ui.QUICK_SAID.values(),
        ):
            self.assertLessEqual(len(label), 80, label)
        self.assertEqual(set(ui.QUICK_SAID), set(tl.QUICK_DAYS))


class TheNote(unittest.TestCase):
    def test_the_time_limits_line_nearest_first(self) -> None:
        # (as every line of the note, a thread's words can't open markdown: brackets escaped)
        note = presession.compose(
            presession.Facts(time_limits=["Hlin's job: 1 day left (due Day 4)", "Curse: words"])
        )
        assert note is not None
        (line,) = [x for x in note.lines if x.startswith(presession.LIMITS_PREFIX)]
        self.assertEqual(
            line, "⏰ Time limits: Hlin's job: 1 day left \\(due Day 4\\); Curse: words"
        )


class TheDate(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        kept = await self.memory.keep_thread(
            GUILD_A, self.c, "Hlin's job", "quest", "active", aim="", parts=[], source=DM
        )
        self.quest = kept.value.id
        added = await self.memory.add_thread_part(
            GUILD_A, self.c, self.quest, tp.TIME_LIMIT, "before the new moon", source=DM
        )
        self.limit = added.value.id

    async def parts(self, history: bool = False) -> list[tp.Part]:
        from dmbot.memory.gate import Viewer

        return await self.memory.parts_of_threads(
            GUILD_A, self.c, [self.quest], Viewer.the_dm(), history=history
        )

    async def test_a_first_date_is_filled_in_and_a_moved_one_is_history(self) -> None:
        first = await self.memory.set_time_limit_date(
            GUILD_A, self.c, self.limit, 5 * DAY, now=DAY, source=DM
        )
        self.assertEqual(first.value.id, self.limit)  # (in place: the DM filled in a date)
        self.assertEqual(
            (first.value.due_minute, first.value.due_from, first.value.set_at),
            (5 * DAY, "dm", DAY),
        )
        moved = await self.memory.set_time_limit_date(
            GUILD_A, self.c, self.limit, 7 * DAY, now=2 * DAY, source=DM
        )
        self.assertNotEqual(moved.value.id, self.limit)  # (§4: a deadline moved is a new row)
        self.assertEqual(len(await self.parts(history=True)), 2)
        cleared = await self.memory.set_time_limit_date(
            GUILD_A, self.c, moved.value.id, None, now=None, source=DM
        )
        self.assertEqual((cleared.value.due_minute, cleared.value.due_from), (None, ""))
        assert cleared.batch is not None
        await self.memory.undo(GUILD_A, self.c, cleared.batch)
        (back,) = await self.parts()
        self.assertEqual(back.due_minute, 7 * DAY)

    async def test_only_a_time_limit_has_a_date_and_only_the_dm_sets_it(self) -> None:
        step = await self.memory.add_thread_part(
            GUILD_A, self.c, self.quest, tp.OBJECTIVE, "find Sephek", source=DM
        )
        with self.assertRaises(ValueError):
            await self.memory.set_time_limit_date(
                GUILD_A, self.c, step.value.id, DAY, now=0, source=DM
            )
        from dmbot.memory.models import MemoryRuleError

        with self.assertRaises(MemoryRuleError):
            await self.memory.set_time_limit_date(
                GUILD_A, self.c, self.limit, DAY, now=0, source="background"
            )

    async def test_the_note_counts_on_the_group_furthest_ahead(self) -> None:
        await self.memory.set_time_limit_date(
            GUILD_A, self.c, self.limit, 5 * DAY, now=0, source=DM
        )
        other = await self.memory.keep_thread(
            GUILD_A, self.c, "The curse", "quest", "active", aim="", parts=[], source=DM
        )
        await self.memory.add_thread_part(
            GUILD_A, self.c, other.value.id, tp.TIME_LIMIT, "before the shadow claims", source=DM
        )

        async def get(*_: Any) -> Any:
            group = SimpleNamespace(clock=SimpleNamespace(minute=4 * DAY))
            return SimpleNamespace(clock=SimpleNamespace(minute=DAY), groups=(group,))

        async def calendar(*_: Any) -> Calendar:
            return Calendar()

        clocks = SimpleNamespace(get=get, calendar=calendar)
        note = presession.PreSession(self.memory, None, None, None, clocks)  # type: ignore[arg-type]
        every = await self.memory.threads(GUILD_A, self.c)
        self.assertEqual(
            await note._time_limits(GUILD_A, self.c, every),
            ["Hlin's job: 1 day left (due Day 6)", "The curse: before the shadow claims"],
        )


if __name__ == "__main__":
    unittest.main()
