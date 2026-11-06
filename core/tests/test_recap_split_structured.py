"""The recap's opening split through the structured-output adapter (#2714, slice 2): a strong
model drafts the labels, a fast one fills the schema, the same check decides; off by default, and
when it gives nothing the one call of before is made. Invented fixtures only."""

from __future__ import annotations

import unittest
from typing import Any, ClassVar

from dmbot.ai import AIError, Reply
from dmbot.config import ConfigError, load_settings
from dmbot.recaps import pipeline as pl
from dmbot.recaps import split as sp
from tests import recap_fixtures as fx
from tests import test_recap_split as st
from tests.test_recap_jobs import WorkerHarness

DRAFT = "talk: last line 2\nrecap: last line 7"


class TheStructuredSplit(WorkerHarness):
    def story(self) -> Any:
        return st.reading([st.TALK, st.READ_ALOUD, [t for _, t in fx.LINES]])

    def rest(self) -> list[str]:
        offset = len(st.TALK) + len(st.READ_ALOUD)
        return [st.beats_after(offset), fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER]

    async def start(
        self, fast: fx.Canned, careful: fx.Canned, *, on: bool = True, **kw: Any
    ) -> None:
        await self.setUpWorker(
            fast, careful, lines=self.story(), earlier=st.EARLIER, names=[*fx.NAMES], **kw
        )
        self.worker._structured_split = on

    def labels(self, job: Any) -> list[str]:
        split = sp.from_json(job.split)
        assert split is not None
        return [p.label for p in split.parts]

    async def test_a_strong_draft_then_a_fast_form_give_the_same_split(self) -> None:
        fast = fx.Canned(st.SPLIT_ANSWER, *self.rest())
        careful = fx.Canned(DRAFT)
        await self.start(fast, careful)
        job = await self.go()
        self.assertEqual((job.state, self.labels(job)), ("ready", ["talk", "recap", "play"]))
        self.assertEqual(careful.systems[0], sp.draft_system())  # the draft: the step's own prompt
        self.assertIn("<opening>", careful.sent[0])
        self.assertEqual(fast.schemas[0], sp.SCHEMA)  # the form pass asked for the schema
        self.assertEqual(fast.sent[0], DRAFT)  # and saw only the draft, never the opening
        self.assertNotIn("<opening>", fast.sent[0])

    async def test_it_is_off_unless_switched_on(self) -> None:
        fast = fx.Canned(st.SPLIT_ANSWER, *self.rest())
        careful = fx.Canned()
        await self.start(fast, careful, on=False)
        job = await self.go()
        self.assertEqual((job.state, careful.sent), ("ready", []))
        self.assertIn("<opening>", fast.sent[0])
        self.assertEqual(job.calls, 6)  # the usual: the split is one call

    async def test_a_form_that_fails_twice_goes_back_to_the_one_call_of_before(self) -> None:
        fast = fx.Canned("{oops", st.SPLIT_ANSWER, *self.rest())
        careful = fx.Canned(DRAFT, "{still oops")
        await self.start(fast, careful)
        job = await self.go()
        self.assertEqual((job.state, self.labels(job)), ("ready", ["talk", "recap", "play"]))
        self.assertIn("<opening>", fast.sent[1])  # the legacy call, with the opening

    async def test_a_failed_call_waits_the_job_and_makes_no_split(self) -> None:
        careful = fx.Canned(AIError("down"))
        await self.start(fx.Canned(*self.rest()), careful)
        job = await self.go()
        self.assertEqual((job.errors, job.split, job.state), (1, None, "queued"))

    async def test_every_call_of_the_new_way_is_counted_against_the_recap(self) -> None:
        fast, careful = fx.Canned(st.SPLIT_ANSWER, *self.rest()), fx.Canned(DRAFT)
        await self.start(fast, careful)
        job = await self.go()
        # a draft and a form where there was one call: 6 become 7 (usually one more; a form
        # redone on the stronger tier is two more, the one call after both fail is three)
        self.assertEqual(job.calls, 7)

    async def test_a_player_who_stops_being_recorded_meanwhile_starts_it_again(self) -> None:
        agreed = {fx.DM, fx.ALICE, fx.BOB}

        async def consenting() -> Any:
            return set(agreed)

        class Leaving(fx.Canned):
            async def complete(self, *a: Any, **k: Any) -> Any:
                agreed.discard(fx.ALICE)  # while the AI answers
                return await super().complete(*a, **k)

        await self.start(fx.Canned(st.SPLIT_ANSWER), Leaving(DRAFT), consenting=consenting)
        job = await self.go()
        self.assertEqual((job.state, job.restarts, job.split), ("queued", 1, None))

    async def test_a_fast_form_that_fails_is_redone_by_the_stronger_tier(self) -> None:
        fast = fx.Canned("{oops", *self.rest())
        careful = fx.Canned(DRAFT, st.SPLIT_ANSWER)
        await self.start(fast, careful)
        job = await self.go()
        self.assertEqual((job.state, self.labels(job)), ("ready", ["talk", "recap", "play"]))
        self.assertEqual(careful.sent[1], DRAFT)  # the same draft, formatted again
        self.assertEqual(job.calls, 7 + 1)

    async def test_a_cut_draft_goes_back_to_the_one_call_of_before(self) -> None:
        cut = Reply("talk: last li", True)
        fast = fx.Canned(st.SPLIT_ANSWER, *self.rest())
        await self.start(fast, fx.Canned(cut))
        job = await self.go()
        self.assertEqual((job.state, self.labels(job)), ("ready", ["talk", "recap", "play"]))
        self.assertIn("<opening>", fast.sent[0])  # no form pass on a draft with its end missing

    async def test_a_draft_of_none_means_all_play(self) -> None:
        fast = fx.Canned('{"parts":[]}', *self.rest())
        await self.start(fast, fx.Canned("none"))
        job = await self.go()
        self.assertEqual((job.state, self.labels(job)), ("ready", ["play"]))

    async def test_a_form_call_that_fails_waits_the_job(self) -> None:
        fast = fx.Canned(AIError("down"))
        await self.start(fast, fx.Canned(DRAFT))
        job = await self.go()
        self.assertEqual((job.errors, job.split, job.state), (1, None, "queued"))

    async def test_a_recap_at_its_call_limit_is_stopped_between_the_split_calls(self) -> None:
        fast, careful = fx.Canned(st.SPLIT_ANSWER, *self.rest()), fx.Canned(DRAFT)
        await self.start(fast, careful)
        await self.store.update(1, self.one.id, 100, calls=pl.MAX_CALLS - 1)
        job = await self.go()
        self.assertEqual((job.state, job.split), ("failed", None))
        self.assertEqual(fast.sent, [])  # the draft used the last call; no form was asked for

    def test_the_settings_reach_the_worker(self) -> None:
        import inspect

        from dmbot import bot

        self.assertIn(
            "structured_split=settings.recap_structured_output_split", inspect.getsource(bot)
        )


