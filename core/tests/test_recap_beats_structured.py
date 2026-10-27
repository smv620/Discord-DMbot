"""The recap's beat sheet through the structured-output adapter (#2714, slice 3): a strong model
drafts the beats as plain lines, a fast one fills the schema, `beats.parse` decides; off by
default, and when it gives nothing the one call of before is made. Invented fixtures only."""

from __future__ import annotations

import unittest
from typing import Any, ClassVar

from dmbot.ai import AIError, Reply
from dmbot.config import ConfigError, load_settings
from dmbot.recaps import beats as bt
from dmbot.recaps import pipeline as pl
from tests import recap_fixtures as fx
from tests.test_recap_jobs import WorkerHarness

DRAFT = "event | lines 1 | who | where Greywater Keep | The party reached Greywater Keep at dusk"


class TheStructuredBeats(WorkerHarness):
    def rest(self) -> list[str]:
        return [fx.PROSE_ANSWER, fx.GRADE_ANSWER, fx.FINISH_ANSWER]

    async def start(
        self, fast: fx.Canned, careful: fx.Canned, *, on: bool = True, **kw: Any
    ) -> None:
        await self.setUpWorker(fast, careful, **kw)
        self.worker._structured_beats = on

    async def test_a_strong_draft_then_a_fast_form_give_the_sheet(self) -> None:
        fast = fx.Canned(fx.BEATS_ANSWER, *self.rest())
        careful = fx.Canned(DRAFT)
        await self.start(fast, careful)
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(careful.systems[0], bt.draft_system(1, 1))
        self.assertIn("<transcript>", careful.sent[0])  # the draft reads the session
        self.assertEqual(fast.schemas[0], bt.SCHEMA)
        self.assertEqual(fast.sent[0], DRAFT)  # the form pass saw only the draft
        self.assertEqual(len(job.beats or []), len(fx.BEATS["beats"]))

    async def test_it_is_off_unless_switched_on(self) -> None:
        fast = fx.Canned(fx.BEATS_ANSWER, *self.rest())
        careful = fx.Canned()
        await self.start(fast, careful, on=False)
        job = await self.go()
        self.assertEqual((job.state, careful.sent), ("ready", []))
        self.assertIsNone(fast.schemas[0])

    async def test_a_form_that_fails_twice_goes_back_to_the_one_call_of_before(self) -> None:
        fast = fx.Canned("{oops", fx.BEATS_ANSWER, *self.rest())
        careful = fx.Canned(DRAFT, "{still oops")
        await self.start(fast, careful)
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertIn("<transcript>", fast.sent[1])  # the legacy call, with the session

    async def test_a_failed_call_waits_the_job(self) -> None:
        await self.start(fx.Canned(*self.rest()), fx.Canned(AIError("down")))
        job = await self.go()
        self.assertEqual((job.errors, job.state), (1, "queued"))

    async def test_every_call_of_the_new_way_is_counted_against_the_recap(self) -> None:
        fast, careful = fx.Canned(fx.BEATS_ANSWER, *self.rest()), fx.Canned(DRAFT)
        await self.start(fast, careful)
        job = await self.go()
        # a draft and a form where there was one call: 5 become 6 (the flag-off run counts 5)
        self.assertEqual(job.calls, 6)

    async def test_a_cut_draft_is_never_read_and_is_tried_again_with_twice_the_room(self) -> None:
        cut = Reply("event | lines 1 | who", True)
        fast = fx.Canned(fx.BEATS_ANSWER, *self.rest())
        careful = fx.Canned(cut, DRAFT)
        await self.start(fast, careful)
        job = await self.go()
        self.assertEqual(job.state, "ready")
        self.assertEqual(fast.sent[0], DRAFT)  # no form pass on a draft with its end missing
        self.assertEqual(careful.tokens[1], careful.tokens[0] * 2)

    async def test_a_recap_at_its_call_limit_is_stopped_between_the_calls(self) -> None:
        fast, careful = fx.Canned(fx.BEATS_ANSWER, *self.rest()), fx.Canned(DRAFT)
        await self.start(fast, careful)
        await self.store.update(1, self.one.id, 100, calls=pl.MAX_CALLS - 1)
        job = await self.go()
        self.assertEqual(job.state, "failed")
        self.assertEqual(fast.sent, [])

    async def test_a_player_who_stops_being_recorded_meanwhile_starts_it_again(self) -> None:
        agreed = {fx.DM, fx.ALICE, fx.BOB}

        async def consenting() -> Any:
            return set(agreed)

        class Leaving(fx.Canned):
            async def complete(self, *a: Any, **k: Any) -> Any:
                agreed.discard(fx.ALICE)  # while the AI answers
                return await super().complete(*a, **k)

        await self.start(fx.Canned(fx.BEATS_ANSWER), Leaving(DRAFT), consenting=consenting)
        job = await self.go()
        self.assertEqual((job.state, job.restarts), ("queued", 1))

    def test_the_settings_reach_the_worker(self) -> None:
        import inspect

        from dmbot import bot

        self.assertIn(
            "structured_beats=settings.recap_structured_output_beats", inspect.getsource(bot)
        )


class TheDraftPrompt(unittest.TestCase):
    def test_it_asks_the_same_question_in_plain_lines(self) -> None:
        rules = bt.SYSTEM.split("\nRules:", 1)[1]
        self.assertTrue(bt.DRAFT_SYSTEM.endswith("\nRules:" + rules))  # every rule, as it is
        self.assertNotIn("JSON", bt.DRAFT_SYSTEM)
        self.assertNotIn('"beats"', bt.DRAFT_SYSTEM)
        self.assertIn("in your own words", bt.DRAFT_SYSTEM)  # #2713's rule travels with it

    def test_a_long_session_says_which_part_it_is(self) -> None:
        self.assertIn("part 2 of 3", bt.draft_system(2, 3))
        self.assertNotIn("{part_note}", bt.draft_system(1, 1))
        self.assertNotIn("{limit}", bt.draft_system(1, 1))

    def test_the_schema_has_the_fields_the_check_reads(self) -> None:
        item = bt.SCHEMA["properties"]["beats"]["items"]
        self.assertEqual(set(item["required"]), {"what", "who", "lines", "where", "kind"})
        self.assertEqual(item["properties"]["kind"]["enum"], list(bt.KINDS))


class TheSetting(unittest.TestCase):
    BASE: ClassVar[dict[str, str]] = {
        "DISCORD_TOKEN": "x" * 60,
        "EARS_SHARED_SECRET": "s",
        "DATABASE_URL": "postgres://x",
    }

    def test_it_is_off_by_default_and_reads_on_and_off(self) -> None:
        self.assertFalse(load_settings(self.BASE).recap_structured_output_beats)
        for raw, want in (("1", True), ("on", True), ("0", False), ("", False)):
            got = load_settings({**self.BASE, "RECAP_STRUCTURED_OUTPUT_BEATS": raw})
            self.assertEqual(got.recap_structured_output_beats, want, raw)

    def test_a_bad_value_stops_start_up_naming_the_setting(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            load_settings({**self.BASE, "RECAP_STRUCTURED_OUTPUT_BEATS": "maybe"})
        self.assertIn("RECAP_STRUCTURED_OUTPUT_BEATS", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
