"""How much is waiting per kind on one server, and the oldest wait (#3846): the counts, the
oldest seconds, the line's words, and that nothing from another server or a clock behind leaks
in."""

from __future__ import annotations

import unittest

from dmbot.campaigns.store import CampaignStore
from dmbot.queue_depth import KINDS, Depth, depth_line, queue_depths
from dmbot.recaps.store import RecapStore
from tests import recap_fixtures as fx
from tests.pg import DatabaseTest
from tests.test_recap_jobs import Clock

GUILD, OTHER_GUILD = 1, 2
SID1, SID2 = "1" * 32, "2" * 32
IMPORT = "a" * 32


class TheLine(unittest.TestCase):
    def test_the_line_names_each_kind_with_its_count_and_oldest_wait(self) -> None:
        line = depth_line(
            (Depth("recaps", 2, 41), Depth("audio", 0, 0), Depth("imports", 1, 7)),
        )
        self.assertEqual(line, "queue: recaps 2 (oldest 41 s), audio 0, imports 1 (oldest 7 s)")

    def test_nothing_waiting_gives_zeros_in_order(self) -> None:
        line = depth_line(tuple(Depth(kind, 0, 0) for kind in KINDS))
        self.assertEqual(line, "queue: recaps 0, audio 0, imports 0")


