"""DMbot's top answers on the DM's "Check new names" card (#3603): the order and the words, the
choices only a DM sees, the card's layout and presses, and "That's wrong" on DMbot's own join.
Invented names. The database tests need Postgres."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock, patch

import discord

from dmbot.memory import who_top
from dmbot.memory.models import PROPOSED, WaitingJoin
from dmbot.memory.who_answers import ENTITY, NEW, StoredAnswer, WhoAnswers
from dmbot.ui import joined_review
from dmbot.ui import names as ui
from tests.test_web_name_checks import GUILD, WithChecks


class Words(unittest.TestCase):
    def test_a_new_entry_and_another_name_read_as_the_website_reads_them(self) -> None:
        self.assertEqual(who_top.label(new_kind="character"), "new character")
        self.assertEqual(who_top.label(new_kind="faction"), "new group")
        self.assertEqual(
            who_top.label(name="Superman", kind="character"), "a.k.a. Superman, a character"
        )
        self.assertEqual(
            who_top.label(name="the woman", kind="character", tie="Mira"),
            "a.k.a. the woman, with Mira",
        )

    def test_every_new_kind_the_ai_may_give_is_a_kind_the_card_knows(self) -> None:
        self.assertTrue(set(who_top.CARD_KIND.values()) <= set(ui.KINDS))


class Ranking(WithChecks):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.who = WhoAnswers(self.memory)

    async def keep(self, name_id: str, *answers: StoredAnswer) -> None:
        await self.who.keep(
            GUILD, self.one.id, name_id, list(answers), speaker_ids=(), opted_in=set(),
            session_started_at=1, prompt_version="who-1", model="m",
        )  # fmt: skip

    async def top(self, name: str, *, hidden: bool = True, avoid: tuple[str, ...] = ()) -> Any:
        entity = await self.memory.entity(GUILD, self.one.id, name)
        assert entity is not None
        return await who_top.top_for(
            self.memory, GUILD, self.one.id, entity, opted_in=set(), include_hidden=hidden,
            avoid=avoid,
        )  # fmt: skip

    async def test_the_stored_answers_come_first_in_their_order_then_the_ranked_ones(self) -> None:
        name = await self.suggest("Tam")
        ilse = await self.known("Ilse", "character")
        old_tam = await self.known("Old Tam", "character")
        await self.keep(
            name,
            StoredAnswer(ENTITY, "likely", target_id=ilse),
            StoredAnswer(NEW, "maybe", new_kind="character"),
        )
        got = await self.top(name)
        self.assertEqual(
            [(t.label, t.target_id, t.new_kind) for t in got],
            [
                ("a.k.a. Ilse, a character", ilse, None),
                ("new character", None, "character"),
                ("a.k.a. Old Tam, a character", old_tam, None),
            ],
        )
        self.assertEqual(got[0].confidence, "likely")

    async def test_with_no_stored_answer_the_ranked_names_are_the_choices(self) -> None:
        name = await self.suggest("Tam")
        old_tam = await self.known("Old Tam", "character")
        await self.known("Oakmere", "place")
        got = await self.top(name)
        self.assertEqual([t.target_id for t in got], [old_tam])

    async def test_nothing_linking_it_to_a_name_gives_no_choices(self) -> None:
        name = await self.suggest("Vale")
        await self.known("Oakmere", "place")
        self.assertEqual(await self.top(name), [])

    async def test_at_most_five_and_a_name_said_to_be_wrong_is_left_out(self) -> None:
        name = await self.suggest("Tam")
        ids = [await self.known(f"Tam {n}", "character") for n in range(7)]
        self.assertEqual(len(await self.top(name)), who_top.TOP)
        got = await self.top(name, avoid=(ids[0],))
        self.assertNotIn(ids[0], [t.target_id for t in got])

    async def test_a_hidden_entry_is_a_choice_for_a_dm_only(self) -> None:
        name = await self.suggest("Fox")
        fox = await self.known("The Grey Fox", "quest")
        await self.keep(name, StoredAnswer(ENTITY, "likely", target_id=fox))
        self.assertEqual([t.target_id for t in await self.top(name)], [fox])
        self.assertEqual(await self.top(name, hidden=False), [])

    async def test_an_answer_naming_an_entry_that_is_gone_is_not_a_choice(self) -> None:
        name = await self.suggest("Tam")
        old_tam = await self.known("Old Tam", "character")
        await self.keep(name, StoredAnswer(ENTITY, "sure", target_id="f" * 32))
        self.assertEqual([t.target_id for t in await self.top(name)], [old_tam])


def card(top: list[who_top.Top], **kw: Any) -> ui.SuggestionReview:
    view = ui.SuggestionReview("c" * 32, ["e" * 32])
    view.top = top
    view.name = "Tam"
    view.known_kind = kw.get("kind", "")
    view._buttons()
    return view


class TheCard(unittest.TestCase):
    TOP: ClassVar[list[who_top.Top]] = [
        who_top.Top("a.k.a. Old Tam, a character", target_id="a" * 32, confidence="sure"),
        who_top.Top("new character", new_kind="character"),
    ]

    def test_the_best_guesses_are_the_first_row_with_more_options_last(self) -> None:
        view = card(self.TOP)
        first = view.children[0]
        assert isinstance(first, discord.ui.Select)
        self.assertEqual(first.row, 0)
        self.assertEqual(first.placeholder, "DMbot's best guesses…")
        self.assertEqual(
            [o.label for o in first.options],
            ["a.k.a. Old Tam, a character", "new character", "More options…"],
        )
        self.assertEqual(first.options[0].description, "DMbot is sure")
        self.assertEqual(first.options[-1].value, ui.MORE_OPTIONS)

    def test_with_no_answers_the_card_is_what_it_was(self) -> None:
        plain = card([])
        self.assertFalse(
            any(getattr(c, "placeholder", "") == "DMbot's best guesses…" for c in plain.children)
        )
        self.assertTrue(all(c.row in (0, 1, 2, 3, 4) for c in plain.children))

    def test_every_layout_fits_five_rows_and_a_row_never_holds_a_menu_and_a_button(self) -> None:
        for kind in ("", "npc"):
            view = card(self.TOP, kind=kind)
            view.refs = [("s", 1)]  # (so "Where was it said?" shows)
            view._buttons()
            rows: dict[int | None, list[Any]] = {}
            for c in view.children:
                rows.setdefault(c.row, []).append(c)
            for row, items in rows.items():
                self.assertIsNotNone(row)
                self.assertLessEqual(row or 0, 4)
                if any(isinstance(i, discord.ui.Select) for i in items):
                    self.assertEqual(len(items), 1, (kind, row))
                self.assertLessEqual(len(items), 5)


class Pressing(unittest.IsolatedAsyncioTestCase):
    def setup(self, *, dm: bool = True) -> ui.SuggestionReview:
        view = ui.SuggestionReview("c1", ["e" * 32])
        view.top = TheCard.TOP
        view.entity_id = "e" * 32
        view._buttons()
        self.told: list[str] = []
        self.sent: list[Any] = []
        self.same_as = AsyncMock()
        self.confirm_as = AsyncMock()
        campaign = SimpleNamespace(id="c1", guild_id=5)
        memory = SimpleNamespace(
            entity=AsyncMock(return_value=SimpleNamespace(id="e" * 32, status=PROPOSED, name="Tam"))
        )

        async def tell(_i: Any, text: str, **_k: Any) -> None:
            self.told.append(text)

        for p in (
            patch.object(ui, "_campaign_for", AsyncMock(return_value=campaign)),
            patch.object(ui, "_memory", lambda _i: memory),
            patch.object(ui, "sees_secrets", lambda _c, _u: dm),
            patch.object(ui, "_tell", tell),
            patch.object(ui, "_answer_first", AsyncMock()),
        ):
            p.start()
            self.addCleanup(p.stop)
        for name, fake in (
            ("_same_as", self.same_as),
            ("_confirm_as", self.confirm_as),
        ):
            p = patch.object(view, name, fake)
            p.start()
            self.addCleanup(p.stop)
        return view

    def interaction(self) -> Any:
        async def send(text: str, **kw: Any) -> None:
            self.sent.append((text, kw))

        return SimpleNamespace(
            user=SimpleNamespace(id=7),
            client=SimpleNamespace(settings=SimpleNamespace(site_url="https://example.org")),
            followup=SimpleNamespace(send=send),
        )

    async def test_an_entry_choice_is_the_same_as_with_an_undo(self) -> None:
        view = self.setup()
        cast(Any, view.best)._values = ["0"]
        await view._top_picked(self.interaction())
        self.same_as.assert_awaited_once()
        self.assertEqual(self.same_as.call_args.args[1], "a" * 32)
        self.assertTrue(self.same_as.call_args.kwargs["undo"])

    async def test_a_new_choice_confirms_it_as_that_kind(self) -> None:
        view = self.setup()
        cast(Any, view.best)._values = ["1"]
        await view._top_picked(self.interaction())
        self.assertEqual(self.confirm_as.call_args.args[-1], "npc")

    async def test_more_options_links_to_this_campaign_and_name(self) -> None:
        view = self.setup()
        cast(Any, view.best)._values = [ui.MORE_OPTIONS]
        await view._top_picked(self.interaction())
        ((text, kw),) = self.sent
        self.assertIn("mark it as not worth tracking", text)
        (button,) = kw["view"].children
        self.assertIn("campaign=5.c1", button.url)
        self.assertIn("name=" + "e" * 32, button.url)
        self.assertTrue(kw["ephemeral"])

    async def test_nobody_but_a_dm_can_press_any_of_it(self) -> None:
        view = self.setup(dm=False)
        for value in ("0", "1", ui.MORE_OPTIONS):
            cast(Any, view.best)._values = [value]
            await view._top_picked(self.interaction())
        self.same_as.assert_not_awaited()
        self.confirm_as.assert_not_awaited()
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.told), 3)


class ThatsWrong(unittest.IsolatedAsyncioTestCase):
    JOIN = WaitingJoin(41, "k" * 32, "g" * 32, "The Ferry Lady", "Old Tam", 1)

    async def run_it(self, *, dm: bool = True) -> tuple[Any, list[str], list[Any]]:
        memory = SimpleNamespace(
            waiting_joins=AsyncMock(return_value=[self.JOIN]),
            undo=AsyncMock(),
            entity=AsyncMock(
                return_value=SimpleNamespace(id="g" * 32, status=PROPOSED, name="The Ferry Lady")
            ),
        )
        replaced: list[str] = []
        cards: list[Any] = []

        async def replace(_i: Any, text: str, view: Any) -> None:
            replaced.append(text)
            cards.append(view)

        async def prepare(self_: Any, *_a: Any) -> str:
            return "THE CARD"

        view = joined_review.JoinedReview("c1")
        view.shown = self.JOIN
        for p in (
            patch.object(joined_review, "_memory", lambda _i: memory),
            patch.object(
                joined_review,
                "_campaign_for",
                AsyncMock(return_value=SimpleNamespace(id="c1", guild_id=5)),
            ),
            patch.object(joined_review, "sees_secrets", lambda _c, _u: dm),
            patch.object(joined_review, "changed", lambda *_a: None),
            patch.object(joined_review, "_tell", AsyncMock()),
            patch.object(joined_review, "_replace", replace),
            patch.object(joined_review, "_answer_first", AsyncMock()),
            patch.object(ui.SuggestionReview, "prepare", prepare),
        ):
            p.start()
            self.addCleanup(p.stop)
        await view._wrong(SimpleNamespace(user=SimpleNamespace(id=7)))
        return memory, replaced, cards

    async def test_it_undoes_the_join_and_opens_the_names_card_without_that_entry(self) -> None:
        memory, replaced, cards = await self.run_it()
        memory.undo.assert_awaited_once_with(5, "c1", 41)
        self.assertIn("Undone", replaced[0])
        self.assertIn("THE CARD", replaced[0])
        card_view = cards[0]
        self.assertIsInstance(card_view, ui.SuggestionReview)
        self.assertEqual(card_view.avoid, {"g" * 32: "k" * 32})

    async def test_a_non_dm_changes_nothing(self) -> None:
        memory, replaced, _cards = await self.run_it(dm=False)
        memory.undo.assert_not_awaited()
        self.assertEqual(replaced, [])

    def test_the_wrong_button_is_on_the_card(self) -> None:
        view = joined_review.JoinedReview("c1")
        self.assertTrue(hasattr(view, "_wrong"))
