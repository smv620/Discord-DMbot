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
        original_response=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
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


def button(view: Any, label: str) -> Any:
    """The button with this label (the view's items are not typed as buttons)."""
    return next(b for b in view.children if getattr(b, "label", None) == label)


def labels(view: Any) -> list[str]:
    return [str(getattr(b, "label", "")) for b in view.children]


def can_save(it: Any) -> bool:
    """Whether what was sent has a Save button under it."""
    view = it.response.send_message.await_args.kwargs.get("view")
    return view is not None and "Save" in labels(view)


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
            labels(view),
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
            pressed = button(view, label)
            await pressed.callback(it)
            self.assertIsInstance(it.response.send_modal.await_args.args[0], form)
        it = interaction(calendar=OWN)
        days = button(ui.Choices(CID, OWN), "Days")
        await days.callback(it)
        self.assertIn("Dates will read as Day 1", said(it))  # (Days needs no form)

    async def test_cancel_changes_nothing(self) -> None:
        it = interaction()
        cancel = button(ui.Choices(CID), "Cancel")
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
        self.assertFalse(can_save(it))

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
            self.assertFalse(can_save(it), length)

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
        await button(step, "Set first day").callback(again)
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
            self.assertFalse(can_save(it))


class FilledIn(unittest.IsolatedAsyncioTestCase):
    """Opening "My own" on a campaign that has its own calendar fills the forms in (Expert, 3c)."""

    layout = lay.Layout(
        "Vale",
        lay.four_seasons(90).parts,
        lay.Leap(4, "Extra", "Spring"),
        ("Sunday", "Moonday"),
        "AR",
        "after the Reckoning",
    )
    own = Calendar(
        cal.CUSTOM, layout=layout, layout_start=layout.start_on(1201, "Summer", 2, "Moonday")
    )

    async def test_the_first_form_has_the_calendar_in_its_boxes(self) -> None:
        it = interaction(calendar=self.own)
        view = ui.Choices(CID, self.own)
        await button(view, "My own").callback(it)
        form = it.response.send_modal.await_args.args[0]
        self.assertEqual(
            (form.name.default, form.weekdays.default, form.label.default, form.leap.default),
            ("Vale", "Sunday, Moonday", "AR, after the Reckoning", "Extra, 4, Spring"),
        )
        self.assertEqual(form.parts.default.splitlines()[0], "Spring 90 season")

    async def test_the_second_form_has_where_day_one_is(self) -> None:
        form = ui.OwnForm(CID, self.own)
        typed(
            form,
            name="Vale",
            parts="Spring 90 season\nSummer 90 season",
            weekdays="",
            label="",
            leap="",
        )
        it = interaction(calendar=self.own)
        await form.on_submit(it)
        step = it.response.send_message.await_args.kwargs["view"]
        again = interaction()
        await button(step, "Set first day").callback(again)
        day_one = again.response.send_modal.await_args.args[0]
        self.assertEqual(
            (
                day_one.year.default,
                day_one.part.default,
                day_one.day.default,
                day_one.weekday.default,
            ),
            ("1201", "Summer", "2", "Moonday"),
        )

    async def test_a_campaign_with_no_calendar_of_its_own_has_empty_boxes(self) -> None:
        form = ui.OwnForm(CID, Calendar())
        self.assertTrue(all(not box.default for box in (form.name, form.parts, form.leap)))

    async def test_the_extra_day_box_says_what_to_type_and_the_preview_says_it_in_words(
        self,
    ) -> None:
        form = ui.OwnForm(CID)
        self.assertEqual(form.leap.label, "Extra day: name, years apart, after")
        self.assertEqual(form.leap.placeholder, "Shieldmeet, 4, Midsummer")
        for box in (form.name, form.parts, form.weekdays, form.label, form.leap):
            self.assertLessEqual(len(box.label), 45)  # (Discord's limits on a box)
            self.assertLessEqual(len(box.placeholder), 100)
        text, ok = ui.preview_text(
            Calendar(),
            {
                "kind": "custom",
                "def": self.layout.to_json(),
                "dayOne": {"year": 1201, "part": "Summer", "day": 2},
            },
            None,
        )
        self.assertTrue(ok)
        self.assertIn("Extra comes every 4 years, after Spring.", text)


