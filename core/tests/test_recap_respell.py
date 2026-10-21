"""A fixed spelling reaches the campaign's recaps (#2170, part C): a waiting recap is respelled and
keeps its approval, a posted one's stored text follows, audio already made is left (and so no
longer matches), another campaign's recaps are never touched. Invented names."""

from __future__ import annotations

from typing import Any

import tests.recap_fixtures as fx
from dmbot.recaps.approval import text_hash
from dmbot.transcript.respell import recaps_note
from tests.test_recap_jobs import Base

TITLE = "Branok at the gate"
TEXT = "Branok waits. Then Branok Hall burns, and branok runs."


class Recaps(Base):
    async def make(
        self, campaign: Any, at: int, *, status: str = "draft", approved: bool = False,
        audio: str = "off", state: str = "ready",
    ) -> None:  # fmt: skip
        await self.store.enqueue(1, campaign.id, at, dm_user_id=fx.DM)
        hashed = text_hash(TITLE, TEXT)
        async with self.db.guild(1) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET state = %s, status = %s, title = %s, summary = %s,"
                " original = %s, approved_hash = %s, audio = %s, audio_text_hash = %s"
                " WHERE campaign_id = %s AND session_at = %s",
                (state, status, TITLE, TEXT, TEXT, hashed if approved else "", audio,
                 hashed if audio == "ready" else "", campaign.id, at),
            )  # fmt: skip

    async def respell(self, campaign: Any | None = None) -> Any:
        return await self.store.respell(
            1, (campaign or self.one).id, "Branok", "Brannoch", ["Branok Hall"]
        )

    async def test_a_waiting_recap_is_respelled_and_keeps_its_approval(self) -> None:
        await self.make(self.one, 100, approved=True)
        done = await self.respell()
        self.assertEqual((done.waiting, done.posted, done.approvals_kept), (1, 0, 1))
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.title, "Brannoch at the gate")
        self.assertEqual(job.summary, "Brannoch waits. Then Branok Hall burns, and Brannoch runs.")
        self.assertIn("Brannoch", job.original)
        self.assertTrue(job.approved)  # the approval follows the new text
        async with self.db.guild(1) as conn:
            cur = await conn.execute("SELECT respelled_at FROM recap_jobs")
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNotNone(row["respelled_at"])

    async def test_an_unapproved_recap_stays_unapproved(self) -> None:
        await self.make(self.one, 100)
        done = await self.respell()
        self.assertEqual(done.approvals_kept, 0)
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertFalse(job.approved)

    async def test_a_posted_recaps_stored_text_follows(self) -> None:
        await self.make(self.one, 100, status="posted")
        done = await self.respell()
        self.assertEqual((done.waiting, done.posted), (0, 1))
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.title, "Brannoch at the gate")

    async def test_audio_already_made_is_left_and_no_longer_matches(self) -> None:
        await self.make(self.one, 100, approved=True, audio="ready")
        done = await self.respell()
        self.assertEqual(done.audio_old, 1)
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.audio, "ready")
        self.assertNotEqual(job.audio_text_hash, text_hash(job.title, job.summary))

    async def test_recaps_being_written_or_dropped_are_left_alone(self) -> None:
        await self.make(self.one, 100, state="prose")
        await self.make(self.one, 200, status="dropped")
        done = await self.respell()
        self.assertEqual((done.waiting, done.posted), (0, 0))
        for at in (100, 200):
            job = await self.store.get(1, self.one.id, at)
            assert job is not None
            self.assertEqual(job.title, TITLE)

    async def test_only_the_kept_original_having_it_counts_for_nothing(self) -> None:
        await self.make(self.one, 100, approved=True, audio="ready")
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET title = 'The gate', summary = 'It waits.'")
            await conn.execute(
                "UPDATE recap_jobs SET approved_hash = %s, audio_text_hash = %s",
                (text_hash("The gate", "It waits."),) * 2,
            )
        done = await self.respell()
        self.assertEqual((done.waiting, done.posted, done.approvals_kept, done.audio_old), (0,) * 4)
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertIn("Brannoch", job.original)  # written quietly
        async with self.db.guild(1) as conn:
            cur = await conn.execute("SELECT respelled_at FROM recap_jobs")
            row = await cur.fetchone()
        assert row is not None
        self.assertIsNone(row["respelled_at"])  # the page has nothing to say

    async def test_a_recap_the_new_spelling_would_make_too_long_is_left_and_the_rest_go(
        self,
    ) -> None:
        await self.make(self.one, 100)
        await self.make(self.one, 200)
        async with self.db.guild(1) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET summary = %s WHERE session_at = 100", ("Branok " * 855,)
            )  # 5985 characters: one more letter each would pass 6000
        done = await self.respell()
        self.assertEqual((done.waiting, done.skipped), (1, 1))
        long = await self.store.get(1, self.one.id, 100)
        assert long is not None
        self.assertTrue(long.summary.startswith("Branok Branok"))

    async def test_a_name_with_a_backslash_or_wildcard_still_matches(self) -> None:
        await self.make(self.one, 100)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET title = %s", ("Ka\\zeth_1 waits",))
        done = await self.store.respell(1, self.one.id, "Ka\\zeth_1", "Kazeth", [])
        self.assertEqual(done.waiting, 1)

    async def test_another_campaigns_recaps_are_never_touched(self) -> None:
        await self.make(self.two, 100)
        await self.respell(self.one)
        job = await self.store.get(1, self.two.id, 100)
        assert job is not None
        self.assertEqual(job.title, TITLE)

    async def test_a_recap_that_does_not_say_it_is_not_counted(self) -> None:
        await self.make(self.one, 100)
        self.assertEqual(
            (await self.store.respell(1, self.one.id, "Mirelle", "Mirella", [])).waiting, 0
        )

    def test_the_note_is_plain_and_says_what_stays(self) -> None:
        from dmbot.recaps.store import RecapRespelled

        note = recaps_note("Branok", "Brannoch", RecapRespelled(2, 1, 1, 1))
        self.assertIn("2 recaps that are waiting and 1 posted recap", note)
        self.assertIn("An approval stays", note)
        self.assertIn("Posts already in Discord are not changed", note)
        self.assertIn("make it again on the website", note)

    async def test_a_curly_apostrophe_in_the_recap_is_found(self) -> None:
        await self.make(self.one, 100)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET title = %s", ("Ka’zeth waits",))
        done = await self.store.respell(1, self.one.id, "Ka'zeth", "Kazett", [])
        self.assertEqual(done.waiting, 1)
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.title, "Kazett waits")

    async def test_a_line_the_fix_would_make_say_a_secret_name_is_left_as_it_was(self) -> None:
        await self.make(self.one, 100)
        done = await self.store.respell(
            1, self.one.id, "Branok", "Brannoch", ["Branok Hall"], ["Brannoch waits"]
        )  # "Brannoch waits" is a secret name: the new spelling would complete it
        self.assertEqual((done.waiting, done.posted, done.skipped), (0, 0, 1))
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.title, TITLE)  # whole, not half respelled
        self.assertEqual(job.summary, TEXT)

    async def test_the_names_to_check_follow_the_fix(self) -> None:
        await self.make(self.one, 100)
        async with self.db.guild(1) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET to_confirm = %s", (["Branok", "Mirelle", "branok"],)
            )
        await self.respell()
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertEqual(job.to_confirm, ("Brannoch", "Mirelle"))  # joined into one, in order

    async def test_a_recap_being_posted_is_left_alone(self) -> None:
        await self.make(self.one, 100, status="posting")
        self.assertEqual((await self.respell()).waiting, 0)

    async def test_an_approved_recap_can_still_be_posted_after_the_fix(self) -> None:
        await self.make(self.one, 100, approved=True)
        await self.respell()
        job = await self.store.get(1, self.one.id, 100)
        assert job is not None
        self.assertTrue(job.approved)
        # The door's own check (SQL) agrees: the text it hashes is the text that was approved.
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT encode(sha256(convert_to(title || E'\\n' || summary, 'UTF8')), 'hex')"
                " = approved_hash AS same FROM recap_jobs"
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertTrue(row["same"])
        self.assertTrue(
            await self.store.decide(1, self.one.id, 100, "post", require_approved=True)
        )  # the approval-gated post goes through
