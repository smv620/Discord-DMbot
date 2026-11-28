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
from dmbot.timebot import calendar as cal
from dmbot.timebot import layout as lay
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

    def test_soon_at_its_edges(self) -> None:
        self.assertEqual(tl.soon_mark(5 * DAY, 5 * DAY), 4 * DAY)  # (set at or after it: a day)
        self.assertEqual(tl.soon_mark(2 * DAY, 0), DAY)  # (exactly two days ahead: a day before)
        self.assertEqual(tl.left_said(DAY, DAY - 1, Calendar()), "1 hour left (due Day 2)")

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


class OwnCalendar(unittest.TestCase):
    """A calendar of its own (#5364 slice 4a): every card, alert and countdown says its dates
    through the one function, so they print in the campaign's calendar like Harptos does."""

    seasons = lay.four_seasons(90, "Vale")
    vale = Calendar(cal.CUSTOM, layout=seasons, layout_start=seasons.start_on(1201, "Summer", 2))

    def test_a_limits_date_and_countdown_are_in_the_calendar(self) -> None:
        due = 3 * DAY
        self.assertEqual(tl.date_said(due, self.vale), "Summer 5th, 1201")
        self.assertEqual(tl.left_said(due, 0, self.vale), "3 days left (due Summer 5th, 1201)")
        self.assertEqual(
            tl.left_said(due, due + 5, self.vale), "time's up (was due Summer 5th, 1201)"
        )

    def test_plain_and_harptos_calendars_say_what_they_always_did(self) -> None:
        self.assertEqual(tl.date_said(3 * DAY, Calendar()), "Day 4")
        flame = Calendar(cal.HARPTOS, cal.day_one(1492, "Flamerule", 14))
        self.assertEqual(tl.date_said(3 * DAY, flame), "Flamerule 17th, 1492 DR")
        self.assertEqual(tl.date_said(-5, flame), "Flamerule 14th, 1492 DR")  # (never before Day 1)

    def test_a_date_in_the_words_is_read_in_the_calendar_of_its_own(self) -> None:
        """#5364 4b: the one reader, with the campaign's own months and seasons."""
        now = 10 * DAY  # (game day 11)
        for words, due_day in (
            (
                "by the 2nd day of Summer",
                361,
            ),  # (Day 1 is Summer 2nd: its next coming is a year on)
            ("before Summer 20", 19),
            ("Summer 2nd, 1202", 361),
            ("by Day 30", 30),
        ):
            got = tl.date_in_words(words, self.vale, now)
            self.assertEqual(got, cal.minute_of(due_day), words)
            self.assertEqual(
                tl.date_said(got or 0, self.vale), self.vale.words(cal.minute_of(due_day))
            )

    def test_words_that_are_no_date_here_stay_words(self) -> None:
        now = 10 * DAY
        for words in (
            "by Flamerule 14",  # (not this calendar's month)
            "Summer 2nd or Autumn 3rd",  # (two dates)
            "Summer 91st",  # (a day the season has not got)
            "before Summer",  # (a bare season is no date)
            "Summer 2nd, 1200",  # (before Day 1)
            "by Day 0",
        ):
            self.assertIsNone(tl.date_in_words(words, self.vale, now), words)

    def test_day_n_is_read_in_every_calendar(self) -> None:
        flame = Calendar(cal.HARPTOS, cal.day_one(1492, "Flamerule", 14))
        for calendar in (Calendar(), flame, self.vale):
            self.assertEqual(
                tl.date_in_words("by Day 12", calendar, 5 * DAY), cal.minute_of(12), calendar.kind
            )

    def test_the_websites_clock_door_makes_the_same_calendar(self) -> None:
        stored = {"kind": "custom", "year": 1201, "ordinal": 91, "def": cal.def_of(self.vale)}
        self.assertEqual(cal.from_door(stored), self.vale)
        self.assertEqual(
            cal.from_door({"kind": "harptos", "year": 1492, "ordinal": 3}),
            Calendar(cal.HARPTOS, cal.DayOne(1492, 3)),
        )
        for bad in (
            {}, {"kind": "days"}, {"kind": "harptos", "year": 1493, "ordinal": 365},
            {"kind": "custom", "year": 1201, "ordinal": 91, "def": None},
            {"kind": "custom", "year": 1201, "ordinal": 91, "def": {"layout": {}, "weekday": 0}},
            {"kind": "custom", "year": 1201, "ordinal": 999, "def": cal.def_of(self.vale)},
            {"kind": "custom", "year": True, "ordinal": 91, "def": cal.def_of(self.vale)},
        ):  # fmt: skip
            self.assertEqual(cal.from_door(bad), Calendar(), str(bad)[:50])


