"""The writer's message front is cached only when asked (#4054): with RECAP_CACHE on, the first
writer call sends the beats, the transcript and the blocks after them as a cached leading block,
and the words the model reads are the same as with it off. Invented text only."""

from __future__ import annotations

from tests import recap_fixtures as fx
from tests.test_recap_critics_run import CLEAN, Smart
from tests.test_recap_jobs import WorkerHarness

TAGS = ["beats", "transcript", "ending", "names"]


def tags(message: str) -> list[str]:
    """The tagged blocks of the writer's message that matter here, in order."""
    import re

    return [t for t in re.findall(r"<(\w+)>", message) if t in TAGS]


class CacheFront(WorkerHarness):
    async def writer_message(self, *, cache_front: bool) -> tuple[str, str]:
        """(the cached front, the text after it) of the writer's call."""
        fast = Smart(fx.BEATS_ANSWER, fx.prose_answer(CLEAN), fx.GRADE_ANSWER, fx.FINISH_ANSWER)
        await self.setUpWorker(
            fast, fx.Canned(), critics=True, cache_front=cache_front, heard="They opened the door."
        )
        await self.go()
        wrote = [
            (front, text)
            for front, text in zip(fast.fronts, fast.sent, strict=False)
            if "<beats>" in front + text and "<names>" in front + text
        ]
        self.assertTrue(wrote, "the writer was never called")
        return wrote[0]

    async def test_off_the_writer_gets_one_plain_message_as_before(self) -> None:
        front, text = await self.writer_message(cache_front=False)
        self.assertEqual(front, "")
        self.assertIn("<transcript>", text)

    async def test_on_the_front_is_cached_and_it_is_the_same_message_split_in_two(self) -> None:
        front, text = await self.writer_message(cache_front=True)
        self.assertTrue(front.startswith("<beats>"))
        self.assertIn("<transcript>", front)
        self.assertEqual(text, "")  # a first try has nothing after the front
        self.assertEqual(tags(front), TAGS)  # the same blocks in the same order as with it off

    async def test_off_the_same_blocks_are_in_the_one_message(self) -> None:
        _, text = await self.writer_message(cache_front=False)
        self.assertEqual(tags(text), TAGS)
