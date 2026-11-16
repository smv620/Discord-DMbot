"""🧵 Progress to check, the 📜 card's second stage (#4522, quests slice 5b part 3): its words,
that the card turns to it once no new thread waits, that Keep writes the lines left on in one change
and turns down the ones unticked, that ↩ Undo brings them all back, "None of these happened", and
that the Questions board counts them. Postgres. Invented names."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
        self.assertEqual(card.line_text(trap, {}), "New: in the way, the bridge snapped")
        sleds = tp.Change(tp.PART, parts.HELP, "two sleds", "lent")
        self.assertIn("(lent: to give back)", card.line_text(sleds, {}))
        moon = tp.Change(tp.PART, parts.TIME_LIMIT, "before the moon", due="the full moon")
        self.assertIn("(when: the full moon)", card.line_text(moon, {}))
        done = tp.Change(tp.PART_STATE, state="done", part_id="p1")
        self.assertEqual(card.line_text(done, {"p1": "find Garret"}), "“find Garret”: done")
        going = tp.Change(tp.THREAD_STATE, state=GOING)
        self.assertEqual(card.line_text(going, {}), "The thread: happening now")

    def test_an_unticked_line_is_struck_and_the_session_named(self) -> None:
        a = tp.Line("a", "t", tp.Change(tp.THREAD_STATE, state=GOING), "s", 1, (1,), (2,), 1)
        b = tp.Line("b", "t", tp.Change(tp.PART, parts.OBJECTIVE, "find it"), "s", 1, (1,), (2,), 1)
        text = card.card_text("Find *Garret*", [a, b], {}, frozenset({"a"}), at=1, of=2,
                              heard="the session of 14 Nov 2026")  # fmt: skip
        self.assertIn("🧵 **Progress to check** (1 of 2 threads)", text)
        self.assertIn("On **Find \\*Garret\\***, DMbot heard in **the session of 14 Nov", text)
        self.assertIn("• The thread: happening now", text)
        self.assertIn("• ~~New: step, find it~~ (left out)", text)


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
        self.assertIn("On **Find Garret**", text)
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
        self.assertEqual(len(await self.store.waiting(GUILD_A, self.c)), 2)

    async def test_none_of_these_happened_and_undo(self) -> None:
        view = ui.ThreadQuestions(self.c, GUILD_A)
        it = self.interaction()
        await view._load(it)
        await view._no_progress(it)
        self.assertEqual(await self.store.waiting(GUILD_A, self.c), [])
        self.assertEqual(await self.state(), OPEN)
        await view._undo(it)
        self.assertEqual(len(await self.store.waiting(GUILD_A, self.c)), 2)

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
        self.assertIn("2 changes to story threads you have", text)


if __name__ == "__main__":
    unittest.main()
