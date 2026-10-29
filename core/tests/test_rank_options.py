"""Ranking the options of "Did they mean…?" (#2048): the pure parts, the question book and the
bot's one call. The AI only orders the buttons; every failure leaves the question as it was."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import unittest
from typing import Any, cast
from unittest.mock import MagicMock

import tests.test_consent_refusals as cr
from dmbot.ai import Reply
from dmbot.dm_screen.name_questions import question_view
from dmbot.transcript import questions as nq
from dmbot.transcript import rank_options as ro
from tests.test_sessions import DM, GUILD, PLAYER

OPTIONS = (("a" * 32, "Maren"), ("b" * 32, "Marron"))


def answer(name: str) -> Reply:
    return Reply(json.dumps({"likely": name}), cut=False)


class FakeRanker:
    """The fast AI: returns what it was given, records what it was sent."""

    def __init__(self, *replies: Reply | Exception | float) -> None:
        self.replies = list(replies)
        self.sent: list[tuple[str, str, dict[str, Any] | None]] = []

    async def complete(
        self, system: str, text: str, *, max_tokens: int = 8000, schema: Any = None
    ) -> Reply:
        self.sent.append((system, text, schema))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, float):
            await asyncio.sleep(reply)
            return answer("Marron")
        return reply


class ThePureParts(unittest.TestCase):
    def test_the_answer_is_one_of_the_names_or_none(self) -> None:
        names = ["Maren", "Marron"]
        self.assertEqual(ro.read(json.dumps({"likely": "Marron"}), names), "Marron")
        for bad in (
            json.dumps({"likely": "none"}),
            json.dumps({"likely": "Marin"}),  # not an option
            json.dumps({"likely": 3}),
            json.dumps(["Maren"]),
            "Marron",  # not the fixed shape
            "",
        ):
            self.assertIsNone(ro.read(bad, names), bad)

    def test_the_shape_allows_only_the_options_and_none(self) -> None:
        shape = ro.schema(["Maren", "Marron"])
        self.assertEqual(shape["properties"]["likely"]["enum"], ["Maren", "Marron", "none"])
        self.assertFalse(shape["additionalProperties"])

    def test_the_prompt_holds_the_line_the_last_three_lines_and_the_names_only(self) -> None:
        text = ro.prompt(
            "Marin",
            "then Marin speaks",
            ["one", "two", "three", "four"],
            ["Maren", "Marron"],
        )
        self.assertIn("<heard>Marin</heard>", text)
        self.assertIn("1. Maren\n2. Marron", text)
        self.assertNotIn("one", text.split("<scene>")[1])  # only the last three scene lines
        self.assertIn("two\nthree\nfour", text)

    def test_a_secret_name_in_the_text_stops_the_call(self) -> None:
        secret = {"silas vane"}
        self.assertTrue(ro.safe_to_send(["then Marin speaks"], secret))
        self.assertFalse(ro.safe_to_send(["so Silas Vane, the baron"], secret))
        self.assertFalse(ro.safe_to_send(["Silas Vane"], secret))  # also as an option
        self.assertTrue(ro.safe_to_send(["silas vanessa"], secret))  # whole words only

    def test_a_long_name_and_two_that_start_alike_still_read_apart(self) -> None:
        one = nq.option_label("Aurelianus Maximilianus Bellerophon of the Vale", likely=True)
        self.assertLessEqual(len(one), nq.LABEL_MAX)
        self.assertTrue(one.endswith("(likely)"))

    def test_the_likely_option_goes_first_and_the_rest_keep_their_order(self) -> None:
        three = (*OPTIONS, ("c" * 32, "Marrow"))
        self.assertEqual([n for _, n in ro.first(three, "Marrow")], ["Marrow", "Maren", "Marron"])
        self.assertEqual(ro.first(three, "Nobody"), three)


class TheQuestionBook(unittest.TestCase):
    def asked(self) -> tuple[nq.QuestionBook, nq.Asked]:
        book = nq.QuestionBook()
        question = MagicMock(heard="Marin", options=OPTIONS, context="", start=0, end=1)
        asked = book.offer(PLAYER, (question,), 0.0, {"a" * 32}, line="Marin")
        assert asked is not None
        return book, asked

    def test_ranking_reorders_the_open_question_and_marks_it(self) -> None:
        book, asked = self.asked()
        ranked = book.rank(asked.id, "Marron")
        assert ranked is not None
        self.assertEqual([n for _, n in ranked.options], ["Marron", "Maren"])
        self.assertTrue(ranked.likely)
        self.assertIs(book.open, ranked)

    def test_the_buttons_say_likely_on_the_first_and_a_press_means_what_it_shows(self) -> None:
        book, asked = self.asked()
        ranked = book.rank(asked.id, "Marron")
        assert ranked is not None
        labels = [cast(Any, item).item.label for item in question_view(GUILD, ranked).children]
        self.assertEqual(labels[:2], ["Marron (likely)", "Maren"])
        self.assertEqual(ranked.options[0][1], "Marron")  # button 0 is the likely name
        plain = [cast(Any, item).item.label for item in question_view(GUILD, asked).children]
        self.assertEqual(plain[:2], ["Maren", "Marron"])  # unranked: as before

    def test_a_long_name_still_fits_the_button_with_the_word_likely(self) -> None:
        label = nq.option_label("Aurelianus Maximilianus Bellerophon", likely=True)
        self.assertLessEqual(len(label), nq.LABEL_MAX)
        self.assertTrue(label.endswith("(likely)"))

    def test_nothing_changes_for_an_unknown_name_a_second_ranking_or_a_saving_answer(self) -> None:
        book, asked = self.asked()
        self.assertEqual(book.rank(asked.id, "Marin"), asked)  # not an option
        self.assertIsNone(book.rank("other", "Marron"))
        book.answering = True
        self.assertEqual(book.rank(asked.id, "Marron"), asked)
        book.answering = False
        first = book.rank(asked.id, "Marron")
        self.assertIs(book.rank(asked.id, "Maren"), first)  # ranked once


class TheBotsCall(cr.Refusals):
    async def question(self, *replies: Reply | Exception | float) -> tuple[Any, Any, FakeRanker]:
        table, _, _, _ = await self.asked_about_marin()
        asked = table.questions.open
        assert asked is not None
        ai = FakeRanker(*replies)
        self.bot.topic_ai = ai  # type: ignore[assignment]
        return table, asked, ai

    def posted(self) -> Any:
        return self.bot.post_message  # (a mock, from the harness)

    def saved(self) -> Any:
        return cast(Any, self.bot.memory).add_correction

    def names(self, asked: Any) -> list[str]:
        return [n for _, n in asked.options]

    async def test_the_likely_name_goes_first_and_the_ai_is_sent_only_the_line_scene_and_names(
        self,
    ) -> None:
        table, asked, ai = await self.question(answer("Marron"))
        ranked = await self.bot._rank_question(table, asked)
        self.assertEqual(self.names(ranked)[0], "Marron")
        self.assertTrue(ranked.likely)
        ((_, text, schema),) = ai.sent
        self.assertIn("Marin", text)
        self.assertIn("then Marin speaks", text)
        self.assertEqual(schema, ro.schema(self.names(asked)))
        self.assertNotIn(str(PLAYER), text)  # nobody's id, and no speaker names
        self.assertEqual((table.rank_calls, table.rank_likely), (1, 1))

    async def test_every_failure_posts_the_question_as_it_was(self) -> None:
        for reply in (
            answer("none"),
            answer("Nobody Known"),
            Reply("not json", cut=False),
            Reply(json.dumps({"likely": "Marron"}), cut=True),
            Reply("", cut=False, refused=True),
            RuntimeError("down"),
            5.0,  # slower than the time allowed
        ):
            table, asked, _ = await self.question(reply)
            with unittest.mock.patch.object(ro, "TIMEOUT_S", 0.05):
                ranked = await self.bot._rank_question(table, asked)
            self.assertEqual(ranked, asked, reply)
            self.assertFalse(ranked.likely)
            self.assertEqual(table.rank_likely, 0)

    async def test_the_counts_say_what_happened(self) -> None:
        table, asked, _ = await self.question(answer("none"))
        await self.bot._rank_question(table, asked)
        self.assertEqual((table.rank_calls, table.rank_none, table.rank_errors), (1, 1, 0))
        table, asked, _ = await self.question(RuntimeError("down"))  # (the same table)
        await self.bot._rank_question(table, asked)
        self.assertEqual((table.rank_calls, table.rank_none, table.rank_errors), (2, 1, 1))

    async def test_there_is_a_cap_per_session_and_zero_turns_it_off(self) -> None:
        table, asked, ai = await self.question(answer("Marron"))
        table.rank_calls = 40
        self.assertEqual(await self.bot._rank_question(table, asked), asked)
        table.rank_calls = 0
        self.bot.settings = dataclasses.replace(self.bot.settings, cleaner_rank_max=0)
        self.assertEqual(await self.bot._rank_question(table, asked), asked)
        self.assertEqual(ai.sent, [])  # no call in either case

    async def test_no_ai_key_means_no_call_and_no_change(self) -> None:
        table, asked, _ = await self.question()
        self.bot.topic_ai = None
        self.assertEqual(await self.bot._rank_question(table, asked), asked)

    async def test_a_secret_name_in_the_scene_stops_the_call(self) -> None:
        from dmbot.memory.lookup import CampaignLookup, LookupData
        from dmbot.memory.models import CONFIRMED, Alias, Entity

        table, asked, ai = await self.question(answer("Marron"))
        lookup = table.name_lookup
        secret = Alias(
            "s" * 32,
            "a" * 32,
            "Silas Vane",
            "silas vane",
            "full",
            None,
            True,
            CONFIRMED,
            (),
            "dm",
            0,
        )
        entities = tuple(lookup.entities.values())
        assert isinstance(entities[0], Entity)
        table.name_lookup = CampaignLookup.build(
            LookupData(2, entities, (*lookup_aliases(lookup), secret), (), ())
        )
        self.said(table, "and Silas Vane smiled")
        await self.settle()
        ranked = await self.bot._rank_question(table, asked)
        self.assertEqual(ranked, asked)
        self.assertEqual(ai.sent, [])  # nothing at all was sent

    async def test_two_options_of_one_name_are_not_ranked(self) -> None:
        table, asked, ai = await self.question(answer("Maren"))
        twin = dataclasses.replace(asked, options=(asked.options[0], ("c" * 32, "Maren")))
        self.assertEqual(await self.bot._rank_question(table, twin), twin)
        self.assertEqual(ai.sent, [])

    async def test_a_clue_only_some_know_stops_the_call_like_a_secret_name(self) -> None:
        table, asked, ai = await self.question(answer("Marron"))
        entities = {
            k: dataclasses.replace(v, audience_limited=True) if v.name == "Maren" else v
            for k, v in table.name_lookup.entities.items()
        }
        table.name_lookup = dataclasses.replace(table.name_lookup, entities=entities)
        self.assertEqual(await self.bot._rank_question(table, asked), asked)
        self.assertEqual(ai.sent, [])

    async def test_a_question_closed_during_the_call_is_not_posted(self) -> None:
        table, asked, ai = await self.question(answer("Marron"))
        table.questions.close(nq.EXPIRED, 0.0)
        table.questions.closed_at = None
        table.questions.open = asked
        self.posted().reset_mock()
        real = ai.complete

        async def close_then_answer(*a: Any, **k: Any) -> Reply:
            table.questions.close(nq.ENDED, 0.0)  # the session ends while the AI thinks
            return await real(*a, **k)

        ai.complete = close_then_answer  # type: ignore[method-assign]
        await self.bot._ask_dm(table, asked)
        self.posted().assert_not_awaited()

    async def test_consent_withdrawn_during_the_call_posts_nothing(self) -> None:
        table, asked, ai = await self.question(answer("Marron"))
        table.questions.close(nq.EXPIRED, 0.0)
        table.questions.closed_at = None
        table.questions.open = asked
        self.posted().reset_mock()
        real = ai.complete

        async def stop_then_answer(*a: Any, **k: Any) -> Reply:
            self.stop(PLAYER)
            return await real(*a, **k)

        ai.complete = stop_then_answer  # type: ignore[method-assign]
        await self.bot._ask_dm(table, asked)
        self.posted().assert_not_awaited()
        self.assertIsNone(table.questions.open)

    async def test_a_stopped_speakers_lines_are_not_in_the_scene(self) -> None:
        table, asked, ai = await self.question(answer("Marron"))
        self.stop(PLAYER)
        await self.bot._rank_question(table, asked)
        for _, text, _ in ai.sent:
            self.assertNotIn("then Marin speaks", text.split("<scene>")[1])

    async def test_the_question_is_posted_ranked_and_a_press_of_the_first_button_picks_it(
        self,
    ) -> None:
        table, asked, _ = await self.question(answer("Marron"))
        table.questions.close(nq.EXPIRED, 0.0)
        table.questions.closed_at = None
        table.questions.open = asked
        self.posted().reset_mock()
        await self.bot._ask_dm(table, asked)
        _, _, view = self.posted().await_args.args
        self.assertEqual(cast(Any, view.children[0]).item.label, "Marron (likely)")
        self.assertEqual(table.questions.open.options[0][1], "Marron")
        _, done, _ = await self.bot.answer_name_question(GUILD, asked.id, "0", DM)
        self.assertTrue(done)
        self.assertEqual((table.rank_answered, table.rank_picked), (1, 1))
        self.saved().assert_awaited()
        self.assertEqual(self.saved().await_args.kwargs["entity_id"], "b" * 32)

    async def test_picking_another_name_counts_as_answered_not_as_picked(self) -> None:
        table, asked, _ = await self.question(answer("Marron"))
        table.questions.close(nq.EXPIRED, 0.0)
        table.questions.closed_at = None
        table.questions.open = asked
        await self.bot._ask_dm(table, asked)
        await self.bot.answer_name_question(GUILD, asked.id, "1", DM)
        self.assertEqual((table.rank_answered, table.rank_picked), (1, 0))


def lookup_aliases(lookup: Any) -> tuple[Any, ...]:
    """The aliases a built lookup was made from (its non-secret entries, as `Alias` rows)."""
    from dmbot.memory.models import CONFIRMED, Alias

    return tuple(
        Alias(
            e.alias_id, e.entity_id, e.text, e.key, "full", None, e.secret, CONFIRMED, (), "dm", 0
        )
        for e in lookup.names
    )


if __name__ == "__main__":
    unittest.main()
