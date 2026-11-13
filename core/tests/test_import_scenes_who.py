"""An imported session gets its scenes, then its "who is this" step (#3602's ruling, #3596): in
that order, each under its own words in the import's AI feed, with the people the import recorded
(asked again each time, renewing the import's claim), and neither failing stops the other. A
stand-in with the steps canned; no database, no Discord."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot import ai_feed
from dmbot.bot import IMPORT_SCENES, IMPORT_WHO, DMBot

READY = SimpleNamespace(campaign_id="c1", session_id="s" * 32)
CAMPAIGN = SimpleNamespace(dm_user_ids=(7, 9))
READY_C = SimpleNamespace(campaign_id="c", session_id="s")  # (`test_who_wiring`'s ids)


class ImportScenesAndWho(unittest.IsolatedAsyncioTestCase):
    def fake(self, scenes: Any, who: Any) -> Any:
        return SimpleNamespace(
            import_job=SimpleNamespace(store=SimpleNamespace(renew_scan=AsyncMock())),
            consent=SimpleNamespace(recorded_in=AsyncMock(return_value=frozenset({8}))),
            _write_scenes=scenes,
            _write_who=who,
            _walk_story_chain=AsyncMock(),  # (#4173 slice 4: its own step, tested elsewhere)
        )

    async def run_it(self, me: Any) -> None:
        token = ai_feed._current.set(ai_feed.JobContext(5, "c1", "import", "s" * 32, ""))
        try:
            await DMBot._import_scenes_and_who(me, 5, READY, CAMPAIGN)  # type: ignore[arg-type]
        finally:
            ai_feed._current.reset(token)

    async def test_scenes_then_who_with_the_people_the_import_recorded(self) -> None:
        seen: list[tuple[str, str, Any]] = []

        def step(name: str) -> Any:
            async def run(
                gid: int, cid: str, sid: str, dm_ids: set[int], consenting: Any, renew: Any
            ) -> None:
                ctx = ai_feed._current.get()
                assert ctx is not None
                seen.append((name, ctx.step, (gid, cid, sid, dm_ids, await consenting())))

            return run

        me = self.fake(step("scenes"), step("who"))
        await self.run_it(me)
        args = (5, "c1", "s" * 32, {7, 9}, frozenset({8}))
        self.assertEqual(seen, [("scenes", IMPORT_SCENES, args), ("who", IMPORT_WHO, args)])
        me.consent.recorded_in.assert_awaited_with(5, "c1")
        me.consent.recorded_in.assert_awaited_with(5, "c1")
        self.assertFalse(me.import_job.store.renew_scan.await_count)  # (only after a counted call)

    async def test_a_failed_scenes_step_still_runs_who(self) -> None:
        who = AsyncMock()
        me = self.fake(AsyncMock(side_effect=RuntimeError("down")), who)
        with self.assertLogs("dmbot.bot", "WARNING") as logs:
            await self.run_it(me)
        who.assert_awaited_once()
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("down", logs.output[0])  # (the type only)

    async def test_a_failed_who_step_does_not_stop_the_import(self) -> None:
        me = self.fake(AsyncMock(), AsyncMock(side_effect=RuntimeError("down")))
        with self.assertLogs("dmbot.bot", "WARNING"):
            await self.run_it(me)  # (no exception)


class TheRealWhoStep(unittest.IsolatedAsyncioTestCase):
    """`_write_who` itself, on `test_who_wiring`'s stand-in: the import's recorded people are
    the ones read (here someone the live list doesn't have), and each counted call renews the
    import's claim."""

    async def test_the_import_s_recorded_people_are_read_and_its_claim_renewed(self) -> None:
        from tests import test_who_wiring as wiring

        helper = wiring.Asking("test_nothing_is_applied_unless_the_apply_switch_is_on")
        me = helper.fake()
        me.consent = SimpleNamespace(
            consenting=AsyncMock(return_value=frozenset()),  # (nobody agreed live)
            recorded_in=AsyncMock(return_value=frozenset({8})),  # (the import recorded 8)
        )
        me.import_job = SimpleNamespace(store=SimpleNamespace(renew_scan=AsyncMock()))
        me._write_scenes = AsyncMock()
        me._walk_story_chain = AsyncMock()
        me._write_who = lambda *a: DMBot._write_who(me, *a)
        with helper.patches():
            await DMBot._import_scenes_and_who(me, 1, READY_C, SimpleNamespace(dm_user_ids=(7,)))  # type: ignore[arg-type]
            run = helper.step_run
        run.assert_awaited_once()
        (pending,) = run.await_args.args[2]
        self.assertEqual(pending.item.speaker_ids, (8,))
        kw = run.await_args.kwargs
        self.assertEqual(await kw["consenting"](), frozenset({8}))
        self.assertFalse(me.consent.consenting.await_count)
        self.assertFalse(me.import_job.store.renew_scan.await_count)
        await kw["count_call"]()
        me.import_job.store.renew_scan.assert_awaited_once()
        self.assertEqual(me.import_job.store.renew_scan.await_args.args[:2], (1, "s"))


if __name__ == "__main__":
    unittest.main()
