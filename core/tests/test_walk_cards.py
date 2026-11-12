"""The one-by-one cards of a kind inside "✅ Check everything" (#4192, slice 2): the progress line,
each button's answer, Later, an Undo of its own, Stop for now, and on to the next part when none is
left; and the kinds-of-connection part that uses them. No database and no Discord."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import discord

from dmbot.dm_questions import KINDS
from dmbot.memory.models import MemoryRuleError
from dmbot.ui import kind_offer, walk_cards
from dmbot.walk import Walk

CID = "a" * 32
GUILD = 4242


def kind(
    items: list[str],
    answered: list[str],
    *,
    gone: str = "",
    boom: str = "",
    log: list[str] | None = None,
) -> walk_cards.Kind:
    async def waiting() -> list[str]:
        return [i for i in items if i not in answered]

    def choice(label: str) -> walk_cards.Choice:
        async def do(item: str) -> walk_cards.Answer:
            if log is not None:
                log.append("answered")
            if item == boom:
                raise RuntimeError("database down")
            if item == gone:
                raise MemoryRuleError("gone")
            answered.append(item)
            undo: discord.ui.Item[Any] | None = (
                discord.ui.Button(label="Undo") if label == "Yes" else None
            )
            return walk_cards.Answer(f"{label} {item}", undo)

        return walk_cards.Choice(label, discord.ButtonStyle.success, do)

    later = walk_cards.Choice("Later", discord.ButtonStyle.secondary, AsyncMock(), later=True)
    return walk_cards.Kind(
        waiting=waiting,
        key=lambda item: item,
        text=lambda item, left: f"card {item} ({left} left)",
        choices=lambda: [choice("Yes"), later],
        finished="finished",
    )


class TheCards(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.shown: list[tuple[str, Any]] = []
        self.next_parts: list[str] = []
        self.told: list[str] = []

        async def replace(interaction: object, text: str, view: Any) -> None:
            self.shown.append((text, view))

        async def next_part(interaction: object, walk: Walk) -> None:
            self.next_parts.append(walk.kind)

        async def tell(interaction: object, text: str, **_: object) -> None:
            self.told.append(text)

        async def send(interaction: object, text: str, view: Any = None) -> None:
            self.shown.append((text, view))

        self.events: list[str] = []

        async def answer_first(interaction: object, **_: object) -> None:
            self.events.append("deferred")

        for target, replacement in (
            ("dmbot.ui.walk_cards._answer_first", answer_first),
            ("dmbot.ui.walk_cards._replace", replace),
            ("dmbot.ui.walk_cards._send", send),
            ("dmbot.ui.walk_cards._tell", tell),
            ("dmbot.ui.walk_cards.next_part", next_part),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.followups: list[str] = []

        async def followup(text: str, **_: object) -> None:
            self.followups.append(text)

        self.interaction = SimpleNamespace(followup=SimpleNamespace(send=followup))
        self.walk = Walk(CID, GUILD, 1, (KINDS,))

    async def press(self, view: walk_cards.WalkCards, label: str) -> None:
        button = next(c for c in view.children if getattr(c, "label", "") == label)
        await button.callback(cast(Any, self.interaction))

    async def test_the_first_card_has_its_progress_line_and_the_choices_and_stop(self) -> None:
        answered: list[str] = []
        await walk_cards.start(
            cast(Any, self.interaction), kind(["a", "b", "c"], answered), self.walk
        )
        text, view = self.shown[0]
        self.assertEqual(text, "_Kinds of connection · 1 of 3_\ncard a (3 left)")
        self.assertEqual(
            [c.label for c in view.children],
            ["Yes", "Later", "Stop for now"],
        )

    async def test_an_answer_moves_to_the_next_card_and_the_last_goes_on_to_the_next_part(
        self,
    ) -> None:
        answered: list[str] = []
        await walk_cards.start(cast(Any, self.interaction), kind(["a", "b"], answered), self.walk)
        view = self.shown[0][1]
        await self.press(view, "Yes")
        self.assertIn("card b (1 left)", self.shown[-1][0])
        self.assertIn("2 of 2", self.shown[-1][0])
        await self.press(view, "Yes")
        self.assertEqual(answered, ["a", "b"])
        self.assertEqual(self.shown[-1][0].splitlines()[-1], "finished")
        self.assertEqual(self.next_parts, [KINDS])

    async def test_an_undo_is_sent_as_its_own_message_and_the_card_moves_on(self) -> None:
        answered: list[str] = []
        await walk_cards.start(cast(Any, self.interaction), kind(["a", "b"], answered), self.walk)
        await self.press(self.shown[0][1], "Yes")
        self.assertEqual(self.told, ["Yes a"])  # (the answer and its Undo, a message of their own)
        self.assertNotIn("Yes a", self.shown[-1][0])  # (the card is the next item, not the answer)

    async def test_later_skips_the_item_this_round_without_answering_it(self) -> None:
        answered: list[str] = []
        await walk_cards.start(cast(Any, self.interaction), kind(["a", "b"], answered), self.walk)
        await self.press(self.shown[0][1], "Later")
        self.assertEqual(answered, [])
        self.assertIn("card b", self.shown[-1][0])
        self.assertIn(walk_cards.LATER_DONE, self.shown[-1][0])

    async def test_an_item_answered_meanwhile_says_so_and_goes_on(self) -> None:
        answered: list[str] = []
        await walk_cards.start(
            cast(Any, self.interaction), kind(["a", "b"], answered, gone="a"), self.walk
        )
        await self.press(self.shown[0][1], "Yes")
        self.assertIn("gone", self.shown[-1][0])  # (the store's words, or GONE when it gives none)

    async def test_discord_is_answered_before_the_store_is_touched(self) -> None:
        answered: list[str] = []
        await walk_cards.start(
            cast(Any, self.interaction), kind(["a", "b"], answered, log=self.events), self.walk
        )
        await self.press(self.shown[0][1], "Yes")
        self.assertEqual(self.events[:2], ["deferred", "answered"])

    async def test_a_second_press_while_answering_does_nothing(self) -> None:
        answered: list[str] = []
        await walk_cards.start(cast(Any, self.interaction), kind(["a", "b"], answered), self.walk)
        view = self.shown[0][1]
        view.shown = None  # (the first press has taken the card and is still at work)
        await self.press(view, "Yes")
        self.assertEqual(answered, [])
        self.assertEqual(len(self.shown), 1)

    async def test_a_store_failure_says_so_and_keeps_the_card_for_another_try(self) -> None:
        answered: list[str] = []
        await walk_cards.start(
            cast(Any, self.interaction), kind(["a", "b"], answered, boom="a"), self.walk
        )
        view = self.shown[0][1]
        await self.press(view, "Yes")
        self.assertEqual(self.told, [walk_cards.FAILED])
        self.assertEqual(len(self.shown), 1)  # (no move on)
        self.assertEqual(view.shown, "a")

    async def test_another_rule_error_is_shown_in_the_stores_words(self) -> None:
        answered: list[str] = []
        part = kind(["a", "b"], answered, gone="a")
        await walk_cards.start(cast(Any, self.interaction), part, self.walk)
        await self.press(self.shown[0][1], "Yes")
        self.assertIn("gone", self.shown[-1][0])  # (the store's own words, here "gone")

    async def test_nothing_waiting_goes_straight_to_the_next_part(self) -> None:
        await walk_cards.start(cast(Any, self.interaction), kind([], []), self.walk)
        self.assertEqual(self.next_parts, [KINDS])
        self.assertEqual(self.shown, [])


class TheKindsPart(unittest.IsolatedAsyncioTestCase):
    def offer(self) -> Any:
        return SimpleNamespace(words="made friends", words_key="made friends", count=4)

    async def test_yes_makes_the_kind_and_offers_an_undo(self) -> None:
        memory = SimpleNamespace(
            make_kind_of_words=AsyncMock(return_value=SimpleNamespace(batch=9))
        )
        text, undo = await kind_offer.apply_answer(memory, GUILD, CID, self.offer(), "yes")
        memory.make_kind_of_words.assert_awaited_once()
        self.assertIn("4 connections now read “made friends”", text)
        assert undo is not None
        self.assertEqual(undo.item.custom_id, f"dmbot:kindundo:{GUILD}:{CID}:9")

    async def test_not_now_and_never_are_the_stored_answers_with_their_words(self) -> None:
        memory = SimpleNamespace(answer_kind_offer=AsyncMock())
        later, _ = await kind_offer.apply_answer(memory, GUILD, CID, self.offer(), "later")
        never, _ = await kind_offer.apply_answer(memory, GUILD, CID, self.offer(), "never")
        self.assertIn("3 more times", later)
        self.assertIn("won't ask about these words again", never)
        self.assertEqual(memory.answer_kind_offer.await_count, 2)

    async def test_the_part_lists_the_offers_and_has_the_three_answers(self) -> None:
        memory = SimpleNamespace(kind_offer_list=AsyncMock(return_value=[self.offer()]))
        part = kind_offer.walk_kind(memory, GUILD, CID)
        self.assertEqual(
            [c.label for c in part.choices()], [kind_offer.YES, kind_offer.LATER, kind_offer.NEVER]
        )
        self.assertEqual(len(await part.waiting()), 1)
        self.assertEqual(part.key(self.offer()), "made friends")
