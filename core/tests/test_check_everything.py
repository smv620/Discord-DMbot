"""The "✅ Check everything" walk-through (#4192, first slice): its order and words (`walk.py`),
the one button on the after-session message (`dm_questions.combine`), the button's id, and the way
from one part to the next. Needs no database and no Discord."""

from __future__ import annotations

import unittest
from functools import partial
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import discord

from dmbot import walk as walks
from dmbot.dm_questions import (
    CLAIMS,
    CONNECTIONS,
    FAMILY,
    NAMES,
    PEOPLE,
    WALK_PRESS,
    Ask,
    combine,
)
from dmbot.ui import check_everything as ce
from dmbot.walk import Walk

CID = "a" * 32
GUILD = 4242


def ask(kind: str, count: int = 3, *, link: bool = False) -> Ask:
    def button() -> discord.ui.Item[Any]:
        return discord.ui.Button(label=kind, custom_id=f"test:{kind}")

    return Ask(kind, f"{kind} alone", f"{kind} line", button, button, f"{kind} screen", count, link)


def ids(view: discord.ui.View) -> list[str | None]:
    return [getattr(child, "custom_id", None) for child in view.children]


def labels(view: discord.ui.View) -> list[str | None]:
    # (a dynamic item holds its button in `.item`)
    return [getattr(getattr(child, "item", child), "label", None) for child in view.children]


class TheWalksOrderAndWords(unittest.TestCase):
    def test_the_parts_run_names_then_who_is_who_then_what_was_said_then_connections(self) -> None:
        self.assertEqual(walks.PARTS, (NAMES, PEOPLE, CLAIMS, CONNECTIONS))
        counts = {CONNECTIONS: 2, NAMES: 5, CLAIMS: 1, PEOPLE: 0}
        self.assertEqual(walks.parts_for(counts), (NAMES, CLAIMS, CONNECTIONS))

    def test_nothing_waiting_is_no_walk(self) -> None:
        self.assertEqual(walks.parts_for({}), ())

    def test_the_progress_line_says_which_part_and_how_far(self) -> None:
        walk = Walk(CID, GUILD, 1, (NAMES, CLAIMS, CONNECTIONS))
        self.assertEqual(walk.progress(4, 11), "Names, part 1 of 3 · 4 of 11")
        self.assertEqual(walk.next().progress(1, 9), "What was said, part 2 of 3 · 1 of 9")
        self.assertEqual(walk.progress(0, 0), "Names, part 1 of 3")

    def test_the_last_part_knows_it_is_last(self) -> None:
        walk = Walk(CID, GUILD, 1, (NAMES, CLAIMS))
        self.assertFalse(walk.last)
        self.assertTrue(walk.next().last)

    def test_the_line_between_two_parts(self) -> None:
        self.assertEqual(walks.between(NAMES, CLAIMS, 11), "Names done. Next: what was said (11).")

    def test_the_message_button_reads_check_everything_then_carry_on(self) -> None:
        self.assertEqual(walks.button_label(7, started=False), "✅ Check everything (7)")
        self.assertEqual(walks.button_label(4, started=True), "✅ Carry on (4 left)")

    def test_the_card_line_is_empty_outside_a_walk(self) -> None:
        self.assertEqual(ce.progress(None, 1, 2), "")
        walk = Walk(CID, GUILD, 1, (NAMES,))
        self.assertEqual(ce.progress(walk, 1, 2), "_Names, part 1 of 1 · 1 of 2_\n")


