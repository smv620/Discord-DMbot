"""Making a session's theatrical transcript (#3244, slice 3): the run with a stand-in AI, against
a real Postgres. Invented names; the stand-in answers in the script format."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from typing import Any

from dmbot.memory import theatre as th
from dmbot.transcript import theatre_run as tr
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.theatre_store import TheatreStore
from tests.test_memory_names import GUILD, NamesTest

TAVIN, DM_USER, MIRA = 8, 9, 10
START = 1_700_000_000
MS = START * 1000
NAMES = {TAVIN: "Tavin", DM_USER: "DM", MIRA: "Mira"}
KNOWN = ["Kell", "Tavin", "Tavin's cloak"]


def scene_of(*texts: tuple[int, int, str]) -> th.Scene:
    lines = tuple(th.Said(n, u, NAMES[u], t) for n, u, t in texts)
    return th.Scene((), lines)


class Replies:
    """A stand-in AI: one scripted reply per call, and a log of what it was asked."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.asked: list[tuple[str, str]] = []

    async def __call__(self, instructions: str, question: str) -> Any:
        self.asked.append((instructions, question))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def ok(text: str) -> Any:
    return SimpleNamespace(text=text, cut=False)


def cut(text: str = "") -> Any:
    return SimpleNamespace(text=text, cut=True)


import unittest  # noqa: E402


class TheSceneRead(unittest.IsolatedAsyncioTestCase):
    SCENE = scene_of((1, TAVIN, "ya so"), (2, DM_USER, "how many charges"))

    async def test_a_good_answer_is_read_and_checked(self) -> None:
        counts: Counter[str] = Counter()
        ask = Replies(ok("1 | Tavin: Yes.\n2 | DM: How many charges?"))
        got = await tr.read_scene(ask, self.SCENE, KNOWN, counts)
        assert got is not None
        self.assertEqual([r.sources for r in got], [(1,), (2,)])

    async def test_the_ai_is_sent_the_tables_names_and_the_numbered_lines_only(self) -> None:
        ask = Replies(ok("1 | Tavin: Yes."))
        await tr.read_scene(ask, self.SCENE, KNOWN, Counter())
        instructions, question = ask.asked[0]
        self.assertIn("- Kell", instructions)
        self.assertEqual(question.splitlines()[0], "1 | Tavin: ya so")

    async def test_a_cut_answer_is_retried_in_halves_and_joined(self) -> None:
        counts: Counter[str] = Counter()
        ask = Replies(cut("1 | Tavin: Ye"), ok("1 | Tavin: Yes."), ok("2 | DM: How many charges?"))
        got = await tr.read_scene(ask, self.SCENE, KNOWN, counts)
        assert got is not None
        self.assertEqual([r.sources for r in got], [(1,), (2,)])
        self.assertEqual(counts["split"], 1)
        # the second half reads the first line as context
        self.assertTrue(ask.asked[2][1].startswith("CONTEXT 1 | Tavin: ya so"))

    async def test_a_scene_still_cut_after_every_split_is_reported_not_taken_as_empty(self) -> None:
        counts: Counter[str] = Counter()
        many = scene_of(*[(n, TAVIN, f"w{n}") for n in range(1, 9)])
        ask = Replies(*[cut() for _ in range(40)])
        self.assertIsNone(await tr.read_scene(ask, many, KNOWN, counts))
        self.assertGreaterEqual(counts["stopped_early"], 1)

    async def test_an_unreadable_answer_for_several_lines_is_retried_in_halves(self) -> None:
        ask = Replies(ok("Here is your script!"), ok("1 | Tavin: Yes."), ok("2 | DM: Charges?"))
        got = await tr.read_scene(ask, self.SCENE, KNOWN, Counter())
        assert got is not None
        self.assertEqual(len(got), 2)

    async def test_a_failed_call_leaves_the_part_unread(self) -> None:
        counts: Counter[str] = Counter()
        ask = Replies(RuntimeError("down"))
        self.assertIsNone(await tr.read_scene(ask, self.SCENE, KNOWN, counts))
        self.assertEqual(counts["call_failed"], 1)

    async def test_a_one_line_scene_with_nothing_to_say_is_an_empty_success(self) -> None:
        one = scene_of((1, TAVIN, "um"))
        self.assertEqual(await tr.read_scene(Replies(ok("")), one, KNOWN, Counter()), [])


