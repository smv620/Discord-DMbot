"""The shared-rules messages stay in one message and never cut inside bold (#1851).

They quote house-rule text, which DMs often write with `**bold**`. A bare `[:TEXT_MAX]` cut can
land between the two `**` and turn the rest of the message bold, or run past Discord's limit with a
line added after the cut. Each message here is fed 30 rules of 200 characters with bold in them.
"""

from __future__ import annotations

import re
import unittest
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from dmbot.discord_limits import MESSAGE_MAX
from dmbot.dm_screen import (
    house_sync,
    pack_suggestion,
    pack_update,
    set_suggestion,
    set_update,
    shared_update,
)
from dmbot.rules import pack_diff, rule_sets, set_diff

BOLD = re.compile(r"(?<!\\)\*\*")  # a bold marker the writer did not escape
RULE = "**Flanking:** " + "an attacker with an ally on the far side gets advantage, " * 3


def odd_bold_lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if len(BOLD.findall(line)) % 2]


def rules(count: int = 30) -> list[str]:
    return [(f"{n}. {RULE}")[:200] for n in range(count)]


def set_changes() -> list[set_diff.Change]:
    return [
        set_diff.Change("added", n, None, {"number": n, "rule": RULE, "instead": None})
        for n in range(30)
    ]


class SharedMessageCuts(unittest.IsolatedAsyncioTestCase):
    def check(self, text: str) -> None:
        self.assertLessEqual(len(text), MESSAGE_MAX)
        self.assertEqual(odd_bold_lines(text), [], "a line ends inside bold")

    def test_house_sync_reasons_and_review(self) -> None:
        self.check(house_sync.couldnt_read(RULE * 40))

    def test_pack_update_changes(self) -> None:
        changes = [
            pack_diff.Change(
                "added", f"k{n}", None, {"name": f"Entry {n}", "text": RULE, "summary": None}
            )
            for n in range(30)
        ]
        self.check(pack_update.changes_text("Frost Pack", 3, changes))

    def test_set_update_changes_and_comparison(self) -> None:
        self.check(set_update.changes_text("Frost Set", 3, set_changes()))
        pairs = [
            (
                set_diff.Change(
                    "changed",
                    n,
                    {"number": n, "rule": RULE, "instead": None},
                    {"number": n, "rule": RULE, "instead": None},
                ),
                cast(Any, SimpleNamespace(rule=RULE)),
            )
            for n in range(30)
        ]
        self.check(set_update.compare_text("Frost Set", pairs))

    def test_the_combined_update_note(self) -> None:
        def update(n: int) -> shared_update.Update:
            nothing: Callable[..., Any] = lambda *a: []  # noqa: E731
            return shared_update.Update(
                line=f"Frost Set {n}: 3 rules changed",
                single_text="x",
                single_view=cast(Any, nothing),
                buttons=cast(Any, nothing),
                extras=[RULE] * 4,
            )

        collected = shared_update.Collected(updates=[update(n) for n in range(5)], gone=[RULE] * 5)
        self.check(shared_update.combined_text(collected))

    async def test_a_suggestion_preview_to_the_set(self) -> None:
        sent: list[str] = []

        async def tell(_interaction: Any, text: str, **_: Any) -> None:
            sent.append(text)

        sets = SimpleNamespace(
            current_content=AsyncMock(
                return_value=(SimpleNamespace(name="Frost Set", version=2), {"rules": []})
            )
        )
        bot = SimpleNamespace(rule_sets=sets)
        interaction = SimpleNamespace(client=bot)
        campaign = SimpleNamespace(guild_id=1, id="c")
        asked = SimpleNamespace(rule=RULE, instead=None)
        with (
            patch.object(set_suggestion, "_tell", tell),
            patch.object(set_suggestion, "_bot", lambda _i: bot),
            patch.object(rule_sets, "suggested_content", lambda *a: {}),
            patch.object(set_diff, "diff", lambda *a: set_changes()),
        ):
            await set_suggestion._see(cast(Any, interaction), cast(Any, campaign), cast(Any, asked))
        self.assertTrue(sent)
        self.check(sent[0])

    async def test_a_suggestion_preview_to_the_pack(self) -> None:
        sent: list[str] = []

        async def tell(_interaction: Any, text: str, **_: Any) -> None:
            sent.append(text)

        bot = SimpleNamespace(homebrew=None)
        campaign = SimpleNamespace(guild_id=1, id="c")
        asked = SimpleNamespace(name="N" * 80, kind="spell", text=RULE * 30)
        with (
            patch.object(pack_suggestion, "_tell", tell),
            patch.object(pack_suggestion, "_bot", lambda _i: bot),
        ):
            await pack_suggestion._see(
                cast(Any, SimpleNamespace()), cast(Any, campaign), cast(Any, asked)
            )
        self.assertTrue(sent)
        self.check(sent[0])

    def test_the_check_sees_an_open_bold(self) -> None:
        self.assertEqual(odd_bold_lines("fine **x**\nbroken **x"), ["broken **x"])
        self.assertEqual(odd_bold_lines("escaped \\*\\*x"), [])