class ReadingADate(unittest.TestCase):
    """Slice 4c (§8.2): a date the words name is read by code on the campaign's calendar, never by
    the AI, and only offered: the DM taps to count down to it (Supervisor's choice B)."""

    harptos = cal.Calendar(cal.HARPTOS, cal.day_one(1492, "Flamerule", 1))

    def said(self, words: str, now: int = 0) -> str | None:
        due = tl.date_in_words(words, self.harptos, now)
        return None if due is None else tl.date_said(due, self.harptos)

    def test_a_harptos_date_in_its_several_spellings(self) -> None:
        for words in ("by the 14th of Flamerule", "before Flamerule 14", "Flamerule 14th, 1492 DR"):
            self.assertEqual(self.said(words), "Flamerule 14th, 1492 DR", words)
        self.assertEqual(self.said("before Midsummer"), "Midsummer, 1492 DR")
        self.assertEqual(self.said("by the Feast of the Moon"), "Feast of the Moon, 1492 DR")

    def test_a_date_with_no_year_is_its_next_coming(self) -> None:
        self.assertEqual(self.said("by Hammer 3"), "Hammer 3rd, 1493 DR")
        today = (cal.day_number_of(1492, "Flamerule", 20, self.harptos.start) - 1) * DAY  # type: ignore[arg-type]
        self.assertEqual(self.said("by Flamerule 14", today), "Flamerule 14th, 1493 DR")
        # (today is no offer: its countdown would be over at once; its next coming is)
        self.assertEqual(self.said("by Flamerule 20", today), "Flamerule 20th, 1493 DR")
        self.assertEqual(self.said("by Flamerule 20", today + 10 * 60), "Flamerule 20th, 1493 DR")
        self.assertEqual(self.said("by Flamerule 21", today + 10 * 60), "Flamerule 21st, 1492 DR")

    def test_shieldmeet_comes_once_in_four_years(self) -> None:
        start = self.harptos.start
        assert start is not None
        after = (cal.day_number_of(1492, "Eleasis", 1, start) - 1) * DAY
        self.assertEqual(self.said("before Shieldmeet"), "Shieldmeet, 1492 DR")
        self.assertEqual(self.said("before Shieldmeet", after), "Shieldmeet, 1496 DR")
        self.assertIsNone(self.said("Shieldmeet, 1493 DR"))  # (no Shieldmeet that year)

    def test_a_year_rolls_over_from_a_late_day_one(self) -> None:
        late = cal.Calendar(cal.HARPTOS, cal.day_one(1492, "Nightal", 30))
        due = tl.date_in_words("by Hammer 1", late, 0)
        assert due is not None
        self.assertEqual(tl.date_said(due, late), "Hammer 1st, 1493 DR")

    def test_a_number_is_a_year_only_when_it_reads_as_one(self) -> None:
        self.assertEqual(self.said("by Midsummer, 1500 gold"), "Midsummer, 1492 DR")
        self.assertEqual(self.said("Flamerule 14, 3 guards"), "Flamerule 14th, 1492 DR")
        self.assertEqual(self.said("Flamerule 14, 1493."), "Flamerule 14th, 1493 DR")

    def test_nothing_further_ahead_than_a_typed_number_may_be(self) -> None:
        self.assertIsNone(self.said("Flamerule 14th, 1510 DR"))
        self.assertIsNone(tl.date_in_words("by Day 99999", cal.Calendar(), 0))

    def test_words_only_when_it_cant_tell(self) -> None:
        for words in (
            "before the new moon", "before the thaw", "in three days",
            "Flamerule 14 or Flamerule 20",  # (two dates: DMbot can't tell which)
            "Flamerule 14 or Midsummer", "by Midsummer or the 14th of Flamerule",
            "the 1st of Midsummer",  # (a festival has no days)
            "Flamerule 14th, 1491 DR",  # (before Day 1)
            "by Flamerule 31",  # (a day the month hasn't got)
        ):  # fmt: skip
            self.assertIsNone(self.said(words), words)

    def test_with_no_calendar_only_day_n(self) -> None:
        days = cal.Calendar()
        self.assertEqual(tl.date_in_words("by Day 12", days, 5 * DAY), cal.minute_of(12))
        for words in ("Flamerule 14", "day 3", "Day 3 or day 4", "day 0"):
            self.assertIsNone(tl.date_in_words(words, days, 5 * DAY), words)

    def test_the_offer_on_the_card_and_its_button(self) -> None:
        limit = part(tp.TIME_LIMIT, words="by the 14th of Flamerule")
        (line,) = ui.card_lines([limit], {}, None, [], clock=(0, self.harptos))
        self.assertIn("DMbot reads this as Flamerule 14th, 1492 DR, not counting yet", line)
        self.assertLessEqual(len(ui.COUNT_DOWN), 25)  # (fits a phone, #112)
        self.assertIn(ui.COUNT_DOWN, ui.READS_AS)
        for done in (
            part(tp.TIME_LIMIT, words="by the 14th of Flamerule", due_minute=DAY),
            part(tp.TIME_LIMIT, words="by the 14th of Flamerule", state="met"),
        ):
            for line in ui.card_lines([done], {}, None, [], clock=(0, self.harptos)):
                self.assertNotIn("reads this as", line)


