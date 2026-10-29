"""The three-pass structured-output adapter (#2714) with fake tier clients: a pass, an escalation,
a fall back, a cut draft, a refusal. Invented fixtures only."""

from __future__ import annotations

import json
import unittest
from typing import Any

from dmbot import ai_structured as st
from dmbot.ai import AIError, Reply

SCHEMA: dict[str, Any] = {"type": "object"}


def validate(raw: str) -> list[int] | None:
    """A step's own check: the JSON must hold a list of numbers under "n"."""
    try:
        data = json.loads(raw)
        n = data["n"]
    except (ValueError, KeyError, TypeError):
        return None
    return n if isinstance(n, list) and all(isinstance(i, int) for i in n) else None


class Fake:
    """A tier client that answers from a list and remembers what it was asked."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str, int, dict[str, Any] | None]] = []

    async def complete(
        self,
        system: str,
        text: str,
        *,
        max_tokens: int = 8000,
        schema: dict[str, Any] | None = None,
    ) -> Reply:
        self.calls.append((system, text, max_tokens, schema))
        return self.replies.pop(0)


GOOD = Reply('{"n":[1,2]}', False)
BAD = Reply("{oops", False)


async def run(
    draft: Fake, fmt: Fake, escalate: Fake | None, counts: st.Counts
) -> st.Result[list[int]]:
    return await st.structured(
        "Draft it.", "the input", SCHEMA, validate,
        draft=draft, fmt=fmt, escalate=escalate, counts=counts, stage="test",
    )  # fmt: skip


class TheAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_a_good_first_format_pass_is_the_answer_and_costs_two_calls(self) -> None:
        draft, fmt, big, c = Fake(Reply("1 and 2", False)), Fake(GOOD), Fake(), st.Counts()
        r = await run(draft, fmt, big, c)
        self.assertEqual((r.value, r.status, r.passes), ([1, 2], st.OK, 2))
        self.assertEqual(big.calls, [])  # the stronger tier was not needed
        self.assertEqual((c.runs, c.passes, c.escalations, c.fallbacks), (1, 2, 0, 0))

    async def test_the_draft_is_the_only_input_of_the_format_pass_and_it_asks_for_the_schema(
        self,
    ) -> None:
        draft, fmt = Fake(Reply("secret draft words", False)), Fake(GOOD)
        await run(draft, fmt, None, st.Counts())
        ((system, text, tokens, schema),) = fmt.calls
        self.assertEqual((text, schema), ("secret draft words", SCHEMA))
        self.assertNotIn("the input", system + text)  # the transcript never reaches pass 2
        self.assertGreaterEqual(draft.calls[0][2], 16_000)  # the background floor (CLAUDE.md)
        self.assertGreaterEqual(tokens, 16_000)

    async def test_a_failed_check_is_redone_once_on_the_stronger_tier(self) -> None:
        draft, fmt, big, c = Fake(Reply("d", False)), Fake(BAD), Fake(GOOD), st.Counts()
        r = await run(draft, fmt, big, c)
        self.assertEqual((r.value, r.status, r.passes), ([1, 2], st.ESCALATED, 3))
        self.assertEqual(big.calls[0][1], "d")  # the same draft, not a new one
        self.assertEqual((c.escalations, c.schema_failures, c.fallbacks), (1, 1, 0))

    async def test_two_failures_fall_back_to_the_callers_own_way(self) -> None:
        c = st.Counts()
        r = await run(Fake(Reply("d", False)), Fake(BAD), Fake(Reply("[]", False)), c)
        self.assertEqual((r.value, r.status, r.structured), (None, st.NOT_STRUCTURED, False))
        self.assertEqual((c.escalations, c.schema_failures, c.fallbacks), (1, 2, 1))

    async def test_without_a_stronger_tier_one_failure_falls_back(self) -> None:
        r = await run(Fake(Reply("d", False)), Fake(BAD), None, st.Counts())
        self.assertEqual((r.status, r.passes), (st.NOT_STRUCTURED, 2))

    async def test_a_cut_draft_is_reported_as_cut_and_never_formatted(self) -> None:
        fmt, c = Fake(), st.Counts()
        r = await run(Fake(Reply("half a", True)), fmt, Fake(), c)
        self.assertEqual((r.status, r.value, r.passes), (st.CUT, None, 1))
        self.assertEqual(fmt.calls, [])
        self.assertEqual((c.cuts, c.fallbacks), (1, 1))

    async def test_a_cut_format_answer_counts_as_failed_not_as_empty(self) -> None:
        cut = Reply('{"n":[1,2', True)
        r = await run(Fake(Reply("d", False)), Fake(cut), Fake(GOOD), st.Counts())
        self.assertEqual(r.status, st.ESCALATED)

    async def test_format_answers_that_were_cut_twice_are_reported_as_cut(self) -> None:
        cut, c = Reply('{"n":[1,2', True), st.Counts()
        r = await run(Fake(Reply("d", False)), Fake(cut), Fake(cut), c)
        self.assertEqual((r.status, c.cuts, c.fallbacks), (st.CUT, 1, 1))

    async def test_a_refused_draft_stops_everything_and_is_counted(self) -> None:
        c, fmt = st.Counts(), Fake()
        r = await run(Fake(Reply("no", False, refused=True)), fmt, Fake(), c)
        self.assertEqual((r.status, fmt.calls, c.refusals), (st.REFUSED, [], 1))

    async def test_a_refusal_by_the_fast_formatter_goes_to_the_stronger_tier(self) -> None:
        r = await run(
            Fake(Reply("d", False)), Fake(Reply("x", False, refused=True)), Fake(GOOD), st.Counts()
        )
        self.assertEqual((r.value, r.status), ([1, 2], st.ESCALATED))
        c = st.Counts()
        both = Reply("x", False, refused=True)
        r = await run(Fake(Reply("d", False)), Fake(both), Fake(both), c)
        self.assertEqual((r.status, c.refusals), (st.REFUSED, 1))

    async def test_an_empty_draft_is_reported_and_never_formatted(self) -> None:
        fmt, c = Fake(), st.Counts()
        r = await run(Fake(Reply("  \n", False)), fmt, Fake(), c)
        self.assertEqual((r.status, fmt.calls, c.empties, c.fallbacks), (st.EMPTY, [], 1, 1))

    async def test_a_service_error_passes_through_and_is_counted(self) -> None:
        class Broken(Fake):
            async def complete(self, *a: Any, **k: Any) -> Reply:
                raise AIError("down")

        c = st.Counts()
        with self.assertRaises(AIError):
            await run(Broken(), Fake(), None, c)
        self.assertEqual((c.runs, c.errors), (1, 1))

    async def test_the_log_and_the_counts_hold_no_words(self) -> None:
        c = st.Counts()
        with self.assertLogs("dmbot.ai_structured", level="INFO") as logged:
            await run(Fake(Reply("words from a session", False)), Fake(GOOD), None, c)
        text = " ".join(logged.output)
        for word in ("words from a session", "the input", "Draft it", "[1, 2]"):
            self.assertNotIn(word, text)
        self.assertTrue(all(isinstance(v, int) for v in c.as_dict().values()))


if __name__ == "__main__":
    unittest.main()