class TheButtonOnTheMessage(unittest.TestCase):
    def walk_button(self, total: int) -> discord.ui.Item[Any]:
        return ce.WalkButton(GUILD, CID, total)

    def test_two_or_more_kinds_become_one_check_everything_button(self) -> None:
        combined = combine(
            [ask(NAMES, 3), ask(CLAIMS, 8), ask(CONNECTIONS, 29)], walk=self.walk_button
        )
        assert combined is not None
        text, view = combined
        self.assertEqual(ids(view), [f"dmbot:walk:{GUILD}:{CID}"])
        self.assertEqual(labels(view), ["✅ Check everything (40)"])
        self.assertTrue(text.endswith(WALK_PRESS))
        self.assertIn("• names line", text)  # (the count lines keep their words)

    def test_one_kind_keeps_its_own_button_and_words(self) -> None:
        combined = combine([ask(CLAIMS, 8)], walk=self.walk_button)
        assert combined is not None
        text, view = combined
        self.assertEqual(text, "claims alone")
        self.assertEqual(ids(view), ["test:claims"])

    def test_one_walked_kind_among_others_keeps_every_button(self) -> None:
        combined = combine([ask(CLAIMS, 8), ask(FAMILY, 2)], walk=self.walk_button)
        assert combined is not None
        text, view = combined
        self.assertEqual(ids(view), ["test:claims", "test:family"])
        self.assertNotIn("Check everything", text)

    def test_other_kinds_keep_their_own_button_beside_the_walk(self) -> None:
        combined = combine([ask(NAMES, 1), ask(CLAIMS, 1), ask(FAMILY, 2)], walk=self.walk_button)
        assert combined is not None
        _, view = combined
        self.assertEqual(ids(view), [f"dmbot:walk:{GUILD}:{CID}", "test:family"])

    def test_the_uploads_summary_link_stays_beside_the_walk(self) -> None:
        combined = combine(
            [ask(NAMES, 2, link=True), ask(CLAIMS, 1), ask(CONNECTIONS, 4, link=True)],
            walk=self.walk_button,
        )
        assert combined is not None
        _, view = combined
        self.assertEqual(ids(view), [f"dmbot:walk:{GUILD}:{CID}", "test:names", "test:connections"])

    def test_a_kind_that_was_not_counted_leaves_the_total_off_the_label(self) -> None:
        combined = combine([ask(NAMES, 3), ask(CLAIMS, 0)], walk=self.walk_button)
        assert combined is not None
        self.assertEqual(labels(combined[1]), ["✅ Check everything"])

    def test_without_a_walk_maker_nothing_changes(self) -> None:
        combined = combine([ask(NAMES), ask(CLAIMS)])
        assert combined is not None
        text, view = combined
        self.assertEqual(ids(view), ["test:names", "test:claims"])
        self.assertTrue(text.endswith("Press a button to answer."))

    def test_the_buttons_id_names_the_server_and_the_campaign(self) -> None:
        button = ce.WalkButton(GUILD, CID, 3)
        item = cast("discord.ui.Button[Any]", button.item)
        self.assertEqual(item.custom_id, f"dmbot:walk:{GUILD}:{CID}")
        match = ce.WalkButton.__discord_ui_compiled_template__.fullmatch(item.custom_id or "")
        assert match is not None
        self.assertEqual((match["guild"], match["campaign"]), (str(GUILD), CID))


