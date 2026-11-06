"""Making a session's theatrical transcript (#3244, slice 3): the run with a stand-in AI, against
a real Postgres. Invented names; the stand-in answers in the script format."""

from __future__ import annotations

import asyncio
import unittest
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
        if isinstance(reply, BaseException):
            raise reply
        return reply


def ok(text: str) -> Any:
    return SimpleNamespace(text=text, cut=False)


def cut(text: str = "") -> Any:
    return SimpleNamespace(text=text, cut=True)


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

    async def test_a_blank_answer_is_an_empty_success_not_a_failed_part(self) -> None:
        many = scene_of(*[(n, TAVIN, "mm") for n in range(1, 19)])
        self.assertEqual(await tr.read_scene(Replies(ok("  ")), many, KNOWN, Counter()), [])

    async def test_an_unreadable_single_line_is_asked_again_then_counted_not_failed(self) -> None:
        one = scene_of((1, TAVIN, "ya"))
        counts: Counter[str] = Counter()
        got = await tr.read_scene(Replies(ok("Sure!"), ok("Sure!")), one, KNOWN, counts)
        self.assertEqual((got, counts["lost_line"]), ([], 1))
        again = await tr.read_scene(
            Replies(ok("Sure!"), ok("1 | Tavin: Yes.")), one, KNOWN, Counter()
        )
        assert again is not None
        self.assertEqual(len(again), 1)

    async def test_cancelling_is_not_swallowed(self) -> None:
        with self.assertRaises(asyncio.CancelledError):
            await tr.read_scene(Replies(asyncio.CancelledError()), self.SCENE, KNOWN, Counter())

    async def test_a_reply_that_is_a_plain_string_or_none_is_read(self) -> None:
        got = await tr.read_scene(Replies("1 | Tavin: Yes."), self.SCENE, KNOWN, Counter())
        assert got is not None
        self.assertEqual(len(got), 1)
        self.assertEqual(await tr.read_scene(Replies(None), self.SCENE, KNOWN, Counter()), [])

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

    async def agreed_all(self) -> set[int]:
        return {TAVIN, DM_USER, MIRA}

    async def test_a_scene_that_held_a_person_who_stopped_is_left_out_whole(self) -> None:
        # Mira's words were in the scene the AI saw: any line could carry them, whatever it cites
        everyone = {TAVIN, DM_USER, MIRA}
        await self.go(Replies(ok(self.answer())), everyone)  # a script already kept
        report = await self.go(Replies(ok(self.answer())), everyone, later={TAVIN, DM_USER})
        self.assertEqual(report.counts["scene_left_out_no_longer_agreed"], 1)
        self.assertEqual((report.kept, report.saved), (0, False))
        self.assertEqual(
            len(await self.theatre.read(GUILD, self.session)), 3
        )  # (the old one stands)

    async def test_a_person_who_stops_between_scenes_is_never_sent_afterwards(self) -> None:
        everyone = {TAVIN, DM_USER, MIRA}
        a, b, _, _ = self.ids
        calls = 0

        async def still() -> set[int]:
            nonlocal calls
            calls += 1
            return everyone if calls <= 4 else {TAVIN, DM_USER}  # (Mira stops after scene 2)

        ask = Replies(ok(f"{a} | Tavin: Yes."), ok(f"{b} | DM: How many charges?"))
        report = await tr.run(
            self.db, ask, GUILD, self.session, agreed=everyone, names=NAMES, known=KNOWN,
            still_agreed=still, scene_size=1,
        )  # fmt: skip
        self.assertEqual(len(ask.asked), 2)  # (the third scene, Mira's, was never sent)
        self.assertNotIn("I nod", " ".join(q for _, q in ask.asked))
        self.assertEqual(report.counts["scene_left_out_no_longer_agreed"], 1)

    async def test_no_discord_id_is_ever_a_speakers_label(self) -> None:
        raw = [(1, 123456789012345678, "x", False), (2, 987654321098765432, "y", False)]
        labels = tr.speaker_labels(raw, {})
        self.assertEqual(sorted(labels.values()), ["Speaker 1", "Speaker 2"])

    async def test_two_people_with_one_display_name_are_told_apart(self) -> None:
        ask = Replies(ok("x"))
        await tr.run(
            self.db, ask, GUILD, self.session, agreed={TAVIN, DM_USER, MIRA},
            names={TAVIN: "Alex", DM_USER: "Alex", MIRA: "Mira"}, known=KNOWN,
            still_agreed=self.agreed_all,
        )  # fmt: skip
        self.assertIn("| Alex 2:", ask.asked[0][1])

    async def test_typed_chat_is_in_the_script_marked_and_pictures_are_not(self) -> None:
        await self.transcripts.add_lines(
            GUILD,
            self.session,
            [
                Line(MS + 5000, TAVIN, "a typed line", "a typed line", typed=True),
                Line(MS + 6000, TAVIN, "a map", "a hidden map", typed=True, image=True),
            ],
        )
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT id FROM transcript_lines WHERE typed AND NOT image AND session_id = %s",
                (self.session,),
            )
            typed_id = int((await cur.fetchone())["id"])  # type: ignore[index]
        ask = Replies(ok(self.answer() + f"\n{typed_id} | Tavin: A typed line."))
        await self.go(ask, {TAVIN, DM_USER, MIRA})
        self.assertIn(f"{typed_id} | Tavin (typed): a typed line", ask.asked[0][1])
        self.assertNotIn("hidden map", ask.asked[0][1])
        texts = [r.text for r in await self.theatre.read(GUILD, self.session)]
        self.assertIn("(typed) A typed line.", texts)
        self.assertNotIn("(typed) Yes, [Kell].", texts)  # (spoken lines are not marked)

    async def test_rows_are_kept_in_the_order_of_their_sources(self) -> None:
        a, b, c, _ = self.ids
        shuffled = f"{c} | Mira: I nod.\n{a} | Tavin: Yes.\n{b} | DM: How many charges?"
        await self.go(Replies(ok(shuffled)), {TAVIN, DM_USER, MIRA})
        got = await self.theatre.read(GUILD, self.session)
        self.assertEqual([r.source_ids[0] for r in got], [a, b, c])

    async def test_the_first_failed_part_stops_the_spend(self) -> None:
        ask = Replies(RuntimeError("down"), ok("never asked"))
        report = await self.go(ask, {TAVIN, DM_USER, MIRA}, scene_size=1)
        self.assertEqual(len(ask.asked), 1)
        self.assertEqual((report.parts, report.failed_parts), (3, 1))

    async def test_a_part_that_cannot_be_read_leaves_the_old_script_alone(self) -> None:
        await self.go(Replies(ok(self.answer())), {TAVIN, DM_USER, MIRA})
        report = await self.go(Replies(RuntimeError("down")), {TAVIN, DM_USER, MIRA})
        self.assertEqual((report.failed_parts, report.saved), (1, False))
        self.assertEqual(len(await self.theatre.read(GUILD, self.session)), 3)

    async def test_progress_is_told_with_stage_part_and_a_stepped_clock(self) -> None:
        seen: list[tr.Progress] = []

        async def progress(p: tr.Progress) -> None:
            seen.append(p)

        ticks = iter(range(100))
        await self.go(
            Replies(ok(self.answer())), {TAVIN, DM_USER, MIRA}, progress=progress,
            clock=lambda: float(next(ticks)),
        )  # fmt: skip
        self.assertEqual([p.stage for p in seen], ["reading", "reading", "saving", "done"])
        self.assertEqual([(p.part, p.of) for p in seen], [(0, 1), (1, 1), (1, 1), (1, 1)])
        self.assertEqual([p.elapsed for p in seen], [1.0, 2.0, 3.0, 4.0])

    async def test_a_failing_progress_callback_does_not_lose_the_run(self) -> None:
        async def boom(_: tr.Progress) -> None:
            raise RuntimeError("edit failed")

        report = await self.go(Replies(ok(self.answer())), {TAVIN, DM_USER, MIRA}, progress=boom)
        self.assertTrue(report.saved)
        self.assertGreater(report.counts["progress_failed"], 0)

    async def test_a_failed_run_ends_in_its_own_stage(self) -> None:
        seen: list[str] = []

        async def progress(p: tr.Progress) -> None:
            seen.append(p.stage)

        await self.go(Replies(RuntimeError("down")), {TAVIN, DM_USER, MIRA}, progress=progress)
        self.assertEqual(seen[-1], "stopped")
