"""The DM's yes to a counted lore PDF from the website (#4762 step 3, migration 0548): DM only, the
right-to-use box, the file the DM saw (by its ref, never its id), counted before the yes, one open
read for a campaign and five an hour like any lore upload, and the file marked as being read in the
same statement that queues it. Invented names. Needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.web import lore, lore_pdf, sessions
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW
from tests.test_web_api import THURSDAY
from tests.test_web_lore_pdf import PDF, FakeBucket
from tests.test_web_report import GUILD, Reading


class Going(Reading):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bucket = FakeBucket()

    async def counted(self, state: str = "estimated") -> str:
        """Stage a file, mark it counted, and give back the ref the page holds."""
        await lore_pdf.stage(self.web, self.session, self.cref, self.bucket, PDF, 1_000)
        await self.sql(f"UPDATE lore_pdf_staged SET state = '{state}', pages = 9, pictures = 2")
        return str((await lore_pdf.read(self.web, self.session, self.cref))["ref"])

    def body(self, ref: str, **over: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"ref": ref, "choice": "world", "speaker": "", "rights": True}
        out.update(over)
        return out

    async def go(self, ref: str, who: Any = None, **over: Any) -> dict[str, Any]:
        return await lore_pdf.go(self.web, who or self.session, self.cref, self.body(ref, **over))

    async def turned_away(self, ref: str, who: Any = None, **over: Any) -> str:
        with self.assertRaises(RecapError) as err:
            await self.go(ref, who, **over)
        return err.exception.code

    async def uploads(self) -> list[Any]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT state, source, text, link, rights_by FROM lore_uploads ORDER BY seq"
            )
            return list(await cur.fetchall())

    async def test_the_yes_queues_a_pdf_read_and_marks_the_file_as_being_read(self) -> None:
        ref = await self.counted()
        got = await self.go(ref, speaker=self.aldric)
        self.assertEqual(got["upload"]["state"], "waiting")
        [row] = await self.uploads()
        self.assertEqual((row["source"], row["text"], row["link"]), ("pdf", "", ""))
        self.assertEqual(
            (await lore_pdf.read(self.web, self.session, self.cref))["state"], "reading"
        )
        # a second press finds the file taken and the campaign busy
        self.assertEqual(await self.turned_away(ref), "busy")

    async def test_nothing_is_queued_without_the_right_to_use_box(self) -> None:
        ref = await self.counted()
        self.assertEqual(await self.turned_away(ref, rights=False), "needs_confirm")
        self.assertEqual(await self.uploads(), [])
        self.assertEqual(
            (await lore_pdf.read(self.web, self.session, self.cref))["state"], "estimated"
        )

    async def test_the_file_must_be_counted_and_the_one_the_dm_saw(self) -> None:
        ref = await self.counted("staged")
        self.assertEqual(await self.turned_away(ref), "not_counted")
        self.assertEqual(await self.turned_away("0" * 32), "not_found")
        self.assertEqual(await self.turned_away("not a ref"), "bad_text")
        await self.sql("UPDATE lore_pdf_staged SET state = 'estimated'")
        self.assertEqual((await self.go(ref))["upload"]["state"], "waiting")

    async def test_a_file_replaced_after_the_count_is_not_read_on_the_old_yes(self) -> None:
        old = await self.counted()
        await lore_pdf.stage(self.web, self.session, self.cref, self.bucket, PDF + b"y", 2_000)
        await self.sql("UPDATE lore_pdf_staged SET state = 'estimated'")
        self.assertEqual(await self.turned_away(old), "not_found")
        self.assertEqual(await self.uploads(), [])

    async def test_only_a_dm_of_the_campaign_may_say_yes(self) -> None:
        ref = await self.counted()
        who: Any = BOB
        bob = await sessions.find(
            self.web, await sessions.sign_in(self.web, who, [THURSDAY], now=NOW, days=30), now=NOW
        )
        assert bob is not None
        self.assertEqual(await self.turned_away(ref, who=bob), "not_allowed")
        self.assertEqual(await self.uploads(), [])

    async def test_nothing_staged_is_not_found(self) -> None:
        self.assertEqual(await self.turned_away("0" * 32), "not_found")

    async def test_one_open_read_and_five_an_hour_like_any_lore_upload(self) -> None:
        ref = await self.counted()
        await lore.add(
            self.web, self.session, self.cref,
            {"source": "paste", "text": "Mira.", "choice": "world", "speaker": "", "rights": True},
        )  # fmt: skip
        self.assertEqual(await self.turned_away(ref), "busy")  # (shared with pasted lore)
        for _ in range(4):
            await self.sql("UPDATE lore_uploads SET state = 'done'")
            await self.go(await self.counted())
        await self.sql("UPDATE lore_uploads SET state = 'done'")
        self.assertEqual(await self.turned_away(await self.counted()), "too_soon")
        self.assertEqual(len(await self.uploads()), 5)
