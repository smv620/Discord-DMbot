"""The bot's side of a line fixed from the website (#2883): the queued fix is claimed, checked
again and applied the way Discord's `/dmbot fix` applies it. The rows are written by the real
door (#2858), so what the bot reads is what the website queues. Lines here are made up."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import PropertyMock, patch

from dmbot.bot import DMBot
from dmbot.campaigns import Campaign
from dmbot.transcript import web_line_fixes as wf
from dmbot.transcript.suggestions import Own, Suggestion, SuggestionStore
from tests.test_recap_web import BOB
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_line_fixes import ALICE_AT, HEARD_AT, FixBase


class FakeHost:
    """The running bot as `apply` sees it; the suggestion store and the line are real."""

    def __init__(self, store: SuggestionStore, campaigns: Any) -> None:
        self.suggestions: SuggestionStore | None = store
        self._campaigns = campaigns
        self.consent = {ALICE.id, BOB.id}
        self.secret = False
        self.written: list[tuple[int, int, str]] = []
        self.write_ok = True
        self.write_error = False
        self.running = False
        self.posted: list[tuple[int, int, str]] = []
        self.post_ok = True
        self.db: Any = None

    async def get_campaign(self, guild_id: int, campaign_id: str) -> Campaign | None:
        found: Campaign | None = await self._campaigns.get(guild_id, campaign_id)
        return found

    def fix_has_consent(self, guild_id: int, user_id: int) -> bool:
        return user_id in self.consent

    async def fix_names_a_secret(self, guild_id: int, campaign_id: str, text: str) -> bool:
        return self.secret

    async def apply_line_fix(
        self,
        guild_id: int,
        campaign_id: str,
        session_id: str,
        user_id: int,
        started_ms: int,
        text: str,
        *,
        noted: bool = False,
    ) -> bool:
        if self.write_error:
            raise RuntimeError("a database error quoting a line")
        if not self.write_ok:
            return False
        self.written.append((user_id, started_ms, text))
        async with self.db.guild(guild_id) as conn:
            await conn.execute(
                "UPDATE transcript_lines SET text = %s WHERE session_id = %s AND user_id = %s"
                " AND started_ms = %s",
                (text, session_id, user_id, started_ms),
            )
        return True

    def fix_session_running(self, guild_id: int, session_id: str) -> bool:
        return self.running

    async def fix_post_suggestion(
        self, guild_id: int, channel_id: int, made: Suggestion, line: Own, text: str
    ) -> bool:
        self.posted.append((channel_id, made.id, text))
        return self.post_ok


class Base(FixBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.sug = SuggestionStore(self.db)
        self.host = FakeHost(self.sug, self.campaigns)
        self.host.db = self.db
        self.queue = wf.ActionStore(self.db)

    async def run_all(self) -> None:
        while (item := await self.queue.claim_next(THURSDAY.id)) is not None:
            self.assertTrue(await wf.run_one(self.host, self.queue, THURSDAY.id, item))

    async def row(self) -> dict[str, Any]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT state, stage, error, result, undo, words FROM web_line_fixes"
                " ORDER BY seq DESC LIMIT 1"
            )
            found = await cur.fetchone()
        assert found is not None
        return dict(found)

    async def ends(self, state: str, error: str = "", result: str = "") -> dict[str, Any]:
        """Run the queue and check how the newest fix ended; its words are always blank."""
        await self.run_all()
        row = await self.row()
        self.assertEqual((row["state"], row["error"], row["result"]), (state, error, result))
        self.assertEqual(row["words"], "")  # the person's words never outlive the fix
        return row

    async def change_line(self, at: int, speaker: int, words: str) -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE transcript_lines SET text = %s WHERE session_id = %s AND user_id = %s"
                " AND started_ms = %s",
                (words, self.sid, speaker, at),
            )


class Suggesting(Base):
    async def test_a_suggestion_posts_to_the_dm_screen_and_ends_sent(self) -> None:
        await self.ask(self.bob)
        await self.ends("done", result="sent")
        ((channel, _, text),) = self.host.posted
        self.assertEqual((channel, text), (77, "we climb"))
        (open_one,) = await self.sug.waiting(THURSDAY.id, self.one.id)
        self.assertEqual(open_one.suggested, "we climb")

    async def test_a_secret_name_never_stops_a_suggestion(self) -> None:
        self.host.secret = True  # only a rewrite is checked; the speaker must never learn
        await self.ask(self.bob)
        await self.ends("done", result="sent")

    async def test_a_speaker_who_stopped_being_recorded_is_player_stopped(self) -> None:
        await self.ask(self.bob)
        self.host.consent.discard(BOB.id)
        await self.ends("failed", wf.PLAYER_STOPPED)
        self.assertEqual(self.host.posted, [])

    async def test_a_dm_screen_that_went_away_is_no_dm_screen(self) -> None:
        await self.ask(self.bob)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE campaigns SET dm_screen_channel_id = NULL")
        await self.ends("failed", wf.NO_DM_SCREEN)

    async def test_a_question_that_cannot_be_posted_is_dropped_and_no_dm_screen(self) -> None:
        await self.ask(self.bob)
        self.host.post_ok = False
        await self.ends("failed", wf.NO_DM_SCREEN)
        self.assertEqual(await self.sug.waiting(THURSDAY.id, self.one.id), [])

    async def test_a_line_that_already_has_a_suggestion_is_open(self) -> None:
        await self.ask(self.bob)
        await self.sug.suggest(THURSDAY.id, self.one.id, self.sid, BOB.id, HEARD_AT, "we climbed")
        await self.ends("failed", wf.OPEN)

    async def test_three_waiting_suggestions_is_many(self) -> None:
        await self.ask(self.bob)
        for i, at in enumerate((ALICE_AT + 1, ALICE_AT + 2, ALICE_AT + 3)):
            await self.store_t.add_lines(
                THURSDAY.id, self.sid, [self.line(at, BOB.id, f"line number {i}")]
            )
            made = await self.sug.suggest(THURSDAY.id, self.one.id, self.sid, BOB.id, at, "x y")
            self.assertIsInstance(made, Suggestion)
        await self.ends("failed", wf.MANY)

    def line(self, at: int, user: int, words: str) -> Any:
        from dmbot.transcript.models import Line

        return Line(at, user, words, words, 1000)


class Rewriting(Base):
    async def test_a_rewrite_writes_the_line_and_ends_changed(self) -> None:
        await self.ask(self.session)
        await self.ends("done", result="changed")
        self.assertEqual(self.host.written, [(BOB.id, HEARD_AT, "we climb")])
        self.assertEqual((await self.line_words(HEARD_AT, BOB.id))[1], "we climb")
        self.assertEqual(self.host.posted, [])  # nothing goes to the table or the speaker

    async def test_undo_is_offered_only_while_the_session_runs(self) -> None:
        await self.ask(self.session)
        self.assertFalse((await self.ends("done", result="changed"))["undo"])
        await self.change_line(HEARD_AT, BOB.id, "we clime")
        self.host.running = True
        await self.ask(self.session, "we climb up")
        row = await self.ends("done", result="changed")
        self.assertTrue(row["undo"])

    async def test_a_secret_name_fails_a_rewrite(self) -> None:
        await self.ask(self.session)
        self.host.secret = True
        await self.ends("failed", wf.SECRET)
        self.assertEqual(self.host.written, [])

    async def test_a_speaker_who_stopped_being_recorded_is_player_stopped_for_a_rewrite(
        self,
    ) -> None:
        await self.ask(self.session)
        self.host.consent.discard(BOB.id)
        await self.ends("failed", wf.PLAYER_STOPPED)

    async def test_a_line_that_could_not_be_written_is_not_written(self) -> None:
        await self.ask(self.session)
        self.host.write_ok = False
        await self.ends("failed", wf.NOT_WRITTEN)
        await self.ask(self.session, "we climb up")
        self.host.write_error = True
        await self.ends("failed", wf.NOT_WRITTEN)

    async def test_a_dm_who_handed_over_since_is_failed(self) -> None:
        await self.ask(self.session)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("DELETE FROM campaign_dms WHERE user_id = %s", (ALICE.id,))
        await self.ends("failed", wf.FAILED)
        self.assertEqual(self.host.written, [])


class Checks(Base):
    async def test_a_line_changed_since_it_was_shown_is_line_changed(self) -> None:
        await self.ask(self.session)
        await self.change_line(HEARD_AT, BOB.id, "we climbed already")
        await self.ends("failed", wf.LINE_CHANGED)
        self.assertEqual(self.host.written, [])

    async def test_a_line_that_is_gone_is_line_gone(self) -> None:
        await self.ask(self.session)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("DELETE FROM transcript_lines WHERE started_ms = %s", (HEARD_AT,))
        await self.ends("failed", wf.LINE_GONE)

    async def test_the_same_words_or_unusable_words_are_failed(self) -> None:
        await self.ask(self.session)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE web_line_fixes SET words = 'we clime'")
        await self.ends("failed", wf.FAILED)
        await self.ask(self.session, "we climb")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE web_line_fixes SET words = E'\\x01\\x02' WHERE state='waiting'"
            )
        await self.ends("failed", wf.FAILED)

    async def test_without_the_suggestion_store_it_is_not_available(self) -> None:
        await self.ask(self.session)
        self.host.suggestions = None
        await self.ends("failed", wf.NOT_AVAILABLE)

    async def test_a_bug_ends_as_failed_with_the_words_blank(self) -> None:
        await self.ask(self.session)

        async def boom(*_: Any) -> Any:
            raise RuntimeError("a database error quoting a line")

        self.host.get_campaign = boom  # type: ignore[method-assign,assignment]
        with self.assertLogs(level=logging.INFO) as logs:
            await self.ends("failed", wf.FAILED)
        self.assertNotIn("quoting", "\n".join(r.getMessage() for r in logs.records))

    async def test_no_log_line_holds_the_persons_words(self) -> None:
        await self.ask(self.session, "we climb the secret stair")
        with self.assertLogs(level=logging.DEBUG) as logs:
            await self.ends("done", result="changed")
        text = "\n".join(r.getMessage() for r in logs.records)
        self.assertTrue(logs.records)
        for forbidden in ("secret stair", "we climb", "we clime"):
            self.assertNotIn(forbidden, text)


class TheQueue(Base):
    async def test_a_claim_moves_the_row_to_working_and_fixing(self) -> None:
        await self.ask(self.session)
        item = await self.queue.claim_next(THURSDAY.id)
        assert item is not None
        row = await self.row()
        self.assertEqual((row["state"], row["stage"]), ("working", "fixing"))
        self.assertIsNone(await self.queue.claim_next(THURSDAY.id))  # not twice
        self.assertTrue(await self.queue.finish(THURSDAY.id, item, wf.Outcome(result=wf.CHANGED)))
        self.assertFalse(await self.queue.finish(THURSDAY.id, item, wf.Outcome(wf.FAILED)))

    async def test_a_second_run_of_a_finished_rewrite_fails_as_line_changed(self) -> None:
        await self.ask(self.session)
        item = await self.queue.claim_next(THURSDAY.id)
        assert item is not None
        await wf.apply(self.host, self.queue, THURSDAY.id, item)
        again = await wf.apply(
            self.host, self.queue, THURSDAY.id, item
        )  # a row reset after a crash
        self.assertEqual(again.error, wf.LINE_CHANGED)

    async def test_the_sweep_deletes_a_day_old_fix_whatever_its_state(self) -> None:
        await self.ask(self.session)  # fresh, still waiting
        await self.ask(self.bob)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE web_line_fixes SET created_at = created_at - 90000 WHERE mode = 'suggest'"
            )
        self.assertEqual(await self.queue.sweep(THURSDAY.id), 1)
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT mode, words FROM web_line_fixes")
            left = [dict(r) for r in await cur.fetchall()]
        self.assertEqual(left, [{"mode": "rewrite", "words": "we climb"}])


class TheLoop(Base):
    async def test_the_poll_loop_does_not_wait_for_a_fix(self) -> None:
        started, release = asyncio.Event(), asyncio.Event()
        queue: dict[int, list[Any]] = {1: [SimpleNamespace(campaign_id="c")] * 2, 2: []}

        async def claim(guild_id: int) -> Any:
            return queue[guild_id].pop(0) if queue[guild_id] else None

        async def slow(self: Any, store: Any, guild_id: int, item: Any) -> None:
            started.set()
            await release.wait()

        b: Any = DMBot.__new__(DMBot)
        b.web_line_fixes = SimpleNamespace(claim_next=claim)
        b._fix_tasks = {}
        b._closing = False
        b._asking = set()
        guilds = PropertyMock(return_value=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
        with patch.object(DMBot, "guilds", guilds), patch.object(DMBot, "_run_line_fix", slow):
            await asyncio.wait_for(b.run_web_line_fixes(), timeout=1)
            await started.wait()
            await asyncio.wait_for(b.run_web_line_fixes(), timeout=1)
            self.assertEqual(len(b._fix_tasks), 1)  # one at a time for the server
            self.assertEqual(len(queue[1]), 1)  # the second was not even claimed
            release.set()
            await asyncio.gather(*b._fix_tasks.values())
        await asyncio.sleep(0)
        self.assertEqual(b._fix_tasks, {})
