"""The DM's note and Undo for last time's recap left out of an imported session (#3298): counts
only, the DM checked on every press, the lines handed back. Real Postgres, fake Discord."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dmbot.recaps import split as sp
from dmbot.recaps.session_split import release_recap_lines
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import recap_lines as rl
from tests.test_recap_web import Base
from tests.test_web_api import THURSDAY

DM, PLAYER = 7, 9
KEPT = sp.Split(
    (sp.Part(sp.TALK, 1, 1), sp.Part(sp.RECAP, 2, 3), sp.Part(sp.PLAY, 4, 5)), False, "ai"
)


class TheNote(Base):
    def test_the_note_says_how_many_lines_and_nothing_else(self) -> None:
        self.assertIn(
            "12 lines read as last time's recap and not used as this session's events", rl.note(12)
        )
        self.assertIn("1 line read as last time's recap", rl.note(1))
        self.assertIn("A recap already written stays as it is", rl.done(3))


class TheUndo(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.lines = TranscriptStore(self.db)
        self.g, self.c = THURSDAY.id, self.one.id
        self.sid = await self.lines.open_session(self.g, self.c, 1_700_000_000)
        await self.lines.add_lines(
            self.g,
            self.sid,
            [Line(n * 1000, DM, f"line {n}", f"line {n}") for n in range(1, 6)],
        )
        await self.lines.save_session_split(self.g, self.sid, KEPT.to_json())

    async def test_the_lines_are_handed_back_and_queued_to_be_read_again(self) -> None:
        released = await release_recap_lines(self.lines, self.g, self.sid)
        self.assertEqual(released, frozenset({2, 3}))
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT text, reread FROM transcript_lines WHERE session_id = %s"
                " ORDER BY started_ms",
                (self.sid,),
            )
            rows = [(r["text"], r["reread"]) for r in await cur.fetchall()]
        self.assertEqual([again for _, again in rows], [False, True, True, False, False])
        stored = sp.from_json(await self.lines.session_split(self.g, self.sid))
        assert stored is not None
        # only the recap parts became play; talk stays talk, play stays play
        self.assertEqual(
            ([p.label for p in stored.parts], stored.method),
            ([sp.TALK, sp.PLAY, sp.PLAY], "dm"),
        )
        # nothing more to hand back the second time
        self.assertEqual(await release_recap_lines(self.lines, self.g, self.sid), frozenset())

    async def test_nothing_is_queued_for_no_lines_and_a_split_that_cannot_be_read_frees_none(
        self,
    ) -> None:
        self.assertEqual(await self.lines.save_split_and_queue(self.g, self.sid, {}, set()), 0)
        self.assertEqual(await release_recap_lines(self.lines, self.g, self.sid), frozenset())

    def press(self, user: int, campaign: object | None) -> tuple[object, object]:
        bot = MagicMock()
        bot.campaigns.get = AsyncMock(return_value=campaign)
        bot.undo_recap_lines = AsyncMock(return_value=2)
        it = MagicMock(client=bot, guild=MagicMock(id=self.g), user=MagicMock(id=user))
        it.response.is_done = MagicMock(side_effect=[False, True, True, True])  # (answered by then)
        it.response.defer = AsyncMock()
        it.followup.send = AsyncMock()
        return it, bot

    async def test_only_a_dm_can_press_it_and_the_press_is_answered_first(self) -> None:
        campaign = SimpleNamespace(
            id=self.c, dm_user_ids=frozenset({DM}), dm_accounts=frozenset({DM})
        )
        button = rl.RecapLinesUndoButton(self.c, self.sid)
        stranger, bot = self.press(PLAYER, campaign)
        await button.callback(stranger)  # type: ignore[arg-type]
        bot.undo_recap_lines.assert_not_called()  # type: ignore[attr-defined]
        stranger.response.defer.assert_awaited()  # type: ignore[attr-defined]
        dm, bot = self.press(DM, campaign)
        await button.callback(dm)  # type: ignore[arg-type]
        bot.undo_recap_lines.assert_awaited_once_with(self.g, self.c, self.sid)  # type: ignore[attr-defined]
        self.assertIn(rl.done(2), dm.followup.send.await_args.args[0])  # type: ignore[attr-defined]

    def test_the_button_carries_both_ids_for_a_restart(self) -> None:
        item = rl.RecapLinesUndoButton("a" * 32, "b" * 32)
        self.assertEqual(item.item.custom_id, f"dmbot:recaplines:undo:{'a' * 32}:{'b' * 32}")
        self.assertLessEqual(len(item.item.custom_id or ""), 100)

    async def test_the_bots_undo_checks_the_campaign_and_reads_only_what_was_released(self) -> None:
        from dmbot.bot import DMBot

        reads: list[int] = []

        async def read(gid: int) -> None:
            reads.append(gid)

        me: Any = SimpleNamespace(transcripts=self.lines, read_put_back_lines=read)
        undo: Any = DMBot.undo_recap_lines
        self.assertEqual(await undo(me, self.g, "f" * 32, self.sid), 0)  # another campaign's
        self.assertEqual(await undo(me, self.g, self.c, "e" * 32), 0)  # no such session
        self.assertEqual(reads, [])
        self.assertEqual(await undo(me, self.g, self.c, self.sid), 2)
        self.assertEqual(reads, [self.g])
        self.assertEqual(await undo(me, self.g, self.c, self.sid), 0)  # nothing left: no sweep
        self.assertEqual(reads, [self.g])
