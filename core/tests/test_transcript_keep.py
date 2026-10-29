"""Keep transcripts for (#1533): the DM's setting, the nightly job that deletes the transcripts of
sessions that ended longer ago than the limit, and what that leaves (the session's row and number,
recaps, other campaigns and servers)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.campaigns import CampaignError, CampaignStore
from dmbot.transcript.keep import DAY, KeepJob, note_text
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.transcript.suggestions import SuggestionStore
from tests.pg import DatabaseTest

GUILD, OTHER_GUILD = 1, 2
DM, ME = 7, 8
NOW = 1_800_000_000


def line(seconds: int, user: int, text: str) -> Line:
    return Line(seconds * 1000, user, text, text)


class KeepTest(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.store = TranscriptStore(self.db)
        self.suggestions = SuggestionStore(self.db)
        self.camp = await self.campaigns.create(GUILD, "Frostmaiden", DM)
        self.other = await self.campaigns.create(GUILD, "Sunken City", DM)
        self.far = await self.campaigns.create(OTHER_GUILD, "Far Away", DM)
        self.posts: list[tuple[str, str]] = []

    async def post(self, campaign: Any, text: str) -> bool:
        self.posts.append((campaign.id, text))
        return True

    def job(self) -> KeepJob:
        return KeepJob(
            campaigns=self.campaigns,
            transcripts=self.store,
            guild_ids=self.campaigns.guilds_with_campaigns,
            post_screen=self.post,
        )

    async def session(
        self, guild: int, campaign: str, days_ago: float, *, length: int = 3600, ended: bool = True
    ) -> str:
        started = NOW - int(days_ago * DAY)
        sid = await self.store.open_session(guild, campaign, started)
        await self.store.add_lines(
            guild, sid, [line(started + 1, ME, f"hello {sid[:4]}"), line(started + 2, DM, "hi")]
        )
        if ended:
            await self.store.end_session(guild, sid, started + length)
        return sid


class Setting(KeepTest):
    async def test_forever_is_the_default_and_the_dm_can_choose(self) -> None:
        got = await self.campaigns.get(GUILD, self.camp.id)
        assert got is not None
        self.assertIsNone(got.transcript_keep_days)
        for days in (90, 30, 365, None):
            saved = await self.campaigns.set_transcript_keep(GUILD, self.camp.id, days)
            self.assertEqual(saved.transcript_keep_days, days)

    async def test_only_the_offered_choices_are_saved(self) -> None:
        for bad in (0, 1, 60, -5):
            with self.assertRaises(CampaignError):
                await self.campaigns.set_transcript_keep(GUILD, self.camp.id, bad)

    async def test_it_is_this_campaigns_own(self) -> None:
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 30)
        other = await self.campaigns.get(GUILD, self.other.id)
        assert other is not None
        self.assertIsNone(other.transcript_keep_days)

    async def test_a_backup_carries_it(self) -> None:
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 90)
        backup = await self.campaigns.export(GUILD, self.camp.id)
        restored = await self.campaigns.import_backup(OTHER_GUILD, backup, DM)
        self.assertEqual(restored.transcript_keep_days, 90)
        backup["campaign"]["transcript_keep_days"] = 45
        with self.assertRaises(CampaignError):
            await self.campaigns.import_backup(OTHER_GUILD, backup, DM)

    async def test_a_restore_into_a_campaign_keeps_that_campaigns_own_limit(self) -> None:
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 90)
        backup = await self.campaigns.export(GUILD, self.camp.id)
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, None)
        await self.campaigns.import_backup(GUILD, backup, DM, replace_campaign_id=self.camp.id)
        got = await self.campaigns.get(GUILD, self.camp.id)
        assert got is not None
        # Old transcripts are never deleted by a restore: it does not bring a limit into a
        # campaign that has its own.
        self.assertIsNone(got.transcript_keep_days)

    async def test_a_malformed_limit_in_a_backup_is_refused(self) -> None:
        backup = await self.campaigns.export(GUILD, self.camp.id)
        for bad in (90.0, "90", True, 0):
            backup["campaign"]["transcript_keep_days"] = bad
            with self.assertRaises(CampaignError):
                await self.campaigns.import_backup(OTHER_GUILD, backup, DM)

    async def test_changing_it_lets_the_screen_say_so_again(self) -> None:
        self.assertTrue(await self.campaigns.mark_transcript_deletion_noted(GUILD, self.camp.id))
        self.assertFalse(await self.campaigns.mark_transcript_deletion_noted(GUILD, self.camp.id))
        saved = await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 30)
        self.assertFalse(saved.transcript_deleted_noted)


class Job(KeepTest):
    async def test_only_sessions_that_ended_before_the_limit_go(self) -> None:
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 90)
        old = await self.session(GUILD, self.camp.id, 120)
        edge = await self.session(GUILD, self.camp.id, 91)
        recent = await self.session(GUILD, self.camp.id, 30)
        result = await self.job().run_once(NOW)
        self.assertEqual((result.sessions, result.lines, result.failed), (2, 4, 0))
        self.assertEqual(await self.store.lines(GUILD, old), [])
        self.assertEqual(await self.store.lines(GUILD, edge), [])
        self.assertEqual(len(await self.store.lines(GUILD, recent)), 2)

    async def test_forever_deletes_nothing(self) -> None:
        old = await self.session(GUILD, self.camp.id, 900)
        result = await self.job().run_once(NOW)
        self.assertEqual((result.campaigns, result.sessions), (0, 0))
        self.assertEqual(len(await self.store.lines(GUILD, old)), 2)

    async def test_a_running_session_and_one_saved_without_an_end_are_never_deleted(self) -> None:
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 30)
        running = await self.session(GUILD, self.camp.id, 400, ended=False)
        result = await self.job().run_once(NOW)
        self.assertEqual(result.sessions, 0)
        self.assertEqual(len(await self.store.lines(GUILD, running)), 2)

    async def test_another_campaign_and_another_server_are_untouched(self) -> None:
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 30)
        mine = await self.session(GUILD, self.camp.id, 200)
        theirs = await self.session(GUILD, self.other.id, 200)
        far = await self.session(OTHER_GUILD, self.far.id, 200)
        await self.job().run_once(NOW)
        self.assertEqual(await self.store.lines(GUILD, mine), [])
        self.assertEqual(len(await self.store.lines(GUILD, theirs)), 2)
        self.assertEqual(len(await self.store.lines(OTHER_GUILD, far)), 2)

    async def test_the_row_stays_with_its_number_and_the_others_do_not_shift(self) -> None:
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 30)
        first = await self.session(GUILD, self.camp.id, 300)
        second = await self.session(GUILD, self.camp.id, 200)
        third = await self.session(GUILD, self.camp.id, 5)
        before = {s.id: s.number for s in await self.store.sessions(GUILD, self.camp.id)}
        self.assertEqual(before[third], 3)
        await self.job().run_once(NOW)
        listed = {s.id: s.number for s in await self.store.sessions(GUILD, self.camp.id)}
        self.assertEqual(listed, {third: 3})  # not renumbered to 1
        gone = await self.store.session(GUILD, first)
        assert gone is not None
        self.assertEqual((gone.number, gone.lines, gone.speakers), (1, 0, ()))
        self.assertIsNotNone(gone.deleted_at)
        self.assertIsNotNone(gone.ended_at)  # its start and end stay
        second_row = await self.store.session(GUILD, second)
        assert second_row is not None
        self.assertEqual(second_row.number, 2)

    async def test_suggestions_and_mentions_of_the_lines_go_too(self) -> None:
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 30)
        old = await self.session(GUILD, self.camp.id, 200)
        mine = next(x for x in await self.store.lines(GUILD, old) if x.user_id == ME)
        made = await self.suggestions.suggest(GUILD, self.camp.id, old, ME, mine.started_ms, "Fix")
        assert not isinstance(made, str)
        await self.job().run_once(NOW)
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM transcript_suggestions")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)

    async def test_the_screen_says_so_once(self) -> None:
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 90)
        await self.session(GUILD, self.camp.id, 200)
        await self.session(GUILD, self.camp.id, 150)
        await self.job().run_once(NOW)
        self.assertEqual(
            self.posts,
            [
                (
                    self.camp.id,
                    "🗑 Deleted 2 transcripts older than 90 days. "
                    "Change this under ⚙️ Settings → 🗂️ Campaign management → "
                    "🗄️ Transcript retention.",
                )
            ],
        )
        await self.session(GUILD, self.camp.id, 140)
        await self.job().run_once(NOW)
        self.assertEqual(len(self.posts), 1)  # said once

    async def test_it_can_run_twice_and_catch_up(self) -> None:
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 30)
        for ago in (400, 300, 200):
            await self.session(GUILD, self.camp.id, ago)
        first = await self.job().run_once(NOW + 5 * DAY)  # nights were missed: all go at once
        self.assertEqual(first.sessions, 3)
        again = await self.job().run_once(NOW + 6 * DAY)
        self.assertEqual(again.sessions, 0)

    async def test_a_failing_session_rolls_back_only_itself(self) -> None:
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 30)
        bad = await self.session(GUILD, self.camp.id, 300)
        good = await self.session(GUILD, self.camp.id, 200)
        real = self.store.delete_session_transcript

        async def flaky(guild: int, camp: str, sid: str, cutoff: int, now: int) -> int:
            if sid == bad:
                raise RuntimeError("down")
            return await real(guild, camp, sid, cutoff, now)

        self.store.delete_session_transcript = flaky  # type: ignore[method-assign,assignment]
        with self.assertLogs("dmbot.transcript.keep", "ERROR"):
            result = await self.job().run_once(NOW)
        self.assertEqual((result.sessions, result.failed), (1, 1))
        self.assertEqual(len(await self.store.lines(GUILD, bad)), 2)  # untouched, tried tomorrow
        self.assertEqual(await self.store.lines(GUILD, good), [])

    async def test_a_note_that_could_not_be_posted_is_tried_again_next_time(self) -> None:
        await self.campaigns.set_transcript_keep(GUILD, self.camp.id, 30)
        await self.session(GUILD, self.camp.id, 200)
        failing = KeepJob(
            campaigns=self.campaigns,
            transcripts=self.store,
            guild_ids=self.campaigns.guilds_with_campaigns,
            post_screen=AsyncMock(return_value=False),
        )
        with self.assertLogs("dmbot.transcript.keep", "WARNING"):
            result = await failing.run_once(NOW)
        self.assertEqual(result.sessions, 1)  # the deletion itself still happened
        got = await self.campaigns.get(GUILD, self.camp.id)
        assert got is not None
        self.assertFalse(got.transcript_deleted_noted)  # not marked, so not lost
        await self.session(GUILD, self.camp.id, 190)
        await self.job().run_once(NOW)
        self.assertEqual(len(self.posts), 1)


class Words(DatabaseTest):
    def test_the_note_names_the_limit_and_the_count(self) -> None:
        self.assertEqual(
            note_text(3, 90),
            "🗑 Deleted 3 transcripts older than 90 days. "
            "Change this under ⚙️ Settings → 🗂️ Campaign management → 🗄️ Transcript retention.",
        )
        self.assertEqual(
            note_text(1, 365),
            "🗑 Deleted 1 transcript older than 1 year. "
            "Change this under ⚙️ Settings → 🗂️ Campaign management → 🗄️ Transcript retention.",
        )


_ = SimpleNamespace
