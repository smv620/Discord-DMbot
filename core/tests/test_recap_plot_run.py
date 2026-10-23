"""Running PlotBot's check (#2122): which ready recaps wait for one, the one call, the findings
written for the DM, "Not a problem" kept, a changed text never given an old answer, and a check
that cannot run being given up. The AI is a stand-in that returns what it is told to."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dmbot.memory.plot_check import Earlier, parse
from dmbot.recaps.accepted import AcceptedStore
from dmbot.recaps.plot import PER_HOUR, STALE_S, TRIES, PlotRunner, PlotStore
from tests.test_recap_plot import PlotBase
from tests.test_recap_web import NOW
from tests.test_web_api import THURSDAY

FELL = "Ulfgar the dwarf fell in the vault."


class Ai:
    def __init__(self, *issues: Any, text: str | None = None, refused: bool = False) -> None:
        self.reply = text if text is not None else json.dumps({"issues": list(issues)})
        self.refused, self.calls, self.sent = refused, 0, ""

    async def complete(self, system: str, text: str, **kw: Any) -> Any:
        self.calls += 1
        self.sent = text
        return SimpleNamespace(text=self.reply, refused=self.refused, cut=False)


class Running(PlotBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.plots = PlotStore(self.db, clock=lambda: NOW)
        self.accepted = AcceptedStore(self.db)
        self.job = await self.state(self.one.id)
        # (the second campaign's recap is out of the way: these tests are about the first)
        await self.store.update(THURSDAY.id, self.two.id, 100, state="prose")

    def runner(self, ai: Ai | None) -> PlotRunner:
        return PlotRunner(self.plots, self.accepted, ai)

    async def earlier(self, text: str = FELL, at: int = 50, campaign: str | None = None) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO recap_accepted (guild_id, campaign_id, session_at, version,"
                " text_hash, title, summary, accepted_by, accepted_at)"
                " VALUES (%s, %s, %s, 1, 'x', 'The Vault', %s, 1, %s)",
                (THURSDAY.id, campaign or self.one.id, at, text, NOW),
            )

    def finding(self, **over: Any) -> dict[str, Any]:
        sentence = self.sentences()[0]
        return {
            "kind": "dead_acts", "level": "likely", "sentence": 1,
            "reason": f"About {sentence[:20]}", "earlier": 1,
            "quote": "Ulfgar the dwarf fell in the vault", **over,
        }  # fmt: skip

    def sentences(self) -> list[str]:
        from dmbot.recaps.draft import sentences_of

        return sentences_of(self.job.summary)

    async def rows(self, table: str) -> list[Any]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3")
            return list(await cur.fetchall())

    async def test_a_ready_recap_is_checked_and_its_findings_reach_the_page(self) -> None:
        await self.earlier()
        ai = Ai(self.finding())
        self.assertEqual(await self.runner(ai).round(THURSDAY.id), 1)
        self.assertEqual(ai.calls, 1)
        [check] = await self.rows("plot_checks")
        self.assertEqual((check["state"], check["calls"]), ("done", 1))
        got = (await self.plot()).json()
        self.assertEqual((got["state"], got["approveBlocked"]), ("ready", True))
        [one] = got["issues"]
        self.assertEqual((one["kind"], one["level"], one["sentence"]), ("dead_acts", "likely", 1))
        self.assertEqual(
            one["source"], {"text": "Ulfgar the dwarf fell in the vault", "session": "The Vault"}
        )

    async def test_the_same_text_is_not_checked_twice(self) -> None:
        await self.earlier()
        ai = Ai()
        await self.runner(ai).round(THURSDAY.id)
        self.assertEqual(await self.runner(ai).round(THURSDAY.id), 0)
        self.assertEqual(ai.calls, 1)

    async def test_with_nothing_earlier_no_call_is_made_and_nothing_is_held(self) -> None:
        ai = Ai(self.finding())
        await self.runner(ai).round(THURSDAY.id)
        self.assertEqual(ai.calls, 0)
        got = (await self.plot()).json()
        self.assertEqual((got["state"], got["approveBlocked"], got["issues"]), ("ready", False, []))

    async def test_a_changed_text_is_checked_again(self) -> None:
        await self.earlier()
        ai = Ai()
        await self.runner(ai).round(THURSDAY.id)
        await self.store.update(THURSDAY.id, self.one.id, 100, summary="A different recap.")
        self.assertEqual(await self.runner(ai).round(THURSDAY.id), 1)
        self.assertEqual(ai.calls, 2)

    async def test_what_the_dm_called_not_a_problem_is_not_raised_again(self) -> None:
        await self.earlier()
        await self.runner(Ai(self.finding())).round(THURSDAY.id)
        [issue] = (await self.plot()).json()["issues"]
        await self.answer(issue["id"], "fine", "A different Ulfgar.")
        await self.store.update(THURSDAY.id, self.one.id, 100, title="A new title")
        await self.runner(Ai(self.finding())).round(THURSDAY.id)  # the same point, said again
        self.assertEqual((await self.plot()).json()["issues"], [])

    async def test_the_text_changing_during_the_call_throws_the_answer_away(self) -> None:
        await self.earlier()
        store = self.plots

        class Edits(Ai):
            async def complete(this, system: str, text: str, **kw: Any) -> Any:
                await self.store.update(THURSDAY.id, self.one.id, 100, summary="Changed meanwhile.")
                return await super().complete(system, text, **kw)

        await PlotRunner(store, self.accepted, Edits(self.finding())).round(THURSDAY.id)
        self.assertEqual(await self.rows("plot_issues"), [])
        [old] = await self.rows("plot_checks")  # kept, so the edit still counts for the hour
        self.assertEqual(old["state"], "failed")
        self.assertEqual(await self.runner(Ai()).round(THURSDAY.id), 1)  # the new text is checked

    async def test_an_answer_that_cannot_be_used_gives_the_check_up(self) -> None:
        await self.earlier()
        for ai in (Ai(text="not json"), Ai(refused=True)):
            await self.runner(ai).round(THURSDAY.id)
            [check] = await self.rows("plot_checks")
            self.assertEqual(check["state"], "failed")
            async with self.db.guild(THURSDAY.id) as conn:
                await conn.execute("DELETE FROM plot_checks")
        got = (await self.plot()).json()
        self.assertFalse(got["approveBlocked"])

    async def test_an_invented_source_never_reaches_the_dm(self) -> None:
        await self.earlier()
        await self.runner(Ai(self.finding(quote="Ulfgar lived on a farm"))).round(THURSDAY.id)
        self.assertEqual(await self.rows("plot_issues"), [])

    async def test_a_check_that_began_and_never_finished_is_tried_again_a_few_times(self) -> None:
        digest = await self.hash_of()
        await self.plots.begin(THURSDAY.id, self.one.id, 100, digest)
        self.assertEqual(await self.plots.waiting(THURSDAY.id), [])  # (still fresh)
        late = PlotStore(self.db, clock=lambda: NOW + STALE_S + 5)
        self.assertEqual(len(await late.waiting(THURSDAY.id)), 1)
        self.assertTrue(await late.begin(THURSDAY.id, self.one.id, 100, digest))
        self.assertFalse(await late.begin(THURSDAY.id, self.one.id, 100, digest))  # (fresh again)

    async def test_a_campaign_has_an_hourly_allowance(self) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            for n in range(PER_HOUR):
                await conn.execute(
                    "INSERT INTO plot_checks (guild_id, campaign_id, session_at, text_hash, state,"
                    " created_at) VALUES (%s, %s, %s, %s, 'done', %s)",
                    (THURSDAY.id, self.one.id, 900 + n, f"h{n}", NOW),
                )
        self.assertEqual(await self.plots.waiting(THURSDAY.id), [])

    async def test_a_draft_not_ready_or_already_decided_is_not_checked(self) -> None:
        await self.store.update(THURSDAY.id, self.one.id, 100, state="prose")
        self.assertEqual(await self.plots.waiting(THURSDAY.id), [])

    async def test_two_runs_of_the_same_text_write_its_issues_once(self) -> None:
        await self.earlier()
        digest = await self.hash_of()
        await self.plots.begin(THURSDAY.id, self.one.id, 100, digest)
        parsed = (
            parse(Ai(self.finding()).reply, self.sentences(), [Earlier(50, "The Vault", FELL)])
            or []
        )
        first = await self.plots.finish(THURSDAY.id, self.one.id, 100, digest, parsed, 1)
        again = await self.plots.finish(THURSDAY.id, self.one.id, 100, digest, parsed, 1)
        self.assertEqual((first, again), (1, 0))
        self.assertEqual(len(await self.rows("plot_issues")), 1)

    async def test_a_late_failure_does_not_undo_a_finished_check(self) -> None:
        digest = await self.hash_of()
        await self.plots.begin(THURSDAY.id, self.one.id, 100, digest)
        await self.plots.finish(THURSDAY.id, self.one.id, 100, digest, [], 0)
        await self.plots.fail(THURSDAY.id, self.one.id, 100, digest)
        [check] = await self.rows("plot_checks")
        self.assertEqual(check["state"], "done")

    async def test_a_check_that_never_finished_in_three_tries_is_given_up(self) -> None:
        digest = await self.hash_of()
        for n in range(TRIES):
            await PlotStore(self.db, clock=lambda n=n: NOW + n * (STALE_S + 5)).begin(  # type: ignore[misc]
                THURSDAY.id, self.one.id, 100, digest
            )
        late = PlotStore(self.db, clock=lambda: NOW + TRIES * (STALE_S + 5))
        self.assertEqual(await late.waiting(THURSDAY.id), [])  # not tried a fourth time
        await late.give_up_stale(THURSDAY.id)
        [check] = await self.rows("plot_checks")
        self.assertEqual(check["state"], "failed")

    async def test_a_call_that_hangs_gives_the_check_up(self) -> None:
        await self.earlier()

        class Hangs(Ai):
            async def complete(self, system: str, text: str, **kw: Any) -> Any:
                await asyncio.sleep(60)

        with patch("dmbot.recaps.plot.CALL_TIMEOUT_S", 0.05):
            await self.runner(Hangs()).round(THURSDAY.id)
        [check] = await self.rows("plot_checks")
        self.assertEqual(check["state"], "failed")

    async def test_only_recaps_before_this_one_are_history(self) -> None:
        await self.earlier(at=50)
        await self.earlier("Ulfgar rose again in a later session.", at=500)  # after session 100
        ai = Ai()
        await self.runner(ai).round(THURSDAY.id)
        self.assertIn("fell in the vault", ai.sent)
        self.assertNotIn("rose again", ai.sent)

    async def test_nothing_is_written_onto_a_recap_that_was_just_approved(self) -> None:
        await self.earlier()
        digest = await self.hash_of()
        await self.plots.begin(THURSDAY.id, self.one.id, 100, digest)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET status = 'posting' WHERE campaign_id = %s", (self.one.id,)
            )
        parsed = parse(Ai(self.finding()).reply, self.sentences(), [Earlier(50, "T", FELL)]) or []
        self.assertEqual(
            await self.plots.finish(THURSDAY.id, self.one.id, 100, digest, parsed, 1), 0
        )
        self.assertEqual(await self.rows("plot_issues"), [])

    async def test_nothing_is_written_onto_a_recap_approved_while_the_check_ran(self) -> None:
        await self.earlier()
        digest = await self.hash_of()
        await self.plots.begin(THURSDAY.id, self.one.id, 100, digest)
        self.assertTrue(
            await self.store.approve(THURSDAY.id, self.one.id, 100, 1, "text", expected=digest)
        )  # approved (still a 'draft', same text) while the check was running
        parsed = parse(Ai(self.finding()).reply, self.sentences(), [Earlier(50, "T", FELL)]) or []
        self.assertEqual(
            await self.plots.finish(THURSDAY.id, self.one.id, 100, digest, parsed, 1), 0
        )
        self.assertEqual(await self.rows("plot_issues"), [])
