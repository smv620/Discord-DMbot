"""A recap written by someone at the table, kept as a session's recap (#2609): stored whole with its
label, never through the AI writer, asked about once for the right to use it, replacing one keeps
the old as an earlier version, a non-DM refused, the names checked as any recap's with a note and
never a block for a secret, postable, the next session's bridge, and in the campaign's backup.
Needs Postgres."""

from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from dmbot.bot import DMBot
from dmbot.campaigns.models import CampaignError
from dmbot.recaps import critics as cr
from dmbot.recaps import uploaded as up
from dmbot.recaps.approval import text_hash
from dmbot.recaps.store import RecapRightsSection, RecapSection
from tests import recap_fixtures as fx
from tests.test_recap_jobs import NOW, Base

TEXT = "Mira and Garrick crossed the Silver Lock at dawn.\n\nThe ferryman said nothing."


class Pure(unittest.TestCase):
    def test_the_text_is_kept_whole_and_plain(self) -> None:
        self.assertEqual(up.clean_text(" a\r\nb  \n\n\n\nc \n"), "a\nb\n\nc")
        self.assertIsNone(up.clean_text("   "))
        self.assertIsNone(up.clean_text("x" * (up.MAX_CHARS + 1)))
        self.assertEqual(len(up.clean_text("x" * up.MAX_CHARS) or ""), up.MAX_CHARS)
        self.assertIsNone(up.clean_text(5))

    def test_who_wrote_it_is_one_short_line(self) -> None:
        self.assertEqual(up.clean_author("  Mira   Voss "), "Mira Voss")
        self.assertIsNone(up.clean_author(""))
        self.assertIsNone(up.clean_author("x" * 81))

    def test_the_title_is_a_short_first_line_or_by_whom(self) -> None:
        self.assertEqual(up.title_for("# The Silver Lock\nMira went.", "Mira"), "The Silver Lock")
        self.assertEqual(
            up.title_for("Mira went to the lock and then went home.", "Mira"), "Recap by Mira"
        )

    def test_unknown_names_are_offered_and_known_and_secret_ones_are_not(self) -> None:
        text = "We met Brannoch and Garrick near Vale Keep. Silas Vane lied."
        got = up.unknown_names(text, ["Garrick", "Vale Keep"], ["Silas Vane"])
        self.assertIn("Brannoch", got)
        self.assertNotIn("Garrick", got)
        self.assertNotIn("Silas", got)
        self.assertEqual(up.secret_note(text, ["Garrick"], ["Silas Vane"]), up.NOTE_SECRET)
        self.assertEqual(up.secret_note("Garrick ran.", ["Garrick"], ["Silas Vane"]), "")


