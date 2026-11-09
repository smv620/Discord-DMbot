"""The bot's importance step (#3172, slice 3): it runs only where the story extractor runs, inside
the extractor's limits with the people recorded now, and a failure never escapes.
`DMBot._read_importance` on a stand-in with the parts canned; the step's own rules are in
`test_importance_read`. Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dmbot.bot import DMBot
from dmbot.memory import importance_read as ir
from dmbot.memory import importance_step, role_match


class Wiring(unittest.IsolatedAsyncioTestCase):
    def fake(self, **settings: Any) -> Any:
        transcripts = SimpleNamespace(
            session=AsyncMock(return_value=SimpleNamespace(started_at=9)),
            extraction=AsyncMock(return_value=(0, 470)),  # (10 calls left of 480)
            extractor_calls_since=AsyncMock(return_value=0),
            record_extraction=AsyncMock(),
        )
        return SimpleNamespace(
            settings=SimpleNamespace(**{
                "story_extractor_on": True, "story_extractor_max_calls": 480,
                "story_extractor_max_calls_day": 960, "role_match_on": False,
                "voyage_embed_model": "v", **settings,
            }),
            extractor_ai=SimpleNamespace(model="m"),
            memory=SimpleNamespace(_db=object(), mentions=AsyncMock(return_value=[])),
            transcripts=transcripts,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="c"))),
            plan_allows=AsyncMock(return_value=True),
            consent=SimpleNamespace(consenting=AsyncMock(return_value=frozenset({8}))),
            voyage=None, role_seeds=role_match.SeededVectors(),
            meaning_calls=SimpleNamespace(count=AsyncMock()),
        )  # fmt: skip

    async def test_off_means_off(self) -> None:
        me = self.fake(story_extractor_on=False)
        with patch.object(importance_step, "gather", AsyncMock()) as gather:
            await DMBot._read_importance(me, 1, "c", "s")
        self.assertFalse(gather.await_count)
        self.assertFalse(me.campaigns.get.await_count)

    async def test_it_asks_inside_the_extractor_s_limit_with_who_is_recorded(self) -> None:
        me = self.fake()
        items = [ir.Item(1, "e" * 32, "Vex", "character", 1)]
        canned = AsyncMock(return_value=(items, {}, {}))
        with patch.object(importance_step, "gather", canned) as g, \
                patch.object(importance_step, "run", AsyncMock(
                    return_value=importance_step.Report())) as run:  # fmt: skip
            await DMBot._read_importance(me, 1, "c", "s")
        assert g.await_args is not None and run.await_args is not None
        self.assertEqual(g.await_args.kwargs["agreed"], frozenset({8}))
        self.assertEqual(g.await_args.kwargs["session_started_at"], 9)
        self.assertEqual(run.await_args.kwargs["max_calls"], 10)

    async def test_an_import_s_own_list_of_who_was_recorded_is_used(self) -> None:
        # (an import passes `recorded_in`, Supervisor on #3918: the server's list isn't asked)
        me = self.fake()
        items = [ir.Item(1, "e" * 32, "Vex", "character", 1)]
        recorded = AsyncMock(return_value=frozenset({8, -3}))
        canned = AsyncMock(return_value=(items, {}, {}))
        with patch.object(importance_step, "gather", canned) as g, \
                patch.object(importance_step, "run", AsyncMock(
                    return_value=importance_step.Report())) as run:  # fmt: skip
            await DMBot._read_importance(me, 1, "c", "s", consenting=recorded)
        assert g.await_args is not None and run.await_args is not None
        self.assertEqual(g.await_args.kwargs["agreed"], frozenset({8, -3}))
        self.assertIs(run.await_args.kwargs["consenting"], recorded)
        me.consent.consenting.assert_not_awaited()

    async def test_no_calls_left_reads_nothing(self) -> None:
        me = self.fake()
        me.transcripts.extraction = AsyncMock(return_value=(0, 480))
        with patch.object(importance_step, "gather", AsyncMock()) as gather:
            await DMBot._read_importance(me, 1, "c", "s")
        self.assertFalse(gather.await_count)

    async def test_an_import_s_claim_is_renewed_through_the_step(self) -> None:
        me = self.fake()
        renew = AsyncMock()
        items = [ir.Item(1, "e" * 32, "Vex", "character", 1)]
        with patch.object(importance_step, "gather", AsyncMock(return_value=(items, {}, {}))), \
                patch.object(importance_step, "run", AsyncMock(
                    return_value=importance_step.Report())) as run:  # fmt: skip
            await DMBot._read_importance(me, 1, "c", "s", renew=renew)
        assert run.await_args is not None
        self.assertIs(run.await_args.kwargs["renew"], renew)

    async def test_new_roles_are_matched_only_with_the_switch_and_voyage(self) -> None:
        items = [ir.Item(1, "e" * 32, "Vex", "character", 1)]
        for on, voyage, wanted in ((False, object(), False), (True, None, False),
                                   (True, object(), True)):  # fmt: skip
            me = self.fake(role_match_on=on)
            me.voyage = voyage
            with patch.object(importance_step, "gather", AsyncMock(return_value=(items, {}, {}))), \
                    patch.object(importance_step, "run", AsyncMock(
                        return_value=importance_step.Report())) as run:  # fmt: skip
                await DMBot._read_importance(me, 1, "c", "s")
            assert run.await_args is not None
            matcher = run.await_args.kwargs["matcher"]
            self.assertEqual(isinstance(matcher, role_match.RoleMatcher), wanted, (on, voyage))

    async def test_a_failure_never_escapes(self) -> None:
        me = self.fake()
        with patch.object(importance_step, "gather", AsyncMock(side_effect=RuntimeError("x"))):
            await DMBot._read_importance(me, 1, "c", "s")  # (logged by type, not raised)


if __name__ == "__main__":
    unittest.main()
