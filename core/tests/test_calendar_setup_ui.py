"""The DM's Game calendar in Discord (#5364 slice 3c): the panel, its forms, the preview with its
warnings, and Save or Cancel. Stand-ins for Discord's objects; the calendar, the preview and the
save are the real ones. Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

from dmbot.campaigns.models import CampaignError
from dmbot.dm_screen import calendar_setup as ui
from dmbot.timebot import calendar as cal
from dmbot.timebot import calendar_setting as cs
from dmbot.timebot import layout as lay
from dmbot.timebot.calendar import Calendar
from dmbot.timebot.clock import Clock
from dmbot.timebot.store import NOT_A_DM, Stored

CID = "c" * 32
DM, PLAYER = 11, 22
SEASONS = lay.four_seasons(90)
OWN = Calendar(cal.CUSTOM, layout=SEASONS, layout_start=SEASONS.start_on(1201, "Summer", 2))


def interaction(user: int = DM, *, calendar: Calendar | None = None, clock: bool = True) -> Any:
    stored = Stored(Clock(10 * 1440, 0), None, None, calendar=calendar or Calendar())
    campaign = SimpleNamespace(id=CID, dm_user_ids=[DM])
    return SimpleNamespace(
        user=SimpleNamespace(id=user),
        guild=SimpleNamespace(id=5),
        response=SimpleNamespace(
            send_message=AsyncMock(), send_modal=AsyncMock(), edit_message=AsyncMock()
        ),
        client=SimpleNamespace(
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            clocks=SimpleNamespace(
                get=AsyncMock(return_value=stored if clock else None),
                set_calendar=AsyncMock(),
                set_custom_calendar=AsyncMock(),
            ),
        ),
    )


def said(it: Any) -> str:
    return str(it.response.send_message.await_args.args[0])


def typed(form: Any, **values: str) -> None:
    for name, value in values.items():
        getattr(form, name)._value = value


class ThePanel(unittest.IsolatedAsyncioTestCase):
    async def test_a_dm_sees_where_the_calendar_stands_and_the_choices(self) -> None:
        it = interaction(calendar=OWN)
        await ui.open_panel(it, CID)
        text = said(it)
        self.assertIn("Now: **Four seasons**. Day 1 is Summer 2nd, 1201.", text)
        view = it.response.send_message.await_args.kwargs["view"]
        self.assertEqual(
            [b.label for b in view.children],
            ["Days", "Harptos", "Four seasons", "My own", "Cancel"],
        )
        self.assertTrue(it.response.send_message.await_args.kwargs["ephemeral"])

    async def test_days_is_said_plainly_and_a_player_is_turned_away(self) -> None:
        it = interaction()
        await ui.open_panel(it, CID)
        self.assertIn("Now: Day 1, Day 2, and so on.", said(it))
        it = interaction(PLAYER)
        await ui.open_panel(it, CID)
        self.assertEqual(said(it), ui.NOT_A_DM)

    async def test_with_no_game_clock_it_says_to_start_it(self) -> None:
        it = interaction(clock=False)
        await ui.open_panel(it, CID)
        self.assertEqual(said(it), ui.NO_CLOCK)

    async def test_the_buttons_open_the_forms(self) -> None:
        view = ui.Choices(CID)
        for label, form in (("Harptos", ui.HarptosForm), ("Four seasons", ui.SeasonsForm),
                            ("My own", ui.OwnForm)):  # fmt: skip
            it = interaction()
            button = next(b for b in view.children if b.label == label)
            await button.callback(it)
            self.assertIsInstance(it.response.send_modal.await_args.args[0], form)
        it = interaction()
        days = next(b for b in view.children if b.label == "Days")
        await days.callback(it)
        self.assertIn("Dates will read as Day 1", said(it))  # (Days needs no form)

    async def test_cancel_changes_nothing(self) -> None:
        it = interaction()
        cancel = next(b for b in ui.Choices(CID).children if b.label == "Cancel")
        await cancel.callback(it)
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], ui.CANCELLED)
        it.client.clocks.set_calendar.assert_not_awaited()


class TheForms(unittest.IsolatedAsyncioTestCase):
    async def test_harptos(self) -> None:
        form = ui.HarptosForm(CID)
        typed(form, year="1492", part="Flamerule", day="14")
        it = interaction()
        await form.on_submit(it)
        text = said(it)
        self.assertIn("Day 1 is Flamerule 14th, 1492 DR", text)
        self.assertIn("Today is Flamerule 24th, 1492 DR", text)  # (the clock is on day 11)
        self.assertIn(cs.VOICES, text)
        self.assertNotIn(cs.REMOVED, text)

    async def test_a_bad_harptos_day_says_why_and_offers_no_save(self) -> None:
        form = ui.HarptosForm(CID)
        typed(form, year="1492", part="Flamerule", day="31")
        it = interaction()
        await form.on_submit(it)
        self.assertEqual(said(it), cs.PROBLEMS["day"])
        self.assertNotIn("view", it.response.send_message.await_args.kwargs)

    async def test_four_seasons(self) -> None:
        form = ui.SeasonsForm(CID)
        typed(form, length="90", year="1201", part="Summer", day="2")
        it = interaction()
        await form.on_submit(it)
        self.assertIn("Day 1 is Summer 2nd, 1201", said(it))
        for length in ("ninety", "0", "5000"):
            form = ui.SeasonsForm(CID)
            typed(form, length=length, year="1201", part="Summer", day="2")
            it = interaction()
            await form.on_submit(it)
            self.assertNotIn("view", it.response.send_message.await_args.kwargs, length)

    async def test_my_own_asks_where_day_one_is_and_then_previews(self) -> None:
        form = ui.OwnForm(CID)
        typed(
            form,
            name="Vale",
            parts="Spring 90 season\nSummer 90 season",
            weekdays="Sunday, Moonday",
            label="AR, after the Reckoning",
            leap="Extra, 4, Spring",
        )
        it = interaction()
        await form.on_submit(it)
        self.assertEqual(said(it), ui.OWN_NEXT)
        step = it.response.send_message.await_args.kwargs["view"]
        again = interaction()
        await next(b for b in step.children if b.label == "Where is Day 1?").callback(again)
        day_one = again.response.send_modal.await_args.args[0]
        typed(day_one, year="1201", part="Summer", day="2", weekday="Moonday")
        last = interaction()
        await day_one.on_submit(last)
        self.assertIn("Day 1 is Summer 2nd, 1201 AR", said(last))

    async def test_a_calendar_that_cannot_be_is_refused_before_day_one_is_asked(self) -> None:
        for parts in ("Spring", "Spring 90 season\nSpring 5 season", "Spring 90 season\nSummer 5"):
            form = ui.OwnForm(CID)
            typed(form, name="Vale", parts=parts, weekdays="", label="", leap="")
            it = interaction()
            await form.on_submit(it)
            self.assertNotEqual(said(it), ui.OWN_NEXT, parts)
            self.assertNotIn("view", it.response.send_message.await_args.kwargs)


class TheWarnings(unittest.IsolatedAsyncioTestCase):
    async def test_leaving_a_calendar_of_its_own_says_it_is_removed_before_save(self) -> None:
        it = interaction(calendar=OWN)
        await ui.Choices(CID).days.callback(it)
        text = said(it)
        self.assertIn(cs.REMOVED, text)
        self.assertIn(
            "Save", [b.label for b in it.response.send_message.await_args.kwargs["view"].children]
        )

    async def test_saving_over_one_says_it_replaces_it(self) -> None:
        form = ui.SeasonsForm(CID)
        typed(form, length="80", year="1201", part="Summer", day="2")
        it = interaction(calendar=OWN)
        await form.on_submit(it)
        self.assertIn(cs.REPLACES, said(it))


class SavingOrNot(unittest.IsolatedAsyncioTestCase):
    body: ClassVar[dict[str, Any]] = {
        "kind": "harptos",
        "dayOne": {"year": 1492, "part": "Flamerule", "day": 14},
    }

    async def test_save_makes_the_same_store_call_as_the_website(self) -> None:
        it = interaction()
        save = next(b for b in ui.Confirm(CID, self.body).children if b.label == "Save")
        await save.callback(it)
        it.client.clocks.set_calendar.assert_awaited_once_with(
            5, CID, DM, cal.HARPTOS, year=1492, name="Flamerule", day=14
        )
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], ui.SAVED)

    async def test_a_refusal_ends_in_words_and_cancel_saves_nothing(self) -> None:
        it = interaction()
        it.client.clocks.set_calendar.side_effect = CampaignError(NOT_A_DM)
        save = next(b for b in ui.Confirm(CID, self.body).children if b.label == "Save")
        await save.callback(it)
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], ui.NOT_A_DM)
        it = interaction()
        cancel = next(b for b in ui.Confirm(CID, self.body).children if b.label == "Cancel")
        await cancel.callback(it)
        it.client.clocks.set_calendar.assert_not_awaited()

    async def test_a_body_nobody_checked_is_never_saved(self) -> None:
        it = interaction()
        save = next(b for b in ui.Confirm(CID, {"kind": "gregorian"}).children if b.label == "Save")
        await save.callback(it)
        it.client.clocks.set_calendar.assert_not_awaited()
        self.assertEqual(
            it.response.edit_message.await_args.kwargs["content"], cs.PROBLEMS["layout"]
        )
