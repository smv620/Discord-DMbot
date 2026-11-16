"""The session reader's progress on threads the campaign has (#4522, quests slice 5b part 2;
`memory.session_progress`): read in the same call as its new threads, checked by code (a label it
was shown, a quote in the window's own lines, a state the part's kind has), consent asked again
after the answer, and stored for the DM by the bot. Invented names; no AI call."""

from __future__ import annotations

import json
import unittest
from collections.abc import Collection
from typing import Any, ClassVar

from dmbot.memory import session_progress as sp
from dmbot.memory import session_threads as st
from dmbot.memory import thread_parts as parts
from dmbot.memory import thread_progress as prog
from dmbot.memory import thread_read as tr
from dmbot.memory.extractor import Said as Line
from dmbot.memory.models import GOING, OPEN, RESOLVED
from dmbot.ui import thread_questions as ui
from tests.test_memory_store import GUILD_A, MemoryTest
from tests.test_session_threads import BEL, DM_ID, ENTRIES, KIA, NAMES, lines

TALK = lines(
    (DM_ID, "Garret limps out of the cave, alive."),
    (BEL, "We found him! Let's get him home."),
    (DM_ID, "The rope bridge behind you has snapped."),
    (KIA, "I hate rope bridges."),
)
GARRET = sp.KnownThread(
    "t1", "Find Garret", (sp.KnownPart("p1", parts.OBJECTIVE, "find Garret", "to_do"),)
)
OTHER = sp.KnownThread(
    "t2", "Guard the caravan", (sp.KnownPart("p2", parts.OBJECTIVE, "reach the pass", "to_do"),)
)
KNOWN = [GARRET, OTHER]


def said(**kw: str) -> sp.SaidProgress:
    base = {
        "thread": "T1", "change": sp.PART_MOVES, "part_kind": "", "part": "T1.P1",
        "state": "done", "words": "Garret found", "due": "", "quote": "We found him",
    }  # fmt: skip
    base.update(kw)
    return sp.SaidProgress(**base)


def check(*s: sp.SaidProgress, window: list[Line] = TALK) -> tuple[list[Any], dict[str, int]]:
    return sp.check(s, window, KNOWN, where=st.where, seen=set())


class TheChecks(unittest.TestCase):
    def test_a_part_done_with_its_quote_becomes_a_line_with_its_speakers(self) -> None:
        (line,), dropped = check(said())
        thread_id, change, starts, speakers = line
        self.assertEqual((thread_id, change.kind, change.part_id, change.state),
                         ("t1", prog.PART_STATE, "p1", "done"))  # fmt: skip
        self.assertEqual((starts, speakers, dropped), ((2000,), (BEL,), {}))

    def test_what_code_refuses(self) -> None:
        cases = {
            "no_thread": said(thread="T9"),
            "no_quote": said(quote="We never found him"),
            "no_part": said(part="T1.P7"),
            "other_thread": said(thread="T2", part="T1.P1"),
            "not_a_state": said(state="overcome"),  # (an objective is never "overcome")
        }
        for why, s in cases.items():
            with self.subTest(why):
                self.assertEqual(check(s), ([], {why: 1}))
        self.assertEqual(check(said(state="to_do"))[1], {"not_a_state": 1})  # (no change)

    def test_a_new_part_and_the_threads_state(self) -> None:
        (bridge, going), _ = check(
            said(change=sp.NEW_PART, part_kind=parts.COMPLICATION, part="", state="",
                 words="the rope bridge snapped", quote="The rope bridge behind you has snapped"),
            said(change=sp.THREAD_MOVES, part="", state="finished", quote="Let's get him home"),
        )  # fmt: skip
        self.assertEqual((bridge[1].kind, bridge[1].part_kind), (prog.PART, parts.COMPLICATION))
        self.assertEqual(going[1].state, RESOLVED)
        self.assertEqual(
            check(said(change=sp.THREAD_MOVES, part="", state="not_started"))[1],
            {"not_a_state": 1},
        )

    def test_a_line_said_twice_is_one(self) -> None:
        self.assertEqual(len(check(said(), said(words="found him"))[0]), 1)

    def test_the_run_decides_what_was_kept_before(self) -> None:
        """`seen` is only read: a line dropped for consent after the call never blocks the same
        change said later by someone who agrees (the run adds what passed)."""
        seen: set[tuple[str, str]] = set()
        first, _ = sp.check([said()], TALK, KNOWN, where=st.where, seen=seen)
        again, _ = sp.check([said()], TALK, KNOWN, where=st.where, seen=seen)
        self.assertEqual((len(first), len(again), seen), (1, 1, set()))
        seen.add((first[0][0], first[0][1].key(first[0][0])))
        self.assertEqual(sp.check([said()], TALK, KNOWN, where=st.where, seen=seen)[1],
                         {"double": 1})  # fmt: skip

    def test_happening_now_is_never_offered_for_a_thread_already_happening(self) -> None:
        going = sp.KnownThread("t1", "Find Garret", GARRET.parts, GOING)
        move = said(change=sp.THREAD_MOVES, part="", state="happening_now")
        out, why = sp.check([move], TALK, [going], where=st.where, seen=set())
        self.assertEqual((out, why), ([], {"not_a_state": 1}))
        out, _ = sp.check([move], TALK, [GARRET], where=st.where, seen=set())  # (state unknown)
        self.assertEqual(out[0][1].state, GOING)

    def test_a_cut_answer_gives_no_progress(self) -> None:
        self.assertEqual(sp.parse('{"threads": [], "progress": ['), [])
        rows = [{"thread": "T1"}] * (sp.PROGRESS_MAX + 5)
        self.assertEqual(len(sp.parse(json.dumps({"progress": rows}))), sp.PROGRESS_MAX)

    def test_the_prompt_shows_labels_never_ids(self) -> None:
        thread_id, part_id = "a" * 32, "b" * 32
        known = [sp.KnownThread(thread_id, "Find Garret",
                                (sp.KnownPart(part_id, parts.OBJECTIVE, "find Garret", "to_do"),),
                                GOING)]  # fmt: skip
        text = st.prompt([("1", "DM", "hello")], [], [], known)
        self.assertIn("T1. Find Garret (active)", text)
        self.assertIn("T1.P1 objective: find Garret (to_do)", text)
        self.assertNotIn(thread_id, text)
        self.assertNotIn(part_id, text)
        schema = sp.schema(tr.SCHEMA)
        self.assertIn("progress", schema["required"])
        self.assertNotIn("progress", tr.SCHEMA["required"])  # (the lore reader's is untouched)