class Uploading(Base):
    async def put(self, at: int = 700, **kw: object) -> str:
        args: dict[str, object] = {
            "title": "The Silver Lock", "text": TEXT, "written_by": "Mira", "rights": True,
        }  # fmt: skip
        args.update(kw)
        return await self.store.upload(1, self.one.id, at, fx.DM, **args)  # type: ignore[arg-type]

    async def test_it_is_stored_whole_labelled_approved_and_never_queued_for_the_writer(
        self,
    ) -> None:
        long = ("Mira walked on. " * 3000)[:49_000]
        self.assertEqual(await self.put(text=long), "stored")
        job = await self.store.get(1, self.one.id, 700)
        assert job is not None
        self.assertEqual(
            (
                job.state,
                job.status,
                job.written_by,
                job.uploaded_by,
                len(job.summary),
                job.dm_user_id,
            ),
            ("ready", "draft", "Mira", fx.DM, len(long), fx.DM),
        )
        self.assertTrue(job.approved)  # like an approved recap
        self.assertEqual(job.approved_hash, text_hash(job.title, job.summary))
        self.assertEqual(await self.store.due(1), [])  # the worker has nothing to write

    async def accepted(self, at: int = 700) -> list[tuple[int, str]]:
        async with self.db.guild(1) as conn:
            cur = await conn.execute(
                "SELECT version, summary FROM recap_accepted WHERE guild_id = 1"
                " AND campaign_id = %s AND session_at = %s ORDER BY version",
                (self.one.id, at),
            )
            return [(r["version"], r["summary"]) for r in await cur.fetchall()]

    async def test_an_uploaded_recap_is_an_accepted_version_so_plotbot_and_recall_see_it(
        self,
    ) -> None:
        """#2122, "one place for both ways of approving": adding a recap approves it, so it is kept
        as the session's accepted version, and a replacement is the next version."""
        self.assertEqual(await self.put(), "stored")
        self.assertEqual(await self.accepted(), [(1, TEXT)])
        self.assertEqual(await self.put(text="Second hand.", replace_ok=True), "stored")
        self.assertEqual(await self.accepted(), [(1, TEXT), (2, "Second hand.")])
        self.assertEqual(await self.put(text="Second hand.", replace_ok=True), "stored")
        self.assertEqual(len(await self.accepted()), 2)  # the same words again are not a version

    async def test_a_session_with_no_transcript_works_and_a_non_dm_is_refused(self) -> None:
        self.assertEqual(await self.put(at=123_456), "stored")  # (no transcript rows at all)
        outcome = await self.store.upload(
            1, self.one.id, 800, fx.ALICE, title="t", text=TEXT, written_by="Mira", rights=True
        )
        self.assertEqual(outcome, "not_allowed")
        self.assertIsNone(await self.store.get(1, self.one.id, 800))

    async def test_the_right_to_use_is_asked_once_and_recorded(self) -> None:
        self.assertEqual(await self.put(rights=False), "needs_rights")
        self.assertIsNone(await self.store.get(1, self.one.id, 700))
        self.assertIsNone(await self.store.rights(1, self.one.id))
        self.assertEqual(await self.put(rights=True), "stored")
        who, when = await self.store.rights(1, self.one.id) or (0, 0)
        self.assertEqual(who, fx.DM)
        self.assertGreater(when, 0)
        self.assertEqual(await self.put(at=900, rights=False), "stored")  # not asked again
        self.assertEqual((await self.store.rights(1, self.one.id) or (0, 0))[0], fx.DM)
        other = await self.campaigns.create(1, "Two elsewhere", fx.DM)
        self.assertEqual(  # (each campaign asks for itself)
            await self.store.upload(
                1, other.id, 700, fx.DM, title="t", text=TEXT, written_by="Mira"
            ),
            "needs_rights",
        )

    async def test_bad_words_are_refused(self) -> None:
        self.assertEqual(await self.put(text="   "), "bad_text")
        self.assertEqual(await self.put(text="x" * 50_001), "bad_text")
        self.assertEqual(await self.put(written_by=" "), "bad_text")
        self.assertEqual(await self.put(title="t" * 121), "bad_text")

    async def test_a_replaced_recap_is_asked_about_and_kept_as_an_earlier_version(self) -> None:
        await self.store.enqueue(1, self.one.id, 700, dm_user_id=fx.DM)
        await self.store.update(
            1, self.one.id, 700, state="ready", title="Old", summary="Old words."
        )
        self.assertEqual(await self.put(), "needs_confirm")
        job = await self.store.get(1, self.one.id, 700)
        assert job is not None
        self.assertEqual(job.summary, "Old words.")  # asking changed nothing
        self.assertEqual(await self.put(replace_ok=True), "stored")
        job = await self.store.get(1, self.one.id, 700)
        assert job is not None
        self.assertEqual(job.summary, TEXT)
        self.assertEqual([(e["title"], e["summary"]) for e in job.earlier], [("Old", "Old words.")])
        self.assertEqual(await self.put(text="Second hand.", replace_ok=True), "stored")
        job = await self.store.get(1, self.one.id, 700)
        assert job is not None
        self.assertEqual([e["written_by"] for e in job.earlier], ["Mira", ""])  # newest first

    async def test_a_recap_a_worker_has_or_that_is_going_out_is_busy_and_a_waiting_one_is_not(
        self,
    ) -> None:
        await self.store.enqueue(1, self.one.id, 700, dm_user_id=fx.DM)
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET leased_to = %s", (int(time.time()) + 600,))
        self.assertEqual(await self.put(replace_ok=True), "busy")
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET leased_to = 0")  # waiting, not held by anyone
        self.assertEqual(await self.put(replace_ok=True), "stored")
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET status = 'posting'")
        self.assertEqual(await self.put(replace_ok=True), "busy")

    async def test_an_uploaded_recap_is_not_one_of_the_months_recaps(self) -> None:
        now = float(NOW)  # (the store's clock: the month the plain job below was made in)
        await self.put()
        await self.put(at=900)
        self.assertEqual((await self.store.used_in_month(1, self.one.id, now)).recaps, 0)
        await self.store.enqueue(1, self.one.id, 5000, dm_user_id=fx.DM)
        await self.store.update(1, self.one.id, 5000, state="ready", title="t", summary="s")
        self.assertEqual((await self.store.used_in_month(1, self.one.id, now)).recaps, 1)
        job = await self.store.get(1, self.one.id, 700)
        assert job is not None
        self.assertEqual(job.restarted_at, 0)  # (nothing was started over)

    async def test_it_can_be_posted_and_is_the_next_sessions_bridge(self) -> None:
        await self.put(at=700)
        self.assertTrue(await self.store.decide(1, self.one.id, 700, "post"))
        async with self.db.guild(1) as conn:
            await conn.execute("UPDATE recap_jobs SET status = 'posted' WHERE session_at = 700")
        before = await self.store.bridge(1, self.one.id, 900)
        assert before is not None
        self.assertEqual(
            (before.session_at, before.written_by, before.summary), (700, "Mira", TEXT)
        )

    async def test_the_names_check_asks_about_unknown_names_and_notes_a_secret_without_a_block(
        self,
    ) -> None:
        await self.put(text="Mira met Brannoch. Silas Vane was the baron all along.")
        campaign = await self.campaigns.get(1, self.one.id)
        queued = AsyncMock()
        posted = AsyncMock()
        stub = SimpleNamespace(
            recaps=self.store,
            campaigns=SimpleNamespace(get=AsyncMock(return_value=campaign)),
            recap_secret_names=AsyncMock(return_value=["Silas Vane"]),
            recap_allowed_names=AsyncMock(return_value=["Mira"]),
            _queue_recap_names=queued,
            post=posted,
            settings=SimpleNamespace(site_url=""),
        )
        self.assertTrue(await DMBot.check_uploaded_recap(stub, 1, self.one.id, 700))  # type: ignore[arg-type]
        job = await self.store.get(1, self.one.id, 700)
        assert job is not None
        self.assertEqual(job.to_confirm, ("Brannoch",))
        self.assertGreater(job.checked_at, 0)
        self.assertEqual(cr.loop_from_json(job.critics).notes, (up.NOTE_SECRET,))
        self.assertEqual((job.state, job.status), ("ready", "draft"))  # advice, never a block
        self.assertTrue(job.approved)
        queued.assert_awaited_once()
        self.assertEqual(await self.store.unchecked_uploads(1), [])
        self.assertFalse(await DMBot.check_uploaded_recap(stub, 1, self.one.id, 700))  # type: ignore[arg-type]

    async def test_a_check_that_cannot_read_the_secret_names_waits(self) -> None:
        await self.put()
        stub = SimpleNamespace(
            recaps=self.store,
            campaigns=SimpleNamespace(
                get=AsyncMock(return_value=await self.campaigns.get(1, self.one.id))
            ),
            recap_secret_names=AsyncMock(return_value=None),
        )
        self.assertFalse(await DMBot.check_uploaded_recap(stub, 1, self.one.id, 700))  # type: ignore[arg-type]
        self.assertEqual(await self.store.unchecked_uploads(1), [(self.one.id, 700)])

    async def test_it_goes_in_the_campaigns_backup_with_its_label_and_earlier_versions(
        self,
    ) -> None:
        await self.store.enqueue(1, self.one.id, 700, dm_user_id=fx.DM)
        await self.store.update(
            1, self.one.id, 700, state="ready", title="Old", summary="Old words."
        )
        await self.put(replace_ok=True)
        async with self.db.guild(1) as conn:
            rows = await RecapSection().dump(conn, 1, self.one.id)
            rights = await RecapRightsSection().dump(conn, 1, self.one.id)
        self.assertEqual(rows[0]["written_by"], "Mira")
        self.assertEqual(rows[0]["earlier"][0]["summary"], "Old words.")
        self.assertNotIn("posted_url", json.dumps(rows))
        self.assertEqual(rights[0]["user_id"], str(fx.DM))
        section = RecapSection()
        section.check(json.loads(json.dumps(rows)))  # a file made now reads back
        with self.assertRaises(CampaignError):
            section.check([{**rows[0], "earlier": [{"title": 1}]}])
        with self.assertRaises(CampaignError):
            RecapRightsSection.check([{"user_id": "x", "confirmed_at": 1}])

    async def test_a_restore_brings_the_label_and_the_confirmation_back(self) -> None:
        await self.put()
        async with self.db.guild(1) as conn:
            rows = await RecapSection().dump(conn, 1, self.one.id)
            rights = await RecapRightsSection().dump(conn, 1, self.one.id)
            await conn.execute("DELETE FROM recap_jobs WHERE campaign_id = %s", (self.one.id,))
            await conn.execute("DELETE FROM recap_upload_rights")
            await RecapSection().load(conn, 1, self.one.id, rows)
            await RecapRightsSection().load(conn, 1, self.one.id, rights)
        job = await self.store.get(1, self.one.id, 700)
        assert job is not None
        self.assertEqual((job.written_by, job.summary), ("Mira", TEXT))
        self.assertIsNotNone(await self.store.rights(1, self.one.id))


if __name__ == "__main__":
    unittest.main()
