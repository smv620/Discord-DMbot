"""A waiting name that is a known one but for a final "s" (#5215, slice 2): a name of two or more
words is joined through `join_by_ai` (the DM checks it in Check guesses, with Undo); a one-word
name, two names that fit, or a spelling that is already a known name are asked as before. Only
with DMBOT_STORY_WHO_APPLY. Pure rule plus the bot's step with stand-ins; invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from dmbot.memory.models import PROPOSED, Entity, MemoryRuleError
from dmbot.memory.s_join import fold_key, join_target


def row(eid: str, name: str) -> tuple[str, str, str]:
    return (eid, name, name)


class TheRule(unittest.TestCase):
    def test_the_fold_key(self) -> None:
        self.assertEqual(fold_key("Explorer's League"), fold_key("Explorer League"))
        self.assertEqual(fold_key("Explorers Leagues"), "explorer league")
        self.assertNotEqual(fold_key("Harps"), fold_key("Harp"))  # (a 4-letter stem)

    def test_two_words_that_differ_by_a_final_s_join(self) -> None:
        self.assertEqual(join_target("Explorer League", [row("e1", "Explorer's League")]), "e1")

    def test_a_one_word_name_is_always_asked(self) -> None:
        self.assertIsNone(join_target("Harper", [row("e1", "Harpers")]))

    def test_two_known_names_that_fit_are_asked(self) -> None:
        rows = [row("e1", "Explorer's League"), row("e2", "Explorers Leagues")]
        self.assertIsNone(join_target("Explorer League", rows))

    def test_two_spellings_of_one_known_entry_still_join(self) -> None:
        rows = [row("e1", "Explorer's League"), ("e1", "Explorers League", "Explorer's League")]
        self.assertEqual(join_target("Explorer League", rows), "e1")

    def test_a_spelling_that_is_already_a_known_name_is_asked(self) -> None:
        rows = [row("e1", "Explorer's League"), row("e2", "Explorer League")]
        self.assertIsNone(join_target("Explorer League", rows))

    def test_anything_else_is_not_a_final_s(self) -> None:
        self.assertIsNone(join_target("Explorer Lodge", [row("e1", "Explorer's League")]))


def waiting(eid: str, name: str) -> Entity:
    return Entity(eid, "concept", name, "", PROPOSED, None, "scan", 0)


def bot_with(
    *names: Entity, on: bool = True, known: list[tuple[str, str, str]] | None = None
) -> Any:
    memory = SimpleNamespace(
        entities=AsyncMock(return_value=list(names)),
        sound_alikes=AsyncMock(return_value=known or [row("k1", "Explorer's League")]),
        join_by_ai=AsyncMock(),
    )
    return SimpleNamespace(
        settings=SimpleNamespace(story_who_apply_on=on),
        memory=memory,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=None)),
    )


class TheStep(unittest.IsolatedAsyncioTestCase):
    async def run_step(self, bot: Any) -> int:
        from dmbot.bot import DMBot

        return await DMBot._join_final_s(cast(Any, bot), 1, "c1", "s1")

    async def test_joins_a_two_word_name_across_kinds(self) -> None:
        bot = bot_with(waiting("w1", "Explorer League"), waiting("w2", "Harper"))
        self.assertEqual(await self.run_step(bot), 1)
        bot.memory.join_by_ai.assert_awaited_once_with(1, "c1", "k1", "w1", any_kind=True)

    async def test_off_means_off(self) -> None:
        bot = bot_with(waiting("w1", "Explorer League"), on=False)
        self.assertEqual(await self.run_step(bot), 0)
        bot.memory.entities.assert_not_awaited()

    async def test_a_refused_join_is_asked_as_before(self) -> None:
        bot = bot_with(waiting("w1", "Explorer League"))
        bot.memory.join_by_ai.side_effect = MemoryRuleError("no")
        self.assertEqual(await self.run_step(bot), 0)

    async def test_a_failed_read_never_raises(self) -> None:
        bot = bot_with(waiting("w1", "Explorer League"))
        bot.memory.sound_alikes.side_effect = RuntimeError("down")
        self.assertEqual(await self.run_step(bot), 0)
