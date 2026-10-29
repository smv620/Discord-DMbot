"""The kind the name scan suggests for a new name (#2937): the answer's checks, the AI call's
guards, and the review card that shows the kind (or a picker when there is none)."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord

from dmbot.ai import AIError, Reply
from dmbot.memory import scan_kinds
from dmbot.memory.models import PROPOSED, Entity
from dmbot.ui import names as ui
from dmbot.ui.name_background import BulkConfirm, scan_view


def suggestion(name: str, type_: str = "concept", role: str | None = None) -> Entity:
    return Entity(name[:1] * 32, type_, name, "Heard 3 times", PROPOSED, role, "scan", 0)


def answer(*pairs: tuple[str, str]) -> str:
    return json.dumps({"kinds": [{"name": n, "kind": k} for n, k in pairs]})


class TheAnswer(unittest.TestCase):
    def test_a_kind_is_kept_as_dmbot_keeps_it(self) -> None:
        got = scan_kinds.read_answer(
            answer(("Greyfen", "place"), ("Oskar", "person"), ("Lantern Guild", "group")),
            ["Greyfen", "Oskar", "Lantern Guild"],
        )
        self.assertEqual(got, {"Greyfen": "place", "Oskar": "npc", "Lantern Guild": "faction"})

    def test_a_wrong_kind_a_name_not_asked_and_an_idea_are_dropped(self) -> None:
        got = scan_kinds.read_answer(
            answer(("Greyfen", "planet"), ("Invented", "place"), ("Oskar", "idea")),
            ["Greyfen", "Oskar"],
        )
        self.assertEqual(got, {})  # (no kind: the card asks)

    def test_an_unreadable_answer_gives_nothing(self) -> None:
        self.assertEqual(scan_kinds.read_answer("not json", ["Greyfen"]), {})
        self.assertEqual(scan_kinds.read_answer('{"kinds": 5}', ["Greyfen"]), {})

    def test_the_sentence_sent_is_short_and_holds_the_name(self) -> None:
        line = "x " * 300 + "we rode to Greyfen at dawn " + "y " * 300
        piece = scan_kinds.context_for("greyfen", ["nothing here", line])
        self.assertIn("Greyfen", piece)
        self.assertLessEqual(len(piece), scan_kinds.CONTEXT_CHARS)
        self.assertEqual(scan_kinds.context_for("Zed", [line]), "")

    def test_names_go_in_batches(self) -> None:
        names = [f"N{i}" for i in range(scan_kinds.PER_CALL * 2 + 1)]
        self.assertEqual([len(b) for b in scan_kinds.batches(names)], [40, 40, 1])


def host(ai: Any, *, plan: bool = True) -> Any:
    from dmbot.bot import DMBot

    fake = SimpleNamespace(
        name_pass_ai=ai,
        campaigns=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="c"))),
        plan_allows=AsyncMock(return_value=plan),
    )
    return DMBot._scan_kinds.__get__(fake)


class TheAsk(unittest.IsolatedAsyncioTestCase):
    async def test_the_kinds_come_back_for_the_names_asked(self) -> None:
        ai = SimpleNamespace(
            complete=AsyncMock(return_value=Reply(answer(("Greyfen", "place")), False))
        )
        got = await host(ai)(1, "c", ["Greyfen", "Oskar"], ["we rode to Greyfen"])
        self.assertEqual(got, {"Greyfen": "place"})
        sent = ai.complete.await_args.args[1]
        self.assertIn("Greyfen | we rode to Greyfen", sent)

    async def test_no_ai_a_plan_without_it_or_a_failed_call_means_no_kinds(self) -> None:
        self.assertEqual(await host(None)(1, "c", ["Greyfen"], []), {})
        ai = SimpleNamespace(complete=AsyncMock())
        self.assertEqual(await host(ai, plan=False)(1, "c", ["Greyfen"], []), {})
        ai.complete.assert_not_awaited()  # (no plan, no cost)
        failing = SimpleNamespace(complete=AsyncMock(side_effect=AIError("busy")))
        self.assertEqual(await host(failing)(1, "c", ["Greyfen"], []), {})


class TheCard(unittest.IsolatedAsyncioTestCase):
    def view(self, entity: Entity) -> ui.SuggestionReview:
        view = ui.SuggestionReview("c" * 32, [entity.id])
        view.known_kind = ui.background_kind(entity)
        view.name = entity.name
        view._buttons()
        return view

    def labels(self, view: discord.ui.View) -> list[str]:
        return [
            str(getattr(c, "label", "") or getattr(c, "placeholder", "")) for c in view.children
        ]

    async def test_a_name_with_a_kind_says_it_and_lets_the_dm_change_it(self) -> None:
        labels = self.labels(self.view(suggestion("Greyfen", "place")))
        self.assertIn("✅ Yes, a place", labels)
        self.assertIn("✏️ Something else", labels)

    async def test_a_name_with_no_kind_shows_the_picker_on_the_card(self) -> None:
        view = self.view(suggestion("Greyfen"))
        labels = self.labels(view)
        self.assertNotIn("✅ Yes, add it", labels)
        self.assertTrue(any("What is Greyfen?" in label for label in labels))
        self.assertTrue(any(isinstance(c, discord.ui.Select) for c in view.children))
        self.assertIn("🔗 Same as a known name…", labels)
        self.assertIn("🚫 Not a name", labels)
        self.assertIn("⏳ Later", labels)

    async def test_add_all_groups_the_scans_suggestions_by_kind(self) -> None:
        one = scan_view(
            "c" * 32,
            [suggestion("Greyfen", "place"), suggestion("Oakvale", "place"), suggestion("Kaz")],
        )
        self.assertIsInstance(one, BulkConfirm)
        self.assertIn("✅ Add all 2 places", self.labels(one))
        plain = scan_view("c" * 32, [suggestion("Kaz")])
        self.assertNotIsInstance(plain, BulkConfirm)  # just the one-by-one check


if __name__ == "__main__":
    unittest.main()
