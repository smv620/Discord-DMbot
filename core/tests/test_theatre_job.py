"""The DM's "Make a script" job (#3244, slice 5b) against a real Postgres, with a stand-in AI and a
stand-in card. Invented names."""

from __future__ import annotations

import asyncio
from typing import Any

from dmbot.transcript import theatre_job as job
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.theatre_runs import TheatreRuns
from dmbot.transcript.theatre_store import TheatreStore
from tests.test_memory_names import GUILD, NamesTest
from tests.test_theatre_run import DM_USER, KNOWN, MIRA, MS, NAMES, START, TAVIN, Replies, ok


class TheJob(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.session = await TranscriptStore(self.db).open_session(GUILD, self.campaign.id, START)
        await TranscriptStore(self.db).add_lines(
            GUILD,
            self.session,
            [
                Line(MS + 1000, TAVIN, "ya so", "Yes."),
                Line(MS + 2000, DM_USER, "charges", "How many charges?"),
                Line(MS + 3000, MIRA, "I nod", "I nod."),
            ],
        )
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_lines WHERE session_id = %s ORDER BY started_ms",
                (self.session,),
            )
            self.ids = [int(r["id"]) for r in await cur.fetchall()]
        self.shown: list[str] = []
        self.everyone = {TAVIN, DM_USER, MIRA}

    async def edit(self, text: str) -> None:
        self.shown.append(text)

    def answer(self) -> str:
        a, b, c = self.ids
        return f"{a} | Tavin: Yes, [Kell].\n{b} | DM: How many charges?\n{c} | Mira: I nod."

    async def make(self, ask: Any, later: set[int] | None = None, **kw: Any) -> job.Outcome:
        async def still() -> set[int]:
            return later if later is not None else self.everyone

        return await job.make_script(
            self.db, ask, GUILD, self.session, agreed=self.everyone, names=NAMES, known=KNOWN,
            still_agreed=still, edit=self.edit, **kw,
        )  # fmt: skip

    async def test_a_run_ends_the_card_on_what_was_kept(self) -> None:
        out = await self.make(Replies(ok(self.answer())))
        self.assertTrue(out.ok)
        self.assertEqual(
            self.shown[-1], "Script ready: 3 lines from 1 parts. Only you can read it."
        )
        self.assertEqual(len(await TheatreStore(self.db).read(GUILD, self.session)), 3)

    async def test_a_second_press_is_refused_and_changes_nothing(self) -> None:
        await TheatreRuns(self.db).begin(GUILD, self.session)  # (a run is going)
        ask = Replies()
        out = await self.make(ask)
        self.assertEqual((out.text, out.started), (job.ALREADY, False))
        self.assertEqual((ask.asked, self.shown), ([], []))

    async def test_a_failure_keeps_the_old_script_says_so_and_lets_the_next_press_start(
        self,
    ) -> None:
        await self.make(Replies(ok(self.answer())))
        old = await TheatreStore(self.db).read(GUILD, self.session)
        out = await self.make(Replies(RuntimeError("down")))
        self.assertEqual((out.text, out.ok), (job.FAILED, False))
        self.assertEqual(self.shown[-1], job.FAILED)
        self.assertEqual(await TheatreStore(self.db).read(GUILD, self.session), old)
        again = await self.make(Replies(ok(self.answer())))
        self.assertTrue(again.started and again.ok)

    async def test_a_person_who_stopped_has_no_line_in_the_script(self) -> None:
        out = await self.make(Replies(ok(self.answer())), later={TAVIN, DM_USER})
        self.assertEqual(await TheatreStore(self.db).read(GUILD, self.session), [])
        self.assertIn("Nothing was changed", out.text)

    async def test_the_card_shows_the_stage_and_the_part(self) -> None:
        async def tick(_: float) -> None:
            await asyncio.sleep(0)

        class Slow(Replies):
            async def __call__(self, instructions: str, question: str) -> Any:
                for _ in range(5):
                    await asyncio.sleep(0.01)
                return await super().__call__(instructions, question)

        await self.make(Slow(ok(self.answer())), every=0.0, sleep=tick)
        card = "\n".join(self.shown)
        self.assertIn(job.TITLE, card)
        self.assertIn("Reading the session", card)

    def test_the_cost_line_says_about_how_many_calls(self) -> None:
        self.assertEqual(job.estimate_line(100), "This will take about 1 AI call.")
        self.assertEqual(job.estimate_line(151), "This will take about 2 AI calls.")
        self.assertEqual(job.estimate_calls(0), 1)
