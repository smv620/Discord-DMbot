"""The DM's note and Undo for last time's recap left out of an imported session (#3298): counts
only, the DM checked on every press, the lines handed back. Real Postgres, fake Discord."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

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


AT = 1_700_000_000  # the session start: 14 Nov 2023 UTC


class TheNote(Base):
    def test_the_note_names_the_session_and_says_how_many_lines_and_nothing_else(self) -> None:
        text = rl.note(12, AT)
        self.assertIn("Session of 14 Nov 2023: 12 lines were read as last time's recap", text)
        self.assertIn("not as this session's events", text)
        self.assertIn(rl.SHOW, text)
        self.assertIn(rl.UNDO, text)
        self.assertIn("1 line was read as last time's recap", rl.note(1, AT))
        self.assertIn("A recap already written stays as it is", rl.done(3))

    def test_the_lines_are_listed_with_their_speaker_in_order_and_cut_with_a_count(self) -> None:
        names = {DM: "Mira", PLAYER: "Rook"}
        text = rl.show_text([(DM, "We rode out."), (PLAYER, "And so on.")], names, AT)
        self.assertIn("**Mira:** We rode out.", text)
        self.assertLess(text.index("Mira"), text.index("Rook"))
        long = rl.show_text([(DM, "x" * 150)] * 60, names, AT)
        self.assertLessEqual(len(long), 2000)
        self.assertRegex(long, r"…and \d+ more\.")
        self.assertEqual(rl.show_text([], names, AT), rl.NOTHING_TO_SHOW)


class TheOnce(Base):
    """The note is said once for each session (#5232)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.lines = TranscriptStore(self.db)
        self.g, self.c = THURSDAY.id, self.one.id
        self.sid = await self.lines.open_session(self.g, self.c, AT)

    async def test_only_the_first_read_takes_the_turn_and_a_failed_post_gives_it_back(self) -> None:
        self.assertTrue(await self.lines.claim_recap_note(self.g, self.sid, 5))
        self.assertFalse(await self.lines.claim_recap_note(self.g, self.sid, 6))
        await self.lines.release_recap_note(self.g, self.sid)
        self.assertTrue(await self.lines.claim_recap_note(self.g, self.sid, 7))

    async def test_a_second_read_of_the_same_session_posts_nothing_new(self) -> None:
        from dmbot.bot import DMBot

        post = AsyncMock(return_value=True)
        me: Any = SimpleNamespace(transcripts=self.lines, post=post)
        say: Any = DMBot._post_recap_note
        await say(me, self.g, self.c, self.sid, 3, 55)
        await say(me, self.g, self.c, self.sid, 3, 55)
        self.assertEqual(post.await_count, 1)
        assert post.await_args is not None
        self.assertEqual(post.await_args.args[1], rl.note(3, AT))

    async def test_a_refused_post_raises_and_is_tried_again_by_the_next_read(self) -> None:
        from dmbot.bot import DMBot

        post = AsyncMock(side_effect=[False, True])
        me: Any = SimpleNamespace(transcripts=self.lines, post=post)
        say: Any = DMBot._post_recap_note
        with self.assertRaises(RuntimeError):
            await say(me, self.g, self.c, self.sid, 3, 55)
        await say(me, self.g, self.c, self.sid, 3, 55)
        self.assertEqual(post.await_count, 2)


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

        recounted: list[tuple[int, str]] = []

        async def recount(gid: int, cid: str, session_at: int) -> None:
            recounted.append((gid, cid))  # (#3172 item d: the counts of names come back)

        me: Any = SimpleNamespace(
            transcripts=self.lines, read_put_back_lines=read, recount_heard=recount
        )
        undo: Any = DMBot.undo_recap_lines
        self.assertEqual(await undo(me, self.g, "f" * 32, self.sid), 0)  # another campaign's
        self.assertEqual(await undo(me, self.g, self.c, "e" * 32), 0)  # no such session
        self.assertEqual(reads, [])
        self.assertEqual(recounted, [])
        self.assertEqual(await undo(me, self.g, self.c, self.sid), 2)
        self.assertEqual(reads, [self.g])
        self.assertEqual(recounted, [(self.g, self.c)])  # the names said are counted again
        self.assertEqual(await undo(me, self.g, self.c, self.sid), 0)  # nothing left: no sweep
        self.assertEqual(reads, [self.g])
        self.assertEqual(recounted, [(self.g, self.c)])


class TheShow(TheUndo):
    """👀 Show them lists the recap lines, only of people recorded now, and only to a DM."""

    async def lines_text(self, recorded: set[int]) -> str:
        from dmbot.bot import DMBot

        me: Any = SimpleNamespace(
            transcripts=self.lines,
            consent=SimpleNamespace(recorded_in=AsyncMock(return_value=frozenset(recorded))),
        )
        guild = SimpleNamespace(id=self.g, get_member=lambda uid: None)
        show: Any = DMBot.recap_lines_text
        with patch("dmbot.ui.transcripts.display_names", AsyncMock(return_value={DM: "Mira"})):
            return str(await show(me, guild, self.c, self.sid))

    async def test_it_shows_exactly_the_lines_the_split_calls_recap(self) -> None:
        text = await self.lines_text({DM})
        self.assertIn("line 2", text)
        self.assertIn("line 3", text)
        for other in ("line 1", "line 4", "line 5"):
            self.assertNotIn(other, text)

    async def test_a_speaker_who_is_not_recorded_now_leaves_no_words_and_no_trace(self) -> None:
        self.assertEqual(await self.lines_text(set()), rl.NOTHING_TO_SHOW)
        self.assertNotIn("line 2", await self.lines_text(set()))

    async def test_another_campaigns_session_shows_nothing(self) -> None:
        from dmbot.bot import DMBot

        me: Any = SimpleNamespace(transcripts=self.lines, consent=MagicMock())
        show: Any = DMBot.recap_lines_text
        guild = SimpleNamespace(id=self.g)
        self.assertEqual(await show(me, guild, "f" * 32, self.sid), rl.GONE)

    async def test_only_a_dm_can_press_it_and_read_them_as_play_still_works_after(self) -> None:
        campaign = SimpleNamespace(id=self.c, dm_user_ids=frozenset({DM}))
        button = rl.RecapLinesShowButton(self.c, self.sid)
        stranger, stranger_bot = self.press(PLAYER, campaign)
        shown: Any = AsyncMock(return_value="the lines")
        cast(Any, stranger_bot).recap_lines_text = shown
        await button.callback(stranger)  # type: ignore[arg-type]
        shown.assert_not_called()
        dm, dm_bot = self.press(DM, campaign)
        cast(Any, dm_bot).recap_lines_text = AsyncMock(return_value="the lines")
        await button.callback(dm)  # type: ignore[arg-type]
        self.assertEqual(dm.followup.send.await_args.args[0], "the lines")  # type: ignore[attr-defined]
        self.assertEqual(await release_recap_lines(self.lines, self.g, self.sid), frozenset({2, 3}))
        both = [type(i).__name__ for i in rl.undo_view(self.c, self.sid).children]
        self.assertEqual(both, ["RecapLinesShowButton", "RecapLinesUndoButton"])