class TheDepths(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.one = (await self.campaigns.create(GUILD, "One", fx.DM)).id
        self.clock = Clock()
        self.store = RecapStore(self.db, clock=self.clock)

    async def sql(self, guild: int, text: str, *args: object) -> None:
        async with self.db.guild(guild) as conn:
            await conn.execute(text, args)

    async def recap(self, at: int, guild: int = GUILD, campaign: str | None = None) -> None:
        self.clock.now = at
        await self.store.enqueue(guild, campaign or self.one, at, dm_user_id=fx.DM)

    async def import_row(self, sid: str, state: str, imported: int) -> None:
        await self.sql(
            GUILD,
            "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
            " VALUES (%s, %s, %s, %s)",
            sid, GUILD, self.one, imported,
        )  # fmt: skip
        # updated_at is left at its default (0), as the production insert leaves it
        await self.sql(
            GUILD,
            "INSERT INTO session_imports (guild_id, campaign_id, session_id, import_id,"
            " file_name, imported_by, imported_at, state)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            GUILD, self.one, sid, IMPORT, "first.txt", fx.DM, imported, state,
        )  # fmt: skip

    async def test_nothing_waiting_gives_zeros(self) -> None:
        depths = await queue_depths(self.db, GUILD, now=5000)
        self.assertEqual(tuple(d.kind for d in depths), KINDS)
        self.assertEqual({(d.waiting, d.oldest_s) for d in depths}, {(0, 0)})
        self.assertEqual(depth_line(depths), "queue: recaps 0, audio 0, imports 0")

    async def test_each_kind_counts_only_its_waiting_rows(self) -> None:
        await self.recap(100)  # queued: counts
        await self.recap(200)
        await self.sql(GUILD, "UPDATE recap_jobs SET state = 'failed' WHERE session_at = 200")
        await self.recap(300)  # ready, audio queued: counts for audio
        await self.sql(
            GUILD,
            "UPDATE recap_jobs SET state = 'ready', audio = 'queued', audio_asked_at = 300"
            " WHERE session_at = 300",
        )
        await self.recap(400)  # ready, audio not asked: counts for nothing
        self.clock.now = 400
        await self.store.update(GUILD, self.one, 400, state="ready")
        await self.import_row(SID1, "waiting", 500)  # counts
        await self.import_row(SID2, "done", 600)  # not waiting
        depths = {d.kind: d.waiting for d in await queue_depths(self.db, GUILD, now=1000)}
        self.assertEqual(depths, {"recaps": 1, "audio": 1, "imports": 1})

    async def test_the_oldest_wait_is_the_oldest_row(self) -> None:
        await self.recap(100)
        await self.recap(300)
        recaps = {d.kind: d for d in await queue_depths(self.db, GUILD, now=1000)}["recaps"]
        self.assertEqual((recaps.waiting, recaps.oldest_s), (2, 900))

    async def test_another_servers_rows_are_not_counted(self) -> None:
        other_campaign = (await self.campaigns.create(OTHER_GUILD, "Two", fx.DM)).id
        await self.recap(100, guild=OTHER_GUILD, campaign=other_campaign)
        ours = {d.kind: d.waiting for d in await queue_depths(self.db, GUILD, now=1000)}
        theirs = {d.kind: d.waiting for d in await queue_depths(self.db, OTHER_GUILD, now=1000)}
        self.assertEqual(ours["recaps"], 0)
        self.assertEqual(theirs["recaps"], 1)

    async def test_an_import_ages_from_imported_at(self) -> None:
        await self.import_row(SID1, "waiting", 100)
        imports = {d.kind: d for d in await queue_depths(self.db, GUILD, now=1000)}["imports"]
        self.assertEqual((imports.waiting, imports.oldest_s), (1, 900))

    async def test_audio_falls_back_to_approved_at_then_updated_at(self) -> None:
        await self.recap(300)
        await self.sql(
            GUILD,
            "UPDATE recap_jobs SET state = 'ready', audio = 'queued', audio_asked_at = 300,"
            " approved_at = 0, updated_at = 0 WHERE session_at = 300",
        )

        async def audio_oldest() -> int:
            return {d.kind: d for d in await queue_depths(self.db, GUILD, now=1000)}[
                "audio"
            ].oldest_s

        self.assertEqual(await audio_oldest(), 700)  # the asked-again time
        await self.sql(
            GUILD,
            "UPDATE recap_jobs SET audio_asked_at = 0, approved_at = 200 WHERE session_at = 300",
        )
        self.assertEqual(await audio_oldest(), 800)  # the approval time
        await self.sql(
            GUILD, "UPDATE recap_jobs SET approved_at = 0, updated_at = 100 WHERE session_at = 300"
        )
        self.assertEqual(await audio_oldest(), 900)  # the last write

    async def test_an_audio_row_with_no_known_time_counts_but_sets_no_oldest(self) -> None:
        await self.recap(300)
        await self.sql(
            GUILD,
            "UPDATE recap_jobs SET state = 'ready', audio = 'queued', audio_asked_at = 0,"
            " approved_at = 0, updated_at = 0 WHERE session_at = 300",
        )
        audio = {d.kind: d for d in await queue_depths(self.db, GUILD, now=1000)}["audio"]
        self.assertEqual((audio.waiting, audio.oldest_s), (1, 0))

    async def test_an_import_with_no_imported_at_counts_but_sets_no_oldest(self) -> None:
        await self.import_row(SID1, "waiting", 0)
        imports = {d.kind: d for d in await queue_depths(self.db, GUILD, now=1000)}["imports"]
        self.assertEqual((imports.waiting, imports.oldest_s), (1, 0))

    async def test_a_zero_time_counts_but_never_sets_the_oldest(self) -> None:
        await self.recap(0)  # created_at 0: unknown, so it counts but sets no age
        recaps = {d.kind: d for d in await queue_depths(self.db, GUILD, now=1000)}["recaps"]
        self.assertEqual((recaps.waiting, recaps.oldest_s), (1, 0))
        await self.recap(100)
        recaps = {d.kind: d for d in await queue_depths(self.db, GUILD, now=1000)}["recaps"]
        self.assertEqual((recaps.waiting, recaps.oldest_s), (2, 900))

    async def test_a_clock_behind_never_gives_a_negative_age(self) -> None:
        await self.recap(2000)
        recaps = {d.kind: d for d in await queue_depths(self.db, GUILD, now=1999)}["recaps"]
        self.assertEqual((recaps.waiting, recaps.oldest_s), (1, 0))

    async def test_the_line_holds_only_numbers_and_fixed_words(self) -> None:
        planted = (await self.campaigns.create(GUILD, "Planted Xq7 Name", fx.DM)).id
        await self.recap(100, campaign=planted)
        depths = await queue_depths(self.db, GUILD, now=1000)
        line = depth_line(depths)
        self.assertEqual(line, "queue: recaps 1 (oldest 900 s), audio 0, imports 0")
        self.assertNotIn("Xq7", line)
        self.assertNotIn(planted, line)


if __name__ == "__main__":
    unittest.main()
