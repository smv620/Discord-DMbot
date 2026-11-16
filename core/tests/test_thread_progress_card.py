"""🧵 Progress to check, the 📜 card's second stage (#4522, quests slice 5b part 3): its words,
that the card turns to it once no new thread waits, that Keep writes the lines left on in one change
and turns down the ones unticked, that ↩ Undo brings them all back, "None of these happened", and
that the Questions board counts them. Postgres. Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from dmbot.memory import thread_parts as parts
from dmbot.memory import thread_progress as tp
from dmbot.memory.gate import Viewer
from dmbot.memory.models import DM, GOING, OPEN
from dmbot.questions import counts, kinds
from dmbot.questions.counts import Sources
from dmbot.transcript.models import Line as Heard
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import thread_progress_card as card
from dmbot.ui import thread_questions as ui
from tests.agreed import agree
from tests.test_memory_store import DM as DM_USER
from tests.test_memory_store import GUILD_A, MemoryTest

BEL = 22
LINE_MS = 1_700_000_005_000


class TheWords(unittest.TestCase):
    def test_each_kind_of_line_reads_in_the_threads_cards_words(self) -> None:
        trap = tp.Change(tp.PART, parts.COMPLICATION, "the bridge snapped")
        self.assertEqual(card.line_text(trap, {}), "New · In the way: the bridge snapped")
        sleds = tp.Change(tp.PART, parts.HELP, "two sleds", "lent")
        self.assertEqual(card.line_text(sleds, {}), "New · Help: two sleds (lent)")
        moon = tp.Change(tp.PART, parts.TIME_LIMIT, "before the moon", due="the full moon")
        self.assertIn("(by: the full moon)", card.line_text(moon, {}))
        done = tp.Change(tp.PART_STATE, state="done", part_id="p1")
        self.assertEqual(card.line_text(done, {"p1": "find Garret"}), "Done: find Garret")
        self.assertEqual(card.line_text(done, {}), "Done: a step")
        going = tp.Change(tp.THREAD_STATE, state=GOING)
        self.assertEqual(card.line_text(going, {}), "This thread: happening now")
        kept = tp.Change(tp.THREAD_STATE, state="resolved")
        self.assertEqual(card.line_text(kept, {}, "promise"), "This promise: kept")

    def test_an_unticked_line_is_struck_and_the_session_named(self) -> None:
        a = tp.Line("a", "t", tp.Change(tp.THREAD_STATE, state=GOING), "s", 1, (1,), (2,), 1)
        b = tp.Line("b", "t", tp.Change(tp.PART, parts.OBJECTIVE, "find it"), "s", 1, (1,), (2,), 1)
        text = card.card_text("Find *Garret*", [a, b], {}, frozenset({"a"}), at=1, of=2,
                              heard="the session of 14 Nov 2026")  # fmt: skip
        self.assertIn("🧵 **Progress to check** (thread 1 of 2)", text)
        self.assertIn(
            "**Find \\*Garret\\***: did these happen? DMbot heard them in **the session", text
        )
        self.assertIn("• This thread: happening now", text)
        self.assertIn("• ~~New · Step: find it~~ (left out)", text)
        self.assertIn("2 sessions", card.card_text("X", [a, b], {}, frozenset(), at=1, of=1,
                                                   heard="", sessions=2))  # fmt: skip

    def test_a_long_card_with_a_long_answer_in_front_stays_under_discords_limit(self) -> None:
        from dmbot.discord_limits import MESSAGE_MAX

        long = "x" * 200
        lines = [
            tp.Line(str(i), "t", tp.Change(tp.PART, parts.TIME_LIMIT, long, due=long), "s", 1,
                    (1,), (2,), 1)
            for i in range(card.SHOWN)
        ]  # fmt: skip
        text = card.card_text("N" * 200, lines, {}, frozenset(), at=1, of=9, heard="h")
        said = card.kept_text("N" * 200, 25)
        self.assertLessEqual(len(f"{said}\n\n{text}"), MESSAGE_MAX)
        self.assertIn("more: see the menu below", text)
        self.assertTrue(text.endswith(card.HELP))


class TheCard(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.store = tp.ThreadProgressStore(self.db)
        made = await self.memory.new_thread(
            GUILD_A, self.c, "Find Garret", "quest", OPEN, source=DM
        )
        self.thread = made.value.id
        await agree(self.db, GUILD_A, DM_USER, BEL)
        self.sid = await TranscriptStore(self.db).open_session(GUILD_A, self.c, 1_700_000_000)
        await TranscriptStore(self.db).add_lines(
            GUILD_A, self.sid, [Heard(LINE_MS, BEL, "x", "We're going after him.")]
        )
        await self.store.offer(
            GUILD_A, self.c, self.sid, 1_700_000_000,
            [(self.thread, tp.Change(tp.THREAD_STATE, state=GOING), (LINE_MS,), (BEL,)),
             (self.thread, tp.Change(tp.PART, parts.COMPLICATION, "the bridge snapped"),
              (LINE_MS,), (BEL,))],
            now=1_700_000_100,
        )  # fmt: skip

    def interaction(self) -> Any:
        sent: list[str] = []

        async def reply(text: str = "", **kw: Any) -> None:
            sent.append(str(kw.get("content", text)))

        it = MagicMock()
        it.user = SimpleNamespace(id=DM_USER, guild_permissions=SimpleNamespace(manage_guild=False))
        it.guild = SimpleNamespace(id=GUILD_A)
        it.client = SimpleNamespace(
            memory=self.memory, campaigns=self.campaigns,
            consent=SimpleNamespace(recorded_in=AsyncMock(return_value=frozenset({DM_USER, BEL}))),
            transcripts=None,
        )  # fmt: skip
        it.response.is_done = lambda: False
        it.response.defer = AsyncMock()
        it.response.edit_message = it.followup.send = it.edit_original_response = reply
        it.response.send_message = reply
        it.sent = sent
        return it

    async def state(self) -> str:
        found = await self.memory.threads(GUILD_A, self.c)
        (info,) = [t for t in found if t.thread.id == self.thread]
        assert info.state is not None
        return info.state.condition

    async def kinds_kept(self) -> set[str]:
        got = await self.memory.parts_of_threads(GUILD_A, self.c, [self.thread], Viewer.the_dm())
        return {p.kind for p in got}

    async def test_the_card_turns_to_progress_once_no_new_thread_waits(self) -> None:
        view = ui.ThreadQuestions(self.c, GUILD_A)
        text = await view._load(self.interaction())
        assert text is not None
        self.assertIn(card.HEADING, text)
        self.assertIn("**Find Garret**: did these happen?", text)
        labels = [getattr(b, "label", None) for b in view.children]
        self.assertIn(card.KEEP, labels)
        self.assertIn(card.NONE, labels)

    async def test_keep_writes_what_is_left_on_and_undo_brings_it_all_back(self) -> None:
        view = ui.ThreadQuestions(self.c, GUILD_A)
        it = self.interaction()
        await view._load(it)
        trap = next(x.id for x in view.progress if x.change.kind == tp.PART)
        view.progress_on = frozenset(x.id for x in view.progress if x.id != trap)
        await view._keep_progress(it)
        self.assertIn("Kept 1 change on **Find Garret**", "\n".join(it.sent))
        self.assertEqual(await self.state(), GOING)
        self.assertNotIn(parts.COMPLICATION, await self.kinds_kept())  # (unticked: turned down)
        self.assertEqual(await self.store.waiting(GUILD_A, self.c), [])
        await view._undo(it)
        self.assertEqual(await self.state(), OPEN)
        back = await self.store.waiting(GUILD_A, self.c)
        self.assertEqual(len(back), 2)
        self.assertIn(trap, [x.id for x in back])  # (the unticked one is back too, in the Undo)

    async def test_a_keep_where_nothing_fits_still_turns_down_what_was_unticked(self) -> None:
        view = ui.ThreadQuestions(self.c, GUILD_A)
        it = self.interaction()
        await view._load(it)
        going = next(x.id for x in view.progress if x.change.kind == tp.THREAD_STATE)
        view.progress_on = frozenset({going})
        # (the thread moved on since: "happening now" no longer fits)
        await self.memory.set_condition(GUILD_A, self.c, self.thread, GOING, source=DM)
        await view._keep_progress(it)
        self.assertEqual(await self.store.waiting(GUILD_A, self.c), [])
        self.assertIn(card.NONE_FIT, "\n".join(it.sent))

    async def test_a_long_list_shows_a_cards_worth_and_answers_only_those(self) -> None:
        await self.store.offer(
            GUILD_A, self.c, self.sid, 1_700_000_000,
            [(self.thread, tp.Change(tp.PART, parts.OBJECTIVE, f"step {i}"), (LINE_MS,), (BEL,))
             for i in range(30)],
            now=1_700_000_200,
        )  # fmt: skip
        view = ui.ThreadQuestions(self.c, GUILD_A)
        it = self.interaction()
        await view._load(it)
        self.assertEqual(len(view.progress), card.SHOWN)
        await view._no_progress(it)
        self.assertEqual(len(await self.store.waiting(GUILD_A, self.c)), 32 - card.SHOWN)

    async def test_skip_moves_to_the_next_thread_and_comes_back_round(self) -> None:
        other = (await self.memory.new_thread(GUILD_A, self.c, "Guard the caravan", "quest", OPEN,
                                              source=DM)).value.id  # fmt: skip
        await self.store.offer(
            GUILD_A, self.c, self.sid, 1_700_000_000,
            [(other, tp.Change(tp.THREAD_STATE, state=GOING), (LINE_MS,), (BEL,))],
            now=1_700_000_300,
        )  # fmt: skip
        view = ui.ThreadQuestions(self.c, GUILD_A)
        it = self.interaction()
        text = await view._load(it)
        assert text is not None
        self.assertIn("(thread 1 of 2)", text)
        first = view.progress[0].thread_id
        await view._skip_progress(it)
        self.assertNotEqual(view.progress[0].thread_id, first)
        await view._skip_progress(it)  # (all skipped: back to the first, so Skip keeps working)
        self.assertEqual(view.progress[0].thread_id, first)

    async def test_someone_not_the_dm_changes_nothing(self) -> None:
        view = ui.ThreadQuestions(self.c, GUILD_A)
        it = self.interaction()
        await view._load(it)
        it.user = SimpleNamespace(id=999_000_111, guild_permissions=SimpleNamespace(
            manage_guild=False))  # fmt: skip
        await view._keep_progress(it)
        await view._no_progress(it)
        self.assertEqual(len(await self.store.waiting(GUILD_A, self.c)), 2)
        self.assertEqual(await self.state(), OPEN)
        self.assertIn("Only this campaign's DM", "\n".join(it.sent))

    async def test_none_of_these_happened_and_undo(self) -> None:
        view = ui.ThreadQuestions(self.c, GUILD_A)
        it = self.interaction()
        await view._load(it)
        await view._no_progress(it)
        self.assertEqual(await self.store.waiting(GUILD_A, self.c), [])
        self.assertEqual(await self.state(), OPEN)
        await view._undo(it)
        self.assertEqual(len(await self.store.waiting(GUILD_A, self.c)), 2)

    async def test_the_sample_run_keeps_every_line_for_the_score(self) -> None:
        """#4522 5c: `accept_all.keep_progress` keeps each thread's lines as the DM's Keep does,
        so the sample's backup holds what the reader moved."""
        from dmbot.devtools import accept_all

        other = (await self.memory.new_thread(GUILD_A, self.c, "Guard the caravan", "quest", OPEN,
                                              source=DM)).value.id  # fmt: skip
        broken = (await self.memory.new_thread(GUILD_A, self.c, "A third", "quest", OPEN,
                                               source=DM)).value.id  # fmt: skip
        await self.store.offer(
            GUILD_A, self.c, self.sid, 1_700_000_000,
            [(other, tp.Change(tp.THREAD_STATE, state=GOING), (LINE_MS,), (BEL,)),
             (broken, tp.Change(tp.THREAD_STATE, state=GOING), (LINE_MS,), (BEL,))],
            now=1_700_000_300,
        )  # fmt: skip
        self.assertEqual(await accept_all.progress_waiting(self.db, GUILD_A, self.c), 4)
        real = self.memory.keep_thread_progress
        broken_ids = {x.id for x in await self.store.waiting(GUILD_A, self.c)
                      if x.thread_id == broken}  # fmt: skip

        async def keep(g: int, c: str, ids: Any, **kw: Any) -> Any:
            if set(ids) & broken_ids:  # (one thread's keep fails: the others are still kept)
                raise RuntimeError("boom")
            return await real(g, c, ids, **kw)

        with patch.object(self.memory, "keep_thread_progress", keep):
            kept = await accept_all.keep_progress(self.db, self.memory, GUILD_A, self.c)
        self.assertEqual(kept, 3)
        await self.store.turn_down(GUILD_A, self.c, list(broken_ids))
        self.assertEqual(await self.state(), GOING)
        self.assertIn(parts.COMPLICATION, await self.kinds_kept())
        self.assertEqual(await self.store.waiting(GUILD_A, self.c), [])
        self.assertEqual(await accept_all.keep_progress(self.db, self.memory, GUILD_A, self.c), 0)

    async def test_the_board_counts_them_with_the_new_threads(self) -> None:
        got = await counts._COUNTERS[kinds.THREADS](
            Sources(db=self.db, memory=self.memory, recorded=AsyncMock(return_value={BEL})),
            GUILD_A, self.c,
        )  # fmt: skip
        self.assertEqual(got.n, 2)
        nobody = await counts._COUNTERS[kinds.THREADS](
            Sources(db=self.db, memory=self.memory, recorded=AsyncMock(return_value=set())),
            GUILD_A, self.c,
        )  # fmt: skip
        self.assertEqual(nobody.n, 0)  # (a speaker no longer recorded: never counted)

    async def test_the_after_session_line_counts_both(self) -> None:
        text = ui.threads_found_text(0, False, "the session of 14 Nov 2026", 2)
        self.assertIn("2 possible updates to story threads in", text)


if __name__ == "__main__":
    unittest.main()