class FromOnePartToTheNext(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.told: list[str] = []
        self.opened: list[str] = []

        async def tell(interaction: object, text: str, **_: object) -> None:
            self.told.append(text)

        async def run_part(interaction: object, walk: Walk) -> None:
            self.opened.append(walk.kind)

        for target, replacement in (
            ("dmbot.ui.check_everything._tell", tell),
            ("dmbot.ui.check_everything.run_part", run_part),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def counts(self, found: dict[str, int]) -> Any:
        return patch("dmbot.ui.check_everything.waiting_counts", AsyncMock(return_value=found))

    async def test_the_next_part_with_something_waiting_opens_after_one_line(self) -> None:
        walk = Walk(CID, GUILD, 1, (NAMES, CLAIMS, CONNECTIONS))
        with self.counts({NAMES: 0, PEOPLE: 0, CLAIMS: 11, CONNECTIONS: 29}):
            await ce.next_part(cast(Any, object()), walk)
        self.assertEqual(self.told, ["Names done. Next: what was said (11)."])
        self.assertEqual(self.opened, [CLAIMS])

    async def test_a_part_answered_meanwhile_is_skipped(self) -> None:
        walk = Walk(CID, GUILD, 1, (NAMES, CLAIMS, CONNECTIONS))
        with self.counts({NAMES: 0, CLAIMS: 0, CONNECTIONS: 4}):
            await ce.next_part(cast(Any, object()), walk)
        self.assertEqual(self.told, ["Names done. Next: connections (4)."])
        self.assertEqual(self.opened, [CONNECTIONS])

    async def test_after_the_last_part_everything_is_checked(self) -> None:
        walk = Walk(CID, GUILD, 1, (NAMES, CLAIMS)).next()
        with self.counts({NAMES: 0, CLAIMS: 0}):
            await ce.next_part(cast(Any, object()), walk)
        self.assertEqual(self.told, [walks.ALL_DONE])
        self.assertEqual(self.opened, [])

    async def test_nothing_left_in_later_parts_is_everything_checked(self) -> None:
        walk = Walk(CID, GUILD, 1, (NAMES, CLAIMS, CONNECTIONS))
        with self.counts({NAMES: 0, CLAIMS: 0, CONNECTIONS: 0}):
            await ce.next_part(cast(Any, object()), walk)
        self.assertEqual(self.told, [walks.ALL_DONE])
        self.assertEqual(self.opened, [])


class StopForNow(unittest.IsolatedAsyncioTestCase):
    def test_a_card_in_a_walk_gets_a_stop_button_and_one_outside_does_not(self) -> None:
        from dmbot.ui.dmbot_commands import _Menu

        walk = Walk(CID, GUILD, 1, (NAMES,))
        inside, outside = _Menu(), _Menu()
        ce.add_stop(inside, walk, row=2)
        ce.add_stop(outside, None)
        self.assertEqual(labels(inside), ["Stop for now"])
        self.assertEqual(cast("discord.ui.Button[Any]", inside.children[0]).row, 2)
        self.assertEqual(outside.children, [])

    async def test_stop_says_so_and_changes_the_messages_button_to_carry_on(self) -> None:
        from dmbot.ui.dmbot_commands import _Menu

        walk = Walk(CID, GUILD, 1, (NAMES, CLAIMS), origin=(7, 8))
        view = _Menu()
        replaced: list[str] = []

        async def replace(interaction: object, text: str, new: object) -> None:
            replaced.append(text)

        relabel = AsyncMock()
        with (
            patch("dmbot.ui.dmbot_commands._replace", replace),
            patch(
                "dmbot.ui.check_everything.waiting_counts",
                AsyncMock(return_value={NAMES: 3, CLAIMS: 1}),
            ),
            patch("dmbot.ui.check_everything.relabel_origin", relabel),
        ):
            await ce.stop_walk(cast(Any, SimpleNamespace()), walk, view)
        self.assertEqual(replaced, [walks.STOPPED])
        self.assertTrue(view.is_finished())
        self.assertEqual(relabel.await_args.args[1:], (walk, 4))


class WithoutAWalkNothingChanges(unittest.TestCase):
    def test_a_flow_started_the_old_way_has_no_walk(self) -> None:
        from dmbot.ui.claims_review import ClaimsReview
        from dmbot.ui.names import SuggestionReview
        from dmbot.ui.people_review import PeopleReview

        self.assertIsNone(SuggestionReview(CID, ["x"]).walk)
        self.assertIsNone(ClaimsReview(CID, GUILD).walk)
        self.assertIsNone(PeopleReview(CID, GUILD).walk)
        self.assertNotIn("Stop for now", labels(SuggestionReview(CID, ["x"])))

    def test_a_flow_started_in_a_walk_shows_stop_for_now(self) -> None:
        from dmbot.ui.claims_review import ClaimsReview

        walk = Walk(CID, GUILD, 1, (CLAIMS,))
        review = ClaimsReview(CID, GUILD, walk=walk)
        review.page = []
        review._render()
        self.assertIn("Stop for now", labels(review))


_ = partial  # (kept for the stand-in makers above)