class _AI:
    def __init__(self, progress: list[dict[str, str]]) -> None:
        self.progress = progress
        self.system = ""
        self.schema: dict[str, Any] = {}

    async def complete(self, system: str, text: str, **kw: Any) -> Any:
        self.system = system
        self.schema = dict(kw.get("schema") or {})

        class Reply:
            cut = False

        reply = Reply()
        reply.text = json.dumps({"threads": [], "progress": self.progress})  # type: ignore[attr-defined]
        return reply


class TheRun(unittest.IsolatedAsyncioTestCase):
    async def run_with(
        self,
        ai: _AI,
        agreed: list[Collection[int]],
        progress: list[sp.KnownThread] = KNOWN,
        said: list[Line] = TALK,
    ) -> st.Report:
        answers = iter(agreed)
        last: list[Collection[int]] = [agreed[-1]]

        async def consenting() -> Collection[int]:
            last[0] = next(answers, last[0])
            return last[0]

        _found, report = await st.run(
            ai, said, dm_ids={DM_ID}, entries=ENTRIES, names=NAMES, have=(), known=[],
            consenting=consenting, max_calls=5, max_tokens=100, progress=progress,
        )  # fmt: skip
        return report

    ROW: ClassVar[dict[str, str]] = {
        "thread": "T1", "change": sp.PART_MOVES, "part_kind": "", "part": "T1.P1",
        "state": "done", "words": "found", "due": "", "quote": "We found him",
    }  # fmt: skip

    async def test_the_same_call_reads_progress(self) -> None:
        ai = _AI([self.ROW])
        report = await self.run_with(ai, [{DM_ID, BEL}])
        self.assertEqual(report.calls, 1)
        self.assertIn("Also list PROGRESS", ai.system)
        self.assertEqual([(m[0], m[1].state) for m in report.progress], [("t1", "done")])
        self.assertIn("1 progress lines", report.line())

    async def test_with_no_open_thread_the_call_is_5as(self) -> None:
        ai = _AI([])
        await self.run_with(ai, [{DM_ID, BEL}], progress=[])
        self.assertNotIn("Also list PROGRESS", ai.system)
        self.assertNotIn("progress", ai.schema["properties"])
        ai = _AI([])
        await self.run_with(ai, [{DM_ID, BEL}])
        self.assertIn("progress", ai.schema["properties"])

    async def test_the_same_change_in_two_windows_is_kept_once(self) -> None:
        many = [Line(n, BEL, "We found him" if n in (5, 250) else f"line {n}", n * 1000)
                for n in range(1, 301)]  # fmt: skip
        report = await self.run_with(_AI([self.ROW]), [{BEL}], said=many)
        self.assertEqual(len(report.progress), 1)
        self.assertEqual(report.dropped, {"progress_double": 1})

    async def test_a_speaker_who_stops_while_it_reads_drops_the_line(self) -> None:
        report = await self.run_with(_AI([self.ROW]), [{DM_ID, BEL}, {DM_ID}])
        self.assertEqual((report.progress, report.dropped), ([], {"stopped_recording": 1}))


class TheCountLine(unittest.TestCase):
    def test_threads_and_changes_in_one_line(self) -> None:
        label = "the session of 14 Nov 2026"
        both = ui.threads_found_text(2, False, label, 3)
        self.assertIn(
            "heard 2 possible story threads and 3 changes to story threads you have in", both
        )
        self.assertIn(
            "1 change to a story thread you have", ui.threads_found_text(0, False, label, 1)
        )
        self.assertEqual(
            ui.threads_found_text(2, False, label),
            ui.threads_found_text(2, False, label, 0),
        )


class TheInputs(MemoryTest):
    async def test_open_threads_and_their_standing_parts_are_known(self) -> None:
        """And the 5a reader's names: a live thread is named to the model (its state was compared
        as a row, so none ever was, found in #4522 5b)."""
        from dmbot.memory.models import DM
        from dmbot.ui.name_background import progress_inputs, thread_inputs

        live = (await self.memory.new_thread(GUILD_A, self.c, "Find Garret", "quest", GOING,
                                             source=DM)).value.id  # fmt: skip
        await self.memory.new_thread(GUILD_A, self.c, "Old feud", "quest", RESOLVED, source=DM)
        await self.memory.new_thread(GUILD_A, self.c, "A rumour", "hook", OPEN, source=DM)
        await self.memory.add_thread_part(
            GUILD_A, self.c, live, parts.OBJECTIVE, "find Garret", source=DM
        )
        *_, known = await thread_inputs(self.memory, GUILD_A, self.c)
        self.assertEqual(sorted(known), ["A rumour", "Find Garret"])
        threads = {t.name: t for t in await progress_inputs(self.memory, GUILD_A, self.c)}
        self.assertEqual(set(threads), {"A rumour", "Find Garret"})
        self.assertEqual([p.words for p in threads["Find Garret"].parts], ["find Garret"])


if __name__ == "__main__":
    unittest.main()