class TheDraftPrompt(unittest.TestCase):
    def test_it_asks_the_same_question_in_plain_lines(self) -> None:
        rules = sp.SYSTEM.split("\nRules:", 1)[1]
        self.assertTrue(sp.DRAFT_SYSTEM.endswith("\nRules:" + rules))  # every rule, as it is
        for label in ("talk", "catchup", "recap"):
            self.assertIn(label, sp.DRAFT_SYSTEM)
        self.assertNotIn("JSON", sp.DRAFT_SYSTEM)
        self.assertNotIn('"parts"', sp.DRAFT_SYSTEM)
        self.assertEqual(sp.draft_system(), sp.DRAFT_SYSTEM)


class TheSetting(unittest.TestCase):
    BASE: ClassVar[dict[str, str]] = {
        "DISCORD_TOKEN": "x" * 60,
        "EARS_SHARED_SECRET": "s",
        "DATABASE_URL": "postgres://x",
    }

    def test_it_is_off_by_default_and_reads_on_and_off(self) -> None:
        self.assertFalse(load_settings(self.BASE).recap_structured_output_split)
        for raw, want in (("1", True), ("on", True), ("TRUE", True), ("0", False), ("", False)):
            got = load_settings({**self.BASE, "RECAP_STRUCTURED_OUTPUT_SPLIT": raw})
            self.assertEqual(got.recap_structured_output_split, want, raw)

    def test_a_bad_value_stops_start_up_naming_the_setting(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            load_settings({**self.BASE, "RECAP_STRUCTURED_OUTPUT_SPLIT": "maybe"})
        self.assertIn("RECAP_STRUCTURED_OUTPUT_SPLIT", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
