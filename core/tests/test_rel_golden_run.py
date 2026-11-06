"""The golden-set scorer (#3244): the counts and the report, with a stand-in embedder (the real
numbers come from dev1's run with Voyage). Pure; no network."""

from __future__ import annotations

import os
import unittest
from collections.abc import Sequence
from unittest.mock import patch

from dmbot.devtools import rel_golden_run as run
from dmbot.memory import rel_golden, rel_types
from dmbot.memory.rel_type_texts import TYPES

DIM = len(TYPES)
INDEX = {t.key: i for i, t in enumerate(TYPES)}
BY_TEXT = {rel_types.embed_text(t): i for i, t in enumerate(TYPES)}
BY_DESCRIPTION = {c.description: c for c in rel_golden.CASES}


def one_hot(i: int, noise: float = 0.0) -> list[float]:
    v = [noise] * DIM
    v[i] = 1.0
    return v


async def perfect(texts: Sequence[str], as_query: bool) -> list[list[float]]:
    """A type's text is its own direction; a case points at the type it should land on; a case that
    must land on no tie points nowhere near any type."""
    if not as_query:
        return [one_hot(BY_TEXT[t]) for t in texts]
    out = []
    for text in texts:
        expected = BY_DESCRIPTION[text].expected
        out.append(one_hot(INDEX[expected], 0.01) if expected in INDEX else [0.0] * DIM)
    return out


class TheScorer(unittest.IsolatedAsyncioTestCase):
    async def test_a_perfect_embedder_gets_every_typed_case_right(self) -> None:
        r = await run.evaluate(perfect)
        typed = [c for c in rel_golden.CASES if c.expected in INDEX]
        self.assertEqual((r.right, r.wrong), (len(typed), 0))
        self.assertEqual(r.stored_wrongly, 0)  # (the none / alias cases match nothing)
        self.assertEqual(r.cases, len(rel_golden.CASES))

    async def test_the_chosen_pair_reaches_the_owners_precision(self) -> None:
        r = await run.evaluate(perfect)
        assert r.chosen is not None
        self.assertGreaterEqual(r.chosen.precision, rel_types.PRECISION_TARGET)

    async def test_a_mix_up_is_counted_by_type_pair(self) -> None:
        async def swaps_kin_and_knows(texts: Sequence[str], as_query: bool) -> list[list[float]]:
            vecs = await perfect(texts, as_query)
            if not as_query:
                return vecs
            for n, text in enumerate(texts):
                if BY_DESCRIPTION[text].expected == "kin_of":
                    vecs[n] = one_hot(INDEX["knows"], 0.01)
            return vecs

        r = await run.evaluate(swaps_kin_and_knows)
        self.assertGreater(r.wrong, 0)
        self.assertIn("kin_of -> knows", r.confusion)

    async def test_a_none_case_that_matches_a_tie_is_counted_as_stored_wrongly(self) -> None:
        async def everything_is_kin(texts: Sequence[str], as_query: bool) -> list[list[float]]:
            if not as_query:
                return [one_hot(BY_TEXT[t]) for t in texts]
            return [one_hot(INDEX["kin_of"], 0.01) for _ in texts]

        r = await run.evaluate(everything_is_kin)
        self.assertGreater(r.stored_wrongly, 0)

    async def test_the_report_has_numbers_and_no_sentence_of_the_set(self) -> None:
        text = run.report(await run.evaluate(perfect))
        for case in rel_golden.CASES:
            self.assertNotIn(case.description, text)
        self.assertIn("choose: threshold", text)

    async def test_a_missing_key_stops_with_a_message_and_prints_no_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(await run.main_async(run.parse_args([])), 2)


if __name__ == "__main__":
    unittest.main()