class TheWarnings(unittest.IsolatedAsyncioTestCase):
    async def test_leaving_a_calendar_of_its_own_says_it_is_removed_before_save(self) -> None:
        it = interaction(calendar=OWN)
        await ui.Choices(CID).days.callback(it)
        text = said(it)
        self.assertIn(cs.REMOVED, text)
        self.assertIn("Save", labels(it.response.send_message.await_args.kwargs["view"]))

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
        save = button(ui.Confirm(CID, self.body), "Save")
        await save.callback(it)
        it.client.clocks.set_calendar.assert_awaited_once_with(
            5, CID, DM, cal.HARPTOS, year=1492, name="Flamerule", day=14
        )
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], ui.SAVED)

    async def test_a_refusal_ends_in_words_and_cancel_saves_nothing(self) -> None:
        it = interaction()
        it.client.clocks.set_calendar.side_effect = CampaignError(NOT_A_DM)
        save = button(ui.Confirm(CID, self.body), "Save")
        await save.callback(it)
        self.assertEqual(it.response.edit_message.await_args.kwargs["content"], ui.NOT_A_DM)
        it = interaction()
        cancel = button(ui.Confirm(CID, self.body), "Cancel")
        await cancel.callback(it)
        it.client.clocks.set_calendar.assert_not_awaited()

    async def test_a_body_nobody_checked_is_never_saved(self) -> None:
        it = interaction()
        save = button(ui.Confirm(CID, {"kind": "gregorian"}), "Save")
        await save.callback(it)
        it.client.clocks.set_calendar.assert_not_awaited()
        self.assertEqual(
            it.response.edit_message.await_args.kwargs["content"], cs.PROBLEMS["layout"]
        )


class WhenSomethingIsWrong(unittest.IsolatedAsyncioTestCase):
    """A refusal does not throw the DM's typing away (ux-critic): Try again reopens the same form
    with what was typed."""

    async def test_a_bad_line_says_which_and_try_again_keeps_every_box(self) -> None:
        form = ui.OwnForm(CID)
        typed(
            form,
            name="Vale",
            parts="Spring 90 season\nnonsense",
            weekdays="Sunday, Moonday",
            label="AR",
            leap="",
        )
        it = interaction()
        await form.on_submit(it)
        self.assertTrue(said(it).startswith("Line 2, “nonsense”"))
        again = it.response.send_message.await_args.kwargs["view"]
        retry = interaction()
        await button(again, "Try again").callback(retry)
        reopened = retry.response.send_modal.await_args.args[0]
        self.assertIsInstance(reopened, ui.OwnForm)
        self.assertEqual(
            (reopened.name.default, reopened.parts.default, reopened.weekdays.default),
            ("Vale", "Spring 90 season\nnonsense", "Sunday, Moonday"),
        )

    async def test_each_form_names_the_box_that_needs_a_number(self) -> None:
        form = ui.HarptosForm(CID)
        typed(form, year="twelve", part="Flamerule", day="14")
        it = interaction()
        await form.on_submit(it)
        self.assertEqual(said(it), cs.PROBLEMS["year_number"])
        seasons = ui.SeasonsForm(CID)
        typed(seasons, length="many", year="1201", part="Summer", day="2")
        it = interaction()
        await seasons.on_submit(it)
        self.assertEqual(said(it), cs.PROBLEMS["length_number"])
        self.assertIn(
            "Try again",
            labels(it.response.send_message.await_args.kwargs["view"]),
        )

    async def test_a_refusal_at_the_preview_also_keeps_what_was_typed(self) -> None:
        form = ui.HarptosForm(CID)
        typed(form, year="1492", part="Flamerule", day="31")
        it = interaction()
        await form.on_submit(it)
        again = it.response.send_message.await_args.kwargs["view"]
        retry = interaction()
        await button(again, "Try again").callback(retry)
        reopened = retry.response.send_modal.await_args.args[0]
        self.assertEqual((reopened.year.default, reopened.day.default), ("1492", "31"))

    async def test_a_step_left_alone_comes_down_with_a_word(self) -> None:
        it = interaction()
        await ui.open_panel(it, CID)
        view = it.response.send_message.await_args.kwargs["view"]
        await view.on_timeout()
        message = it.original_response.return_value
        message.edit.assert_awaited_once_with(content=ui.TIMED_OUT, view=None)


class TheOrderOfTheWords(unittest.TestCase):
    def test_what_takes_something_away_comes_first_and_no_change_says_so(self) -> None:
        text, ok = ui.preview_text(OWN, {"kind": "days"}, None)
        self.assertTrue(ok)
        lines = text.splitlines()
        self.assertEqual(lines[0], f"⚠️ {cs.REMOVED}")
        self.assertIn("Dates will read as Day 1", lines[1])
        self.assertEqual(lines[-2:], [cs.VOICES, cs.COUNTED])
        self.assertEqual(ui.preview_text(Calendar(), {"kind": "days"}, None), (ui.NO_CHANGE, True))