class TheOffer(unittest.IsolatedAsyncioTestCase):
    """`read_offer`: only a running, undated limit whose words name a date, and a fault in the
    clock is no offer, never a broken menu."""

    harptos = cal.Calendar(cal.HARPTOS, cal.day_one(1492, "Flamerule", 1))

    def client(self, broken: bool = False) -> Any:
        async def get(_g: int, _c: str) -> Any:
            if broken:
                raise RuntimeError("clock")
            return SimpleNamespace(clock=SimpleNamespace(minute=0), groups=())

        async def calendar(_g: int, _c: str) -> Any:
            return self.harptos

        return SimpleNamespace(clocks=SimpleNamespace(get=get, calendar=calendar))

    async def test_offered_only_where_it_can_be(self) -> None:
        limit = part(tp.TIME_LIMIT, words="by the 14th of Flamerule")
        offer = await ui.read_offer(self.client(), GUILD_A, "c1", limit)
        assert offer is not None
        due, said, now, left = offer
        self.assertEqual(due, cal.minute_of(14))
        self.assertEqual((said, now), ("Flamerule 14th, 1492 DR", 0))
        self.assertEqual(left, "13 days left (due Flamerule 14th, 1492 DR)")
        for no in (
            part(tp.TIME_LIMIT, words="by the 14th of Flamerule", due_minute=DAY),
            part(tp.TIME_LIMIT, words="by the 14th of Flamerule", state="met"),
            part(tp.TIME_LIMIT, words="before the thaw"),
            part(tp.OBJECTIVE, words="by the 14th of Flamerule"),
        ):
            self.assertIsNone(await ui.read_offer(self.client(), GUILD_A, "c1", no))
        with self.assertLogs("dmbot.memory.time_limits", "WARNING"):
            self.assertIsNone(await ui.read_offer(self.client(True), GUILD_A, "c1", limit))


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
        same = await self.memory.set_time_limit_date(
            GUILD_A, self.c, moved.value.id, 7 * DAY, now=3 * DAY, source=DM
        )
        self.assertEqual(same.value.id, moved.value.id)  # (the same date again: nothing written)
        cleared = await self.memory.set_time_limit_date(
            GUILD_A, self.c, moved.value.id, None, now=None, source=DM
        )
        self.assertEqual((cleared.value.due_minute, cleared.value.due_from), (None, ""))
        # (clearing a date is a story change too, so a later date is told afresh: history)
        self.assertEqual(len(await self.parts(history=True)), 3)
        assert cleared.batch is not None and moved.batch is not None
        await self.memory.undo(GUILD_A, self.c, cleared.batch)
        (back,) = await self.parts()
        self.assertEqual((back.due_minute, back.set_at), (7 * DAY, 2 * DAY))
        await self.memory.undo(GUILD_A, self.c, moved.batch)  # (the move undone: the first date)
        (first_again,) = await self.parts()
        self.assertEqual((first_again.id, first_again.due_minute), (self.limit, 5 * DAY))

    async def test_the_dms_tap_on_a_read_date_keeps_it_as_dmbots_read(self) -> None:
        """(4c: `due_from` read, so the card can say where the date came from; one Undo)"""
        kept = await self.memory.set_time_limit_date(
            GUILD_A, self.c, self.limit, 5 * DAY, now=0, source=DM, given_by=tp.READ
        )
        self.assertEqual((kept.value.due_minute, kept.value.due_from), (5 * DAY, tp.READ))
        with self.assertRaises(ValueError):  # (only the DM's word or DMbot's read)
            await self.memory.set_time_limit_date(
                GUILD_A, self.c, self.limit, 6 * DAY, now=0, source=DM, given_by="lore"
            )

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

        async def broken(*_: Any) -> Any:
            raise RuntimeError("the clock can't be read")

        every = await self.memory.threads(GUILD_A, self.c)
        for clocks, want in (
            (
                SimpleNamespace(get=get, calendar=calendar),
                ["Hlin's job: 1 day left (due Day 6)", "The curse: before the shadow claims"],
            ),
            # (no clock, or one that can't be read: every limit by its words, never no line)
            (None, ["Hlin's job: before the new moon", "The curse: before the shadow claims"]),
            (
                SimpleNamespace(get=broken, calendar=calendar),
                ["Hlin's job: before the new moon", "The curse: before the shadow claims"],
            ),
        ):
            note = presession.PreSession(self.memory, None, None, None, clocks)  # type: ignore[arg-type]
            self.assertEqual(await note._time_limits(GUILD_A, self.c, every), want)


if __name__ == "__main__":
    unittest.main()