class TheRun(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.transcripts = TranscriptStore(self.db)
        self.theatre = TheatreStore(self.db)
        self.session = await self.transcripts.open_session(GUILD, self.campaign.id, START)
        await self.transcripts.end_session(GUILD, self.session, START + 100)
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [
                Line(MS + 1000, TAVIN, "ya so", "Yes."),
                Line(MS + 2000, DM_USER, "charges", "How many charges?"),
                Line(MS + 3000, MIRA, "I nod", "I nod."),
                Line(MS + 4000, TAVIN, "off topic", "pizza", topic="off_topic"),
            ],
        )
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_lines WHERE session_id = %s ORDER BY started_ms",
                (self.session,),
            )
            self.ids = [int(r["id"]) for r in await cur.fetchall()]

    def answer(self) -> str:
        a, b, c, _ = self.ids
        return f"{a} | Tavin: Yes, [Kell].\n{b} | DM: How many charges?\n{c} | Mira: I nod."

    async def go(
        self, ask: Any, agreed: set[int], later: set[int] | None = None, **kw: Any
    ) -> tr.Report:
        async def still() -> set[int]:
            return later if later is not None else agreed

        return await tr.run(
            self.db, ask, GUILD, self.session, agreed=agreed, names=NAMES, known=KNOWN,
            still_agreed=still, **kw,
        )  # fmt: skip

    async def test_a_session_is_read_and_its_script_kept(self) -> None:
        report = await self.go(Replies(ok(self.answer())), {TAVIN, DM_USER, MIRA})
        self.assertEqual((report.lines, report.parts, report.kept, report.saved), (3, 1, 3, True))
        got = await self.theatre.read(GUILD, self.session)
        self.assertEqual(got[0].text, "Yes, [Kell].")

    async def test_the_off_topic_line_and_the_sidebar_never_reach_the_ai(self) -> None:
        ask = Replies(ok(self.answer()))
        await self.go(ask, {TAVIN, DM_USER, MIRA})
        self.assertNotIn("pizza", ask.asked[0][1])

    async def test_someone_who_does_not_agree_is_never_sent(self) -> None:
        ask = Replies(ok(f"{self.ids[0]} | Tavin: Yes.\n{self.ids[1]} | DM: How many charges?"))
        report = await self.go(ask, {TAVIN, DM_USER})
        self.assertEqual(report.lines, 2)
        self.assertNotIn("I nod", ask.asked[0][1])

    async def test_a_person_who_stopped_while_the_ai_was_away_is_left_out(self) -> None:
        report = await self.go(
            Replies(ok(self.answer())), {TAVIN, DM_USER, MIRA}, later={TAVIN, DM_USER}
        )
        self.assertEqual(report.kept, 2)
        self.assertEqual(report.counts["left_out_no_longer_agreed"], 1)
        texts = [r.text for r in await self.theatre.read(GUILD, self.session)]
        self.assertNotIn("I nod.", texts)

    async def test_a_part_that_cannot_be_read_leaves_the_old_script_alone(self) -> None:
        await self.go(Replies(ok(self.answer())), {TAVIN, DM_USER, MIRA})
        report = await self.go(Replies(RuntimeError("down")), {TAVIN, DM_USER, MIRA})
        self.assertEqual((report.failed_parts, report.saved), (1, False))
        self.assertEqual(len(await self.theatre.read(GUILD, self.session)), 3)

    async def test_progress_is_told_after_every_part_with_counts_only(self) -> None:
        seen: list[tr.Progress] = []

        async def progress(p: tr.Progress) -> None:
            seen.append(p)

        ticks = iter(range(100))
        await self.go(
            Replies(ok(self.answer())), {TAVIN, DM_USER, MIRA}, progress=progress,
            clock=lambda: float(next(ticks)),
        )  # fmt: skip
        self.assertEqual([p.stage for p in seen], ["reading", "saving", "done"])
        self.assertEqual({(p.part, p.of) for p in seen}, {(1, 1)})
        self.assertTrue(all(p.moved_at >= p.elapsed for p in seen))
