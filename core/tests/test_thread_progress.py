"""Progress on story threads the campaign has, waiting for the DM (#4522, quests slice 5b;
Supervisor's ruling A and B on #4522; migration 0520): offered once, kept in ONE change that one
Undo takes back, turned down for good, and the consent rule of every after-session proposal (a
withdrawal drops what waits and strips what was answered). Postgres. Invented names."""

from __future__ import annotations

import unittest

from dmbot.memory import thread_parts as parts
from dmbot.memory import thread_progress as tp
from dmbot.memory.gate import Viewer
from dmbot.memory.models import DM, GOING, OPEN
from dmbot.transcript.models import Line as Heard
from dmbot.transcript.store import TranscriptStore
from tests.agreed import agree
from tests.test_memory_store import GUILD_A, MemoryTest

DM_ID, BEL, KIA = 11, 22, 33
LINE_MS = 1_700_000_005_000
NOW = 1_700_000_100


class ThePureCheck(unittest.TestCase):
    def test_a_line_must_be_one_a_thread_can_take(self) -> None:
        self.assertIsNone(tp.check(tp.Change(tp.PART, "reward", "50 gold")))  # (promised: no)
        self.assertIsNone(tp.check(tp.Change(tp.PART, parts.OBJECTIVE, "  ")))
        self.assertIsNone(tp.check(tp.Change(tp.THREAD_STATE, state=OPEN)))  # (play moved it)
        self.assertIsNone(tp.check(tp.Change(tp.PART_STATE, state="done")))  # (which part?)
        made = tp.check(tp.Change(tp.PART, parts.HELP, "two dog  sleds", state="nonsense"))
        assert made is not None
        self.assertEqual((made.words, made.state), ("two dog sleds", "to_keep"))  # (its first)
        done = tp.check(tp.Change(tp.PART, parts.OBJECTIVE, "find him", state="done"))
        assert done is not None
        self.assertEqual(done.state, "to_do")  # (a new part starts where the DM's would)
        lent = tp.check(tp.Change(tp.PART, parts.HELP, "the sleds", state="lent"))
        assert lent is not None
        self.assertEqual(lent.state, "lent")
        when = tp.check(tp.Change(tp.PART, parts.OBJECTIVE, "go", due="by dawn"))
        assert when is not None
        self.assertEqual(when.due, "")  # (a "when" is a time limit's only)

    def test_the_same_change_has_one_key_whatever_its_spacing(self) -> None:
        a = tp.Change(tp.PART, parts.COMPLICATION, "The bridge is out")
        b = tp.Change(tp.PART, parts.COMPLICATION, "the  bridge is out")
        self.assertEqual(a.key("t1"), b.key("t1"))
        self.assertNotEqual(a.key("t1"), a.key("t2"))


class TheStore(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = tp.ThreadProgressStore(self.db)
        made = await self.memory.new_thread(
            GUILD_A, self.c, "Find Garret", "quest", OPEN, source=DM
        )
        self.thread = made.value.id
        goal = await self.memory.add_thread_part(
            GUILD_A, self.c, self.thread, parts.OBJECTIVE, "find Garret", source=DM
        )
        self.goal = goal.value.id
        await agree(self.db, GUILD_A, DM_ID, BEL)
        self.transcripts = TranscriptStore(self.db)
        self.sid = await self.transcripts.open_session(GUILD_A, self.c, 1_700_000_000)
        await self.transcripts.add_lines(
            GUILD_A, self.sid, [Heard(LINE_MS, BEL, "x", "We found him in the cave.")]
        )

    async def offer(self, *changes: tp.Change, speakers: tuple[int, ...] = (BEL,)) -> int:
        return await self.store.offer(
            GUILD_A, self.c, self.sid, 1_700_000_000,
            [(self.thread, c, (LINE_MS,), speakers) for c in changes], now=NOW,
        )  # fmt: skip

    async def lines(self) -> list[tp.Line]:
        return await self.store.waiting(GUILD_A, self.c)

    async def kept_parts(self) -> list[parts.Part]:
        return await self.memory.parts_of_threads(GUILD_A, self.c, [self.thread], Viewer.the_dm())

    async def state(self) -> str:
        (info,) = [
            t for t in await self.memory.threads(GUILD_A, self.c) if t.thread.id == self.thread
        ]
        assert info.state is not None
        return info.state.condition

    DONE = tp.Change(tp.PART_STATE, state="done")
    TRAP = tp.Change(tp.PART, parts.COMPLICATION, "the cave is trapped")
    GOING_ON = tp.Change(tp.THREAD_STATE, state=GOING)

    def done(self) -> tp.Change:
        return tp.Change(tp.PART_STATE, state="done", part_id=self.goal)

    async def test_it_waits_once_and_never_with_nobody_quoted(self) -> None:
        self.assertEqual(await self.offer(self.TRAP), 1)
        self.assertEqual(await self.offer(self.TRAP), 0)  # (offered once)
        self.assertEqual(await self.offer(self.GOING_ON, speakers=()), 0)
        (line,) = await self.lines()
        self.assertEqual((line.session_id, line.line_starts, line.speaker_ids),
                         (self.sid, (LINE_MS,), (BEL,)))  # fmt: skip

    async def test_keep_writes_every_line_in_one_change_and_one_undo_takes_them_back(self) -> None:
        await self.offer(self.TRAP, self.done(), self.GOING_ON)
        written = await self.memory.keep_thread_progress(
            GUILD_A, self.c, [line.id for line in await self.lines()], source=DM
        )
        self.assertEqual(written.value, 3)
        assert written.batch is not None
        now = {(p.kind, p.words): p for p in await self.kept_parts()}
        self.assertEqual(now[(parts.OBJECTIVE, "find Garret")].state, "done")
        self.assertEqual(now[(parts.COMPLICATION, "the cave is trapped")].source, parts.SESSION)
        self.assertEqual(await self.state(), GOING)
        self.assertEqual(await self.lines(), [])
        await self.memory.undo(GUILD_A, self.c, written.batch, after=tp.unkeep_in(written.batch))
        now = {(p.kind, p.words): p for p in await self.kept_parts()}
        self.assertEqual(now[(parts.OBJECTIVE, "find Garret")].state, "to_do")
        self.assertNotIn((parts.COMPLICATION, "the cave is trapped"), now)
        self.assertEqual(await self.state(), OPEN)
        self.assertEqual(len(await self.lines()), 3)  # (they wait again)

    async def test_a_line_that_no_longer_fits_is_left_out_and_answered(self) -> None:
        await self.offer(self.done())
        await self.memory.set_thread_part_state(GUILD_A, self.c, self.goal, "failed", source=DM)
        (line,) = await self.lines()  # (the part it moves is history now)
        written = await self.memory.keep_thread_progress(GUILD_A, self.c, [line.id], source=DM)
        self.assertEqual((written.value, written.batch), (0, None))  # (no change, no Undo)
        self.assertEqual(await self.lines(), [])
        self.assertEqual(await self.statuses(), ["turned_down"])  # (never shown as kept)

    async def statuses(self) -> list[str]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT status FROM thread_progress WHERE guild_id = %s ORDER BY kind", (GUILD_A,)
            )
            return [str(r["status"]) for r in await cur.fetchall()]

    async def test_a_time_limit_is_kept_as_words_and_never_dated(self) -> None:
        limit = tp.Change(tp.PART, parts.TIME_LIMIT, "before the full moon", due="the full moon")
        await self.offer(limit)
        (line,) = await self.lines()
        self.assertEqual(line.change.due, "the full moon")  # (for the DM's own date, part 3)
        written = await self.memory.keep_thread_progress(GUILD_A, self.c, [line.id], source=DM)
        self.assertEqual(written.value, 1)
        (kept,) = [p for p in await self.kept_parts() if p.kind == parts.TIME_LIMIT]
        self.assertEqual((kept.words, kept.due_minute), ("before the full moon", None))

    async def test_in_a_mixed_keep_the_ones_that_fit_are_written(self) -> None:
        await self.offer(self.TRAP, self.done())
        await self.memory.set_thread_part_state(GUILD_A, self.c, self.goal, "failed", source=DM)
        ids = [line.id for line in await self.lines()]
        written = await self.memory.keep_thread_progress(GUILD_A, self.c, ids, source=DM)
        self.assertEqual(written.value, 1)
        self.assertEqual(sorted(await self.statuses()), ["kept", "turned_down"])

    async def test_only_the_dm_keeps(self) -> None:
        from dmbot.memory.models import MemoryRuleError

        await self.offer(self.TRAP)
        ids = [line.id for line in await self.lines()]
        with self.assertRaises(MemoryRuleError):
            await self.memory.keep_thread_progress(GUILD_A, self.c, ids, source="scan")

    async def test_a_second_keep_of_the_same_lines_is_refused(self) -> None:
        await self.offer(self.TRAP)
        ids = [line.id for line in await self.lines()]
        await self.memory.keep_thread_progress(GUILD_A, self.c, ids, source=DM)
        from dmbot.memory.models import MemoryRuleError

        with self.assertRaises(MemoryRuleError):
            await self.memory.keep_thread_progress(GUILD_A, self.c, ids, source=DM)

    async def test_turned_down_is_never_offered_again(self) -> None:
        await self.offer(self.TRAP)
        (line,) = await self.lines()
        self.assertEqual(await self.store.turn_down(GUILD_A, self.c, [line.id]), 1)
        self.assertEqual(await self.offer(self.TRAP), 0)
        self.assertEqual(await self.store.unturn(GUILD_A, self.c, [line.id]), 1)
        self.assertEqual(len(await self.lines()), 1)

    async def test_it_counts_and_shows_only_while_its_speakers_are_recorded(self) -> None:
        await self.offer(self.TRAP)
        await self.offer(self.GOING_ON, speakers=(DM_ID,))
        self.assertEqual(await self.store.count(GUILD_A, self.c, opted_in={DM_ID}), 1)
        self.assertEqual(len(await self.store.waiting(GUILD_A, self.c, opted_in={DM_ID})), 1)
        self.assertEqual(await self.store.count(GUILD_A, self.c), 1)  # (the sweep deleted it)

    async def test_stop_recording_me_drops_what_waits_and_strips_what_was_answered(self) -> None:
        await self.offer(self.TRAP)
        await self.offer(self.GOING_ON)
        trap, going = sorted(await self.lines(), key=lambda line: line.change.kind)
        kept = await self.memory.keep_thread_progress(GUILD_A, self.c, [trap.id], source=DM)
        assert kept.batch is not None
        async with self.db.guild(GUILD_A) as conn:
            self.assertEqual(await tp.forget(conn, GUILD_A, BEL), 1)  # (the waiting one)
            cur = await conn.execute(
                "SELECT status, speaker_ids, line_starts FROM thread_progress WHERE guild_id = %s",
                (GUILD_A,),
            )
            rows = [(r["status"], r["speaker_ids"], r["line_starts"]) for r in await cur.fetchall()]
        self.assertEqual(rows, [("kept", [], [])])
        # (an Undo still takes the part back, but the line never waits again: nobody is quoted)
        await self.memory.undo(GUILD_A, self.c, kept.batch, after=tp.unkeep_in(kept.batch))
        self.assertEqual(await self.lines(), [])
        kinds = {(p.kind, p.words) for p in await self.kept_parts()}
        self.assertNotIn((parts.COMPLICATION, "the cave is trapped"), kinds)
        self.assertNotIn(going.id, [line.id for line in await self.lines()])

    async def test_a_deleted_session_takes_its_waiting_lines(self) -> None:
        await self.offer(self.TRAP)
        await self.offer(self.GOING_ON)
        trap = next(line for line in await self.lines() if line.change.kind == tp.PART)
        kept = await self.memory.keep_thread_progress(GUILD_A, self.c, [trap.id], source=DM)
        assert kept.batch is not None
        async with self.db.guild(GUILD_A) as conn:
            self.assertEqual(await tp.forget_session(conn, GUILD_A, self.sid), 1)
        self.assertEqual(await self.lines(), [])
        # (the kept one stays the DM's, without its lines, and an Undo never makes it wait)
        await self.memory.undo(GUILD_A, self.c, kept.batch, after=tp.unkeep_in(kept.batch))
        self.assertEqual(await self.lines(), [])

    async def test_a_kia_who_never_agreed_is_still_swept(self) -> None:
        await self.offer(self.TRAP, speakers=(KIA,))
        self.assertEqual(await self.store.count(GUILD_A, self.c, opted_in={BEL, DM_ID}), 0)


if __name__ == "__main__":
    unittest.main()
